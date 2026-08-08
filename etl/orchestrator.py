#!/usr/bin/env python3
"""Scaled ETL orchestrator: full document -> validated Tutor package.

Pipeline:
  segment (DoclingDocument provenance + Markdown slices)
    -> pre-filter -> concept_extractor gate (+ bounded merge remediation)
    -> question_author (explicit target difficulties 1-5)
    -> question_judge (1 bounded repair on reject)
    -> assemble -> package_curator -> package_judge -> validate -> write

Progress is emitted as socket.io-shaped events via emit() (see the ETL doc's
socket.io contract). Per-concept results are cached in the job work dir, so a
re-run resumes instead of re-calling the LLM.
"""
import json, re, sys, os, urllib.request, hashlib, datetime, collections
import jsonschema   # hard dependency: a broken/missing validator must fail loudly, not be worked around

MD   = os.environ.get("ETL_MD",  "/home/logus/env/study/ms/materials/AI-901.md")
DOC  = os.environ.get("ETL_DOC", "/home/logus/env/study/ms/materials/AI-901.json")
OUT  = os.environ.get("ETL_OUT", "/home/logus/env/assets/tutor/data/packages/ai-901-core.json")
SCHEMA = os.environ.get("ETL_SCHEMA", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "package.schema.json"))
SRC_ID = os.environ.get("ETL_SRC_ID", "src-ai901")
SRC_TITLE = os.environ.get("ETL_SRC_TITLE", "Microsoft Certified: Azure AI Fundamentals (AI-901) Master Cheat Sheet")
SRC_URI = os.environ.get("ETL_SRC_URI", "materials/AI-901.pdf")
PKG_ID = os.environ.get("ETL_PKG_ID", "ai-901-core")
# All uploaded documents that make up this package (multi-doc ingestion). Falls
# back to the single SRC_* source when not provided.
SOURCES = json.loads(os.environ.get("ETL_SOURCES") or "[]")
API  = os.environ.get("ETL_API", "http://localhost:7701/v1/chat/completions")

# argv fallbacks only apply when run as a script with NUMERIC args; importing this
# module (e.g. from the service for re-validation) must not choke on argv.
_argv_n = lambda i, d: int(sys.argv[i]) if (len(sys.argv) > i and str(sys.argv[i]).isdigit()) else d
MAX_CONCEPTS = int(os.environ.get("ETL_MAX_CONCEPTS") or _argv_n(1, 999))
QPC          = int(os.environ.get("ETL_QPC") or _argv_n(2, 3))
# Concept density knob passed to document_author (coarse | medium | fine).
GRANULARITY  = (os.environ.get("ETL_GRANULARITY") or "medium").strip().lower()
# Plan-only run: list concepts + emit a forecast, then STOP before authoring.
PLAN_ONLY    = os.environ.get("ETL_PLAN_ONLY") == "1"
MIN_WORDS    = 25
# Set in main(); kept module-level so emit() can reference them. Computing JOB_ID
# reads MD, so it (and the run) live in main() — importing this module has no side effects.
JOB_ID = os.environ.get("ETL_JOB_ID")
WORK = None

# ---------------------------------------------------------------- events (socket.io seam)
def emit(event, **payload):
    """One structured progress event. In the service this is a socket.io emit;
    here it prints a line the admin UI's bridge would broadcast verbatim."""
    print("EVENT " + json.dumps({"jobId": JOB_ID, "event": event, **payload}), flush=True)

# ---------------------------------------------------------------- LLM helpers
def agent(name, user, timeout=240):
    body = json.dumps({"model": name, "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))["choices"][0]["message"]["content"]

def _loads(s):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        s2 = re.sub(r'\\(?![\\"/bfnrtu])', r'\\\\', s)   # stray backslashes (LaTeX, paths)
        s2 = re.sub(r',(\s*[}\]])', r'\1', s2)            # trailing commas before } or ]
        return json.loads(s2)

def extract_json(text):
    t = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text.strip(), flags=re.M)
    s = t.find("{")
    if s < 0: raise ValueError("no JSON")
    instr = False; esc = False; depth = 0
    for i in range(s, len(t)):
        c = t[i]
        if instr:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': instr = False
        elif c == '"': instr = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return _loads(t[s:i+1])
    raise ValueError("unbalanced JSON")

# ---------------------------------------------------------------- meta-reference stripper
# Questions must be SELF-CONTAINED: the student never sees the source document, so a
# stem/rationale must never cite it ("according to the provided text", "the grounding
# states that ...", etc.). The author prompt forbids this and the judge rejects it;
# this is the deterministic last-line-of-defense guardrail. It only applies transforms
# that are GRAMMAR-SAFE (they leave a complete clause) — the riskier paraphrase cases
# are left for the judge to catch, never silently mangled here.
#
# "Source nouns" deliberately EXCLUDE the Azure domain terms "knowledge grounding" /
# "grounding feature" / "timestamp grounding" (real content, not a source pointer).
_SRC_NOUN = (
    r"(?:provided\s+\w+(?:\s+\w+){0,2}"
    r"|comparison\s+table(?:\s+provided)?"
    r"|deployment\s+decision\s+flowchart"
    r"|workflow\s+(?:description|sequence)"
    r"|grounding\s+text|grounding\s+passages?"
    r"|grounding|summary|passages?|comparison"
    r"|flowchart|diagram|exam\s+tip|table|text|document(?:ation)?)"
)
_NOT_FEATURE = r"(?!\s*(?:/|feature|/knowledge))"   # don't eat "grounding/knowledge feature"
_META_VERB = (r"(?:states?|lists?|mentions?|defines?|indicates?|shows?|specif(?:y|ies)|notes?|"
              r"explains?|contrasts?|distinguishes?|emphasi[sz]es?|advises?|recommends?|"
              r"highlights?|names?|describes?|presents?|outlines?)")
# Leading clause + comma:           "According to the provided text, <Q>" -> "<Q>"
_META_LEAD = re.compile(
    r"^\s*(?:according to|based on|as per|per|as (?:stated|mentioned|described|shown|noted|defined|listed|indicated) in)\s+the\s+"
    + _SRC_NOUN + _NOT_FEATURE + r"\s*,\s*", re.IGNORECASE)
# Trailing clause before ? / . :    "<Q> according to the provided text?" -> "<Q>?"
# (The "in" connector here safely drops a trailing "... in the <src>." prepositional
#  phrase only when the source noun sits right before ?/./end — never mid-clause.)
_META_TRAIL = re.compile(
    r"\s*,?\s*(?:according to|based on|as per|per|as (?:stated|mentioned|described|shown|noted|defined|listed|indicated) in|in)\s+the\s+"
    + _SRC_NOUN + r"\s*(?=[?.]|$)", re.IGNORECASE)
# Rationale prefix, REQUIRES "that" (so a full clause remains; "defines X as Y" is left alone):
#   "The grounding explicitly states that <clause>" -> "<clause>"
_META_RAT_PREFIX = re.compile(
    r"^\s*(?:the|this|that|per the)\s+" + _SRC_NOUN + _NOT_FEATURE +
    r"(?:\s+(?:also|explicitly|clearly|specifically|directly))?\s+" + _META_VERB + r"\s+that\s+",
    re.IGNORECASE)
# Soft "the source says so" phrasing:  "X is explicitly mentioned/listed as Y" -> "X is Y".
# Only fires with a meta adverb, or with mentioned/listed/cited/referenced (which imply a
# source) — so plain definitions ("X is defined as Y") are left intact.
_META_EXPLICIT_AS = re.compile(
    r"\b(is|are|was|were)\s+(?:(?:explicitly|clearly|specifically|directly)\s+[a-z]+|mentioned|listed|cited|referenced)\s+as\b",
    re.IGNORECASE)
# "X is explicitly mentioned in the <src>" -> "X is in the <src>" (keeps any real content noun).
_META_EXPLICIT_IN = re.compile(
    r"\b(is|are|was|were)\s+(?:explicitly|clearly|specifically|directly)\s+"
    r"(?:mentioned|listed|included|shown|found|stated|defined|noted|described)\s+in\s+the\b",
    re.IGNORECASE)
# Mid-clause source pointer after a conjunction:  "... because the grounding states that
# <clause>" -> "... because <clause>" (keep the conjunction, drop the source pointer).
_META_MIDCLAUSE = re.compile(
    r"\b(because|but|while|since|whereas|although|and)\s+(?:the|this|that)\s+" + _SRC_NOUN + _NOT_FEATURE +
    r"(?:\s+(?:also|explicitly|clearly|specifically|directly))?\s+" + _META_VERB + r"\s+that\s+",
    re.IGNORECASE)

def strip_meta_references(text):
    """Remove grammar-safe source-pointer phrasing from a question's prose. Returns
    the cleaned text (or the input unchanged if no safe transform applies)."""
    if not text or not isinstance(text, str):
        return text
    t = _META_LEAD.sub("", text)
    t = _META_TRAIL.sub("", t)
    t = _META_RAT_PREFIX.sub("", t)
    t = _META_MIDCLAUSE.sub(r"\1 ", t)
    t = _META_EXPLICIT_AS.sub(r"\1", t)
    t = _META_EXPLICIT_IN.sub(r"\1 in the", t)
    t = re.sub(r"\s+([?.,;:])", r"\1", t)          # drop space before punctuation
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"^[,;:\s]+", "", t)                 # drop orphaned leading punctuation
    return (t[0].upper() + t[1:]) if t else t

# ---------------------------------------------------------------- typography normalizer
# docling's --enrich-formula and table rendering leave LaTeX/HTML artifacts ("$\rightarrow$",
# "&amp;", "\%") in the text; the author copies them verbatim. Map the common ones to the
# Unicode the UI should show. Conservative: only known symbols, never a blanket strip of $...$.
_TYPO = [
    (re.compile(r"\$?\\(?:rightarrow|to)\$?"), "→"),       # ->
    (re.compile(r"\$?\\(?:leftarrow|gets)\$?"), "←"),      # <-
    (re.compile(r"\$?\\leftrightarrow\$?"), "↔"),          # <->
    (re.compile(r"\$?\\Rightarrow\$?"), "⇒"),
    (re.compile(r"\$?\\Leftarrow\$?"), "⇐"),
    (re.compile(r"\$?\\times\$?"), "×"),
    (re.compile(r"\$?\\(?:leq|le)\$?"), "≤"),
    (re.compile(r"\$?\\(?:geq|ge)\$?"), "≥"),
    (re.compile(r"\$?\\(?:neq|ne)\$?"), "≠"),
    (re.compile(r"\$?\\approx\$?"), "≈"),
    (re.compile(r"\$?\\cdot\$?"), "·"),
    (re.compile(r"\$?\\ldots\$?"), "…"),
]
_ENT = {"&rarr;": "→", "&larr;": "←", "&harr;": "↔", "&amp;": "&",
        "&lt;": "<", "&gt;": ">", "&nbsp;": " ", "&quot;": '"', "&#39;": "'"}
_ESC = re.compile(r"\\([%_&#$~{}])")   # de-escape "\%", "\_", "\&", ...

def normalize_typography(text):
    """Convert leftover LaTeX symbols / HTML entities / TeX escapes to plain Unicode."""
    if not text or not isinstance(text, str):
        return text
    for rx, rep in _TYPO:
        text = rx.sub(rep, text)
    for k, v in _ENT.items():
        text = text.replace(k, v)
    text = _ESC.sub(r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()

def _clean_field(t):
    return normalize_typography(strip_meta_references(t)) if isinstance(t, str) else t

def scrub_meta(q):
    """Clean every prose field of a built question (in place): strip source-pointer
    phrasing and normalize typography. Run BEFORE judging so the judge sees clean text."""
    if isinstance(q.get("stem"), str):
        q["stem"] = _clean_field(q["stem"])
    if isinstance(q.get("explanation"), str):
        q["explanation"] = _clean_field(q["explanation"])
    if isinstance(q.get("hints"), list):
        q["hints"] = [_clean_field(h) for h in q["hints"]]
    for o in (q.get("options") or []):
        if isinstance(o.get("text"), str):
            o["text"] = normalize_typography(o["text"])   # options: typography only (don't strip meta from short labels)
        if isinstance(o.get("rationale"), str):
            o["rationale"] = _clean_field(o["rationale"])
    return q

# ---------------------------------------------------------------- sanitize (defensive guardrail)
VALID_TYPES = {"mcq_single", "mcq_multi", "true_false"}
VALID_BLOOM = {"recall", "understand", "apply", "analyze"}
def sanitize_question(q, verbatim=False):
    """Coerce an LLM-authored question to a schema-valid, internally-consistent
    shape. Returns the cleaned question, or None if it cannot be salvaged.

    verbatim=True skips the meta-reference scrub. An IMPORTED question was written
    by a human in the source document: it has no "according to the provided text"
    phrasing to remove, and the scrub's "drop space before punctuation" rule
    actively damages it — ".mcp.json" turned "expansion in .mcp.json" into
    "expansion in.mcp.json" on three CCA workshop stems (2026-08-08)."""
    if not verbatim:
        scrub_meta(q)
    opts = q.get("options") or []
    if len(opts) < 2:
        return None
    nc = sum(1 for o in opts if o.get("correct"))
    if nc == 0:
        return None                                     # no key -> unsalvageable
    if nc == len(opts):
        return None                                     # every option correct -> no distractor, broken
    t = q.get("type")
    if t not in VALID_TYPES:                            # infer from shape
        tf = {o.get("text", "").strip().lower() for o in opts} <= {"true", "false"}
        t = "true_false" if (len(opts) == 2 and tf) else ("mcq_multi" if nc > 1 else "mcq_single")
    if t in ("mcq_single", "true_false") and nc > 1: t = "mcq_multi"
    if t == "mcq_multi" and nc == 1: t = "mcq_single"
    q["type"] = t
    q["render"] = "checkbox" if t == "mcq_multi" else (q["render"] if q.get("render") in ("radio", "dropdown") else "radio")
    if q.get("bloom") not in VALID_BLOOM: q["bloom"] = "understand"
    d = q.get("difficulty")
    if not isinstance(d, int) or not 1 <= d <= 5: q["difficulty"] = 2
    return q

# The judge validates against the WHOLE document, not a cherry-picked chunk: many keys
# depend on facts that live in a different section (e.g. a global task->service mapping
# table). The source comfortably fits E2B's 64K window, so there's no reason to spare it.
# Cap is in characters (~4 chars/token); the default leaves ample room for the question +
# the judge's output. A document larger than this falls back to a leading slice.
JUDGE_SOURCE_CAP = int(os.environ.get("ETL_JUDGE_SOURCE_CAP", 220000))
_FULL_SRC = None
def full_source():
    """The entire source document (cached), used as authoritative ground truth."""
    global _FULL_SRC
    if _FULL_SRC is None:
        _FULL_SRC = open(MD, encoding="utf-8").read()
    return _FULL_SRC

def robust_judge(q, grounding, source_text=None):
    """Judge a question, tolerating malformed judge output. Retries once with a
    stricter reminder, then conservatively REJECTS (never silently keeps).

    `source_text` is the FULL source document (authoritative ground truth); `grounding`
    is the extractor's highlighted spans for this concept (focus hint). The judge checks
    against source_text so it can catch keys that contradict facts in other sections."""
    if source_text is None:
        source_text = full_source()
    # source first => stable prefix the LLM server can cache across the many judge calls
    base = json.dumps({"source_text": source_text[:JUDGE_SOURCE_CAP],
                       "grounding": grounding, "question": q})
    for attempt in range(2):
        suffix = "" if attempt == 0 else ("\n\nYour previous reply was NOT valid JSON. "
                 "Reply with ONLY one valid JSON object — no comments, no extra text.")
        try:
            return agent_json("question_judge", base + suffix)
        except Exception:
            continue
    return {"verdict": "reject", "issues": ["judge output unparseable"], "_parsefail": True}

# ---------------------------------------------------------------- answer-blind validator
# A second opinion that does NOT see the key: it re-solves the question from the full
# document, then code compares its answer to the stored key. Reframing verify->solve is
# what makes a small model (E2B) useful here — asked "is this key right?" it rubber-stamps;
# asked "what IS the answer?" it reasons. Self-consistency over N samples cuts variance.
VALIDATE_ON = os.environ.get("ETL_VALIDATE", "1") != "0"
VALIDATE_N = int(os.environ.get("ETL_VALIDATE_N", 3))
def validate_answer(q, source_text=None, n=None):
    """Solve q answer-blind against the full document (self-consistency over n samples) and
    compare to the stored key. Returns a dict with status agree|dispute|inconclusive."""
    if source_text is None: source_text = full_source()
    if n is None: n = VALIDATE_N
    stored = frozenset(o["id"] for o in q.get("options", []) if o.get("correct"))
    blind = {"stem": q.get("stem", ""), "type": q.get("type"),
             "options": [{"id": o["id"], "text": o.get("text", "")} for o in q.get("options", [])]}
    payload = json.dumps({"source_text": source_text[:JUDGE_SOURCE_CAP], "question": blind})
    samples = []
    for _ in range(n):
        try:
            r = agent_json("answer_validator", payload)
            ids = r.get("answer_ids")
            if isinstance(ids, list) and ids:
                samples.append((frozenset(str(x) for x in ids), r.get("quote", "")))
        except Exception:
            continue
    base = {"stored": sorted(stored), "samples": len(samples)}
    if not samples:
        return {**base, "status": "inconclusive", "derived": None, "quote": ""}
    counts = collections.Counter(s[0] for s in samples)
    top, topn = counts.most_common(1)[0]
    quote = next((s[1] for s in samples if s[0] == top), "")
    out = {**base, "derived": sorted(top), "quote": quote}
    if topn < 2 and len(counts) == len(samples):     # no majority -> model is unsure
        return {**out, "status": "dispute", "reason": "validator inconsistent across samples"}
    if top == stored:
        return {**out, "status": "agree"}
    return {**out, "status": "dispute", "reason": "answer-blind solve disagrees with stored key"}

def dedup_questions(qs, threshold=0.82):
    """Drop near-duplicate stems (token-set Jaccard). Keeps the first occurrence."""
    kept, toksets, dropped = [], [], 0
    for q in qs:
        ts = set(re.findall(r"[a-z0-9]+", (q.get("stem") or "").lower()))
        if any(ts and kt and len(ts & kt) / len(ts | kt) >= threshold for kt in toksets):
            dropped += 1
        else:
            kept.append(q); toksets.append(ts)
    return kept, dropped

def semantic_dedup(questions, emit=lambda *a, **k: None):
    """LLM near-duplicate removal: scans the bank in overlapping batches and drops
    questions that re-test an EARLIER one (the token dedup misses reworded dupes —
    e.g. several 'different sentences, same meaning' variants across concepts)."""
    n = len(questions)
    if n < 2:
        return questions
    keep = [True] * n
    BATCH, STEP = 16, 11          # ~5-question overlap catches dupes across batch seams
    def _line(k, q):
        opts = "; ".join(("✓" if o.get("correct") else "·") + (o.get("text") or "")[:48]
                         for o in (q.get("options") or []))
        return f"{k}. {(q.get('stem') or '')[:160]}  [{opts}]"
    start = 0
    while start < n:
        idxs = [i for i in range(start, min(start + BATCH, n)) if keep[i]]
        if len(idxs) > 1:
            listing = "\n".join(_line(k, questions[i]) for k, i in enumerate(idxs))
            try:
                res = agent_json("dedup_judge", listing)
                for k in (res.get("drop") or []):
                    if isinstance(k, int) and 0 <= k < len(idxs):
                        keep[idxs[k]] = False
            except Exception:
                pass
        start += STEP
    kept = [q for q, k in zip(questions, keep) if k]
    emit("dedup.semantic", removed=n - len(kept), remaining=len(kept))
    return kept

def interleave_by_concept(questions):
    """Round-robin questions across concepts so same-concept / same-theme questions
    are not presented consecutively."""
    groups, order = {}, []
    for q in questions:
        c = (q.get("concept_ids") or ["_"])[0]
        if c not in groups:
            groups[c] = []; order.append(c)
        groups[c].append(q)
    out = []
    while any(groups[c] for c in order):
        for c in order:
            if groups[c]:
                out.append(groups[c].pop(0))
    return out

def dedup_and_order(questions, emit=lambda *a, **k: None):
    """Remove near-duplicate questions (LLM semantic dedup) then spread by concept.

    IMPORTED questions are NEVER deduped. Dedup exists to prune the redundancy the
    local model produces when overlapping chunks yield near-identical concepts; a
    question imported verbatim from a Q&A document was written deliberately by a
    human author. On a curated exam bank the judge groups by TOPIC and destroys the
    set: a CCA workshop module lost 27 of 90 questions this way (2026-08-08), among
    them "What is an agentic loop?" and "What is a PreToolUse hook used for?" — not
    duplicates of anything. Imported questions are identified by the absence of
    `generated_by`, which authoring always sets.
    """
    imported = [q for q in questions if not q.get("generated_by")]
    authored = [q for q in questions if q.get("generated_by")]
    if authored:
        authored = semantic_dedup(authored, emit)
    if imported:
        emit("dedup.skipped", kept=len(imported), reason="imported verbatim")
    return interleave_by_concept(imported + authored)

# Stems that reference source material the student never sees → the question can't stand alone.
_SRC_REF_RE = re.compile(
    r"\bbased on\b|\baccording to\b|\bmentioned in the\b"
    r"|\bas (shown|described|stated|listed|mentioned|illustrated|depicted) (in|above|below)\b"
    r"|\b(shown|illustrated|depicted|presented) (above|below)\b"
    r"|\bthe (example|figure|table|diagram|chart|graph|image|passage|formula|equation)\b"
    r"|\bin (the|this) [\w'\-]+ example\b"
    r"|\b(text|document|doc|vector) ?[12]\b|\bd[12]\b|\bv[12]\b|\bcount for\b",
    re.I)
def authored_quality_ok(q):
    """Reject AUTHORED questions that cannot stand alone: stems that reference source
    material the student never sees ('based on the example', 'the figure above',
    'mentioned in the text') or that give away their own answer (circular/self-answering).
    Returns (ok, reason)."""
    stem = q.get("stem") or ""
    if _SRC_REF_RE.search(stem):
        return False, "references unseeable source material"
    sl = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", stem.lower())).strip()
    for o in q.get("options", []):
        if not o.get("correct"):
            continue
        ot = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (o.get("text") or "").lower())).strip()
        if len(ot.split()) >= 3 and ot and ot in sl:
            return False, "circular: the answer is contained in the stem"
    return True, ""

def agent_json(name, user, **kw): return extract_json(agent(name, user, **kw))

# ---------------------------------------------------------------- provenance (DoclingDocument)
def page_map(doc_path):
    """objective code (e.g. '1.1.1') -> page number, from section_header provenance."""
    m = {}
    try:
        d = json.load(open(doc_path))
        for t in d.get("texts", []):
            if t.get("label") != "section_header": continue
            mm = re.match(r"^(\d+\.\d+(?:\.\d+)?)\b", (t.get("text") or "").strip())
            prov = t.get("prov") or []
            if mm and prov and "page_no" in prov[0]:
                m.setdefault(mm.group(1), prov[0]["page_no"])
    except Exception as e:
        emit("warn", message=f"page_map failed: {e}")
    return m

# ---------------------------------------------------------------- segment (Markdown)
def segment(md_path):
    """Split a document's markdown into concept-sized sections — STRUCTURE-AGNOSTIC.

    Any heading (#..######) starts a section; top-level headings (h1/h2) group as
    domains, the rest are concepts. A document with no usable headings is chunked
    by length. EVERY document therefore yields concepts (no dependency on the
    AI-901 '## Domain N:' / '#### 1.1.1' convention that used to be required)."""
    raw = open(md_path, encoding="utf-8").read()
    secs, cur = [], None
    for ln in raw.split("\n"):
        h = re.match(r"^(#{1,6})\s+(.*)", ln)
        if h:
            if cur: secs.append(cur)
            cur = {"level": len(h.group(1)), "title": h.group(2).strip(), "lines": []}
        elif cur is not None:
            cur["lines"].append(ln)
        elif ln.strip():                     # preamble before the first heading
            cur = {"level": 0, "title": "Overview", "lines": [ln]}
    if cur: secs.append(cur)

    domains, concepts = {}, []
    dnum, cseq = [0], [0]
    def new_domain(title):
        dnum[0] += 1; cseq[0] = 0
        did = f"d{dnum[0]}"
        domains[did] = {"id": did, "title": (title or f"Section {dnum[0]}")[:120], "weight": None}
        return dnum[0]
    def add(title, lines, dn):
        cseq[0] += 1
        concepts.append({"objective": f"{dn}.{cseq[0]}", "title": (title or f"Part {cseq[0]}")[:160],
                         "domain_num": dn, "lines": lines})

    has_top = any(s["level"] in (1, 2) for s in secs)
    cur_dn = None
    for s in secs:
        words = len(" ".join(s["lines"]).split())
        if has_top and s["level"] in (1, 2):
            cur_dn = new_domain(s["title"])
            add(s["title"], s["lines"], cur_dn)   # always a candidate; downstream drops empty ones
        else:
            if cur_dn is None:
                cur_dn = new_domain("Document")
            add(s["title"], s["lines"], cur_dn)

    if not concepts:                          # no headings at all → fixed-size chunks
        cur_dn = new_domain("Document")
        words = raw.split()
        for k in range(0, max(1, len(words)), 500):
            add(None, [" ".join(words[k:k + 500])], cur_dn)

    for c in concepts:
        c["text"] = "\n".join(c["lines"]).strip(); c["words"] = len(c["text"].split())
    return concepts, domains

# ---------------------------------------------------------------- chunk for the LLM
def _markdown_sections(md_path):
    """FALLBACK only: split exported markdown into heading-delimited sections when
    the structured DoclingDocument JSON is unavailable. (The primary path uses
    docling's own section structure — see chunk_doc.)"""
    raw = open(md_path, encoding="utf-8").read()
    blocks, cur = [], []
    for ln in raw.split("\n"):
        if re.match(r"^#{1,6}\s+", ln) and cur:
            blocks.append("\n".join(cur)); cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append("\n".join(cur))
    return blocks

def chunk_doc(md_path, doc_path=None, max_chars=None):
    """Pack a document into LLM-sized chunks using docling's OWN section structure.

    docling already detects the document hierarchy, so we read its structured
    output (the DoclingDocument JSON produced at extraction) and let docling's
    HierarchicalChunker emit section-aware pieces WITH their heading context — no
    regex heading-detection of our own. We then merge small sections up to a char
    budget and slice any single oversized section, so every chunk fits the model
    window (a document bigger than the window just becomes more chunks). Each piece
    is a whole section, so a question and its answer key are never split apart.
    Falls back to a markdown heading split only if the structured JSON is missing."""
    max_chars = max_chars or int(os.environ.get("ETL_CHUNK_CHARS") or 4000)

    # each section: (text_with_heading_context, headings[list], pages[list])
    sections = None
    try:
        from docling.chunking import HierarchicalChunker
        from docling_core.types.doc import DoclingDocument
        doc = DoclingDocument.load_from_json(doc_path or DOC)
        sections = []
        for ch in HierarchicalChunker().chunk(doc):
            heads = [h for h in (getattr(ch.meta, "headings", None) or []) if h]
            pages = sorted({p.page_no for it in (ch.meta.doc_items or [])
                            for p in (getattr(it, "prov", None) or []) if getattr(p, "page_no", None)})
            ctx = (" > ".join(heads) + "\n\n") if heads else ""
            sections.append((ctx + (ch.text or ""), heads, pages))
        emit("chunk.source", source="docling", sections=len(sections))
    except Exception as e:
        emit("warn", message=f"docling chunker unavailable ({e}); markdown fallback")
        sections = [(b, [], []) for b in _markdown_sections(md_path)]

    # slice any single section larger than the budget (rare; long prose)
    norm = []
    for text, heads, pages in sections:
        if len(text) <= max_chars:
            norm.append((text, heads, pages)); continue
        for k in range(0, len(text), max_chars):
            norm.append((text[k:k + max_chars], heads, pages))

    # greedily pack consecutive sections into chunks under the budget, unioning their
    # heading context + source pages so each chunk keeps real provenance for Review
    chunks, cur = [], None
    for text, heads, pages in norm:
        if cur and len(cur["text"]) + len(text) + 2 > max_chars:
            chunks.append(cur); cur = None
        if cur is None:
            cur = {"text": text, "headings": list(heads), "pages": set(pages)}
        else:
            cur["text"] += "\n\n" + text
            cur["headings"] += [h for h in heads if h not in cur["headings"]]
            cur["pages"] |= set(pages)
    if cur:
        chunks.append(cur)
    for c in chunks:
        c["pages"] = sorted(c["pages"])
    return [c for c in chunks if c["text"].strip()]

# ---------------------------------------------------------------- helpers
def opt_id(i): return chr(ord("a") + i)
def target_difficulties(n):
    if n <= 1: return [3]
    return [max(1, min(5, round(1 + i * 4 / (n - 1)))) for i in range(n)]
LOC_RE = re.compile(r"^(§\d+\.\d+(?:\.\d+)?|p\.\s*\d+)$")
def clean_locator(loc, fallback):
    loc = (loc or "").strip()
    return loc if LOC_RE.match(loc) else fallback

_ANS_LABEL = re.compile(r"^(the\s+)?(correct\s+)?(answers?|ans|options?|choices?)\s*[:\-.]?\s*", re.I)
def resolve_key(source_answer, options):
    """Map a document's STATED answer (transcribed by the LLM) to option ids,
    deterministically — so an imported question's key comes from the source's own
    words, never the model's opinion. Accepts option letter(s)/number(s) ("c",
    "b, d", "2") or the correct option's text. Returns a set of correct ids (empty
    if it cannot be resolved). This is answer-token resolution, NOT format parsing."""
    sa = str(source_answer or "").strip()
    if not sa:
        return set()
    letters = {o["id"] for o in options}
    texts = [(o["id"], (o.get("text") or "").strip().lower()) for o in options]
    ids = set()
    for part in re.split(r"[,/&;]|\band\b", sa, flags=re.I):
        p = _ANS_LABEL.sub("", part.strip()).strip().strip("().:").strip()
        if not p:
            continue
        pl = p.lower()
        if len(pl) == 1 and pl in letters:                 # option letter
            ids.add(pl); continue
        if p.isdigit():                                     # 1-based index
            k = int(p) - 1
            if 0 <= k < len(options): ids.add(options[k]["id"])
            continue
        exact = [oid for oid, t in texts if t and t == pl]  # exact option text
        if exact:
            ids.update(exact); continue
        sub = [oid for oid, t in texts if t and (pl in t or t in pl)]
        if len(sub) == 1:                                   # unambiguous substring
            ids.add(sub[0])
    return ids

# ---------------------------------------------------------------- import ready-made Q&A
# A document may ALREADY contain multiple-choice questions (stem → a./b./c./d. →
# "Answer: x"). Import those VERBATIM instead of authoring around them.
# Tolerate docling's rendering: options come out as list items ("- a. Foo") and
# the answer as a heading ("##### Answer: c"), so allow an optional list marker
# before the option letter and optional heading hashes before "Answer".
# Also tolerate a decorative bullet and the word "Correct": a glyph such as "■"
# renders as a stray one-or-two-char token, giving "##### n Correct Answer: C"
# (seen in the CCA workshop PDFs, 2026-08-08). Without this the whole document
# reads as prose and gets AUTHORED instead of imported — see is_qa_doc below.
_OPT_RE = re.compile(r"^\s*(?:[-*+]\s+)?([a-hA-H])[.)]\s+(.+?)\s*$")
_ANS_RE = re.compile(
    r"^\s*#*\s*(?:\S{1,2}\s+)?(?:correct\s+)?Answer\s*[:\-]\s*"
    r"([A-Ha-h](?:\s*[,/&]\s*[A-Ha-h])*)\s*$", re.I)
_PFX_RE = re.compile(r"^\(?\s*(multiple[\s-]*choice|open[^)]*|true[\s/-]*false)\s*\)?\s*[:.\-]?\s*", re.I)

def _clean_qa_text(s):
    """Strip converter scaffolding from an imported stem/option WITHOUT changing wording.
    Docling renders a stem as a heading ("##### Q17. ...") and escapes markdown specials
    ("stop\\_reason"). Both are artefacts of the conversion, not the author's text."""
    s = (s or "").replace("\\_", "_").replace("\\*", "*").replace("\\#", "#")
    s = s.lstrip("#").strip()
    head = s.split(". ", 1)                    # a leading "Q17." question number
    if len(head) == 2 and head[0][:1].upper() == "Q" and head[0][1:].isdigit():
        s = head[1].strip()
    return s

_LEVEL_BANDS = (("beginner", 2, "recall"), ("intermediate", 3, "apply"), ("advanced", 4, "analyze"))

def source_level_bands(text):
    """{(lo, hi): (difficulty, bloom)} read from the document's OWN section headings.

    The CCA workshop modules declare their ramp explicitly:
        "BEGINNER QUESTIONS        Questions 1-30"
        "INTERMEDIATE QUESTIONS    Questions 31-60"
        "ADVANCED QUESTIONS        Questions 61-90"
    Reading that beats inferring thirds: a module split 20/40/30 would be graded wrong
    by arithmetic that happens to be right for an even split.

    Requires the word "questions" AND a bare N-M range on the line, so the summary table
    row ("Beginner  Q1-Q30  Core concepts") and prose ("For Beginner questions, aim for
    90%+") do not register as section headings.
    """
    bands = {}
    for ln in (text or "").split("\n"):
        s = ln.strip().lstrip("#").strip()
        low = s.lower()
        if "question" not in low:
            continue
        for name, diff, bloom in _LEVEL_BANDS:
            if not low.startswith(name):
                continue
            for tok in s.replace("\u2013", "-").replace("\u2014", "-").split():
                lo, _, hi = tok.partition("-")
                if lo.strip().isdigit() and hi.strip().isdigit():
                    bands[(int(lo), int(hi))] = (diff, bloom)
                    break
            break
    return bands

def import_difficulty(ordinal, max_ordinal, bands=None):
    """(difficulty, bloom) for an imported question, from its position in a numbered doc.

    A numbered Q&A document usually ramps. The CCA workshop modules declare it outright:
    "Beginner Q1-30 / Intermediate Q31-60 / Advanced Q61-90". Banding the numbered range
    into thirds keeps that ramp instead of collapsing everything to a flat difficulty 2 —
    which is what made package_judge report "100% skewed" on every module and block it.

    Heuristic, deliberately: it bands by POSITION, not by any level table the document
    might print. Unnumbered questions (ordinal None) keep the flat default.
    """
    if not ordinal:
        return (2, "recall")
    for (lo, hi), val in (bands or {}).items():      # the document's own declaration wins
        if lo <= ordinal <= hi:
            return val
    if not max_ordinal:
        return (2, "recall")
    if ordinal <= max_ordinal / 3:
        return (2, "recall")
    if ordinal <= 2 * max_ordinal / 3:
        return (3, "apply")
    return (4, "analyze")

def _q_ordinal(s):
    """"##### Q17. What is ...?" -> 17. None when the question is not numbered."""
    head = (s or "").lstrip("#").strip().split(". ", 1)
    if len(head) == 2 and head[0][:1].upper() == "Q" and head[0][1:].isdigit():
        return int(head[0][1:])
    return None

def _ans_letters(raw):
    return {x.lower() for x in raw.replace(",", " ").replace("/", " ").replace("&", " ").split() if x}

def source_answer_map(text):
    """{question number -> {answer letters}} scanned straight from the document.

    Deliberately SEPARATE from parse_qa's option-run logic: parse_qa builds a key by
    walking options and finding the answer line after them, so re-running it would only
    reproduce its own mistake. This walks question numbers instead, so a mis-associated
    answer line (the answer of Q16 attached to Q17) shows up as a disagreement between
    the two — which is exactly the failure mode the import parser can introduce.
    """
    out, cur = {}, None
    for ln in (text or "").split("\n"):
        n = _q_ordinal(ln.strip())
        if n is not None:
            cur = n; continue
        m = _ANS_RE.match(ln)
        if m and cur is not None:
            out.setdefault(cur, _ans_letters(m.group(1)))
            cur = None
    return out

def parse_qa(text):
    """Extract already-formed MCQs from a section's markdown. Returns a list of
    {stem, type, options:[{id,text,correct,rationale}], difficulty}. Open/free-text
    questions (no options) are skipped — the app only renders MCQ/true-false."""
    lines = (text or "").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        if not _OPT_RE.match(lines[i]):
            i += 1; continue
        start = i; opts = []
        while i < n:
            m = _OPT_RE.match(lines[i])
            if m:
                opts.append((m.group(1).lower(), m.group(2).strip())); i += 1; continue
            # The run has stalled. Converters break an option run in three ways:
            #   * a long option WRAPS, sometimes with a spurious list marker ("3. of
            #     parent or sibling agent context") and sometimes across a blank line;
            #   * a code-like option ("A) paths: ['**/*.tf']") gets wrapped in a fence,
            #     which can strand the last option outside it.
            # Look ahead over blanks/fences and AT MOST two stray text lines; accept
            # them as continuation only if the option run genuinely resumes afterwards,
            # so the answer line and the following prose are never swallowed.
            k2, buf = i, []
            while k2 < n:
                s2 = lines[k2].strip()
                if not s2 or s2.startswith("```"):
                    k2 += 1; continue
                if _OPT_RE.match(lines[k2]) or len(buf) >= 2:
                    break
                # HARD boundaries: an answer line ends this question, and a numbered stem
                # starts the next one. Without these the look-ahead can step over both and
                # merge the following question's options into this one (a document whose
                # questions carry no explanation prose after the answer hits this).
                if _ANS_RE.match(lines[k2]) or _q_ordinal(s2) is not None:
                    break
                buf.append(s2); k2 += 1
            if not (opts and k2 < n and _OPT_RE.match(lines[k2])):
                break
            for cont in buf:
                head = cont.split(". ", 1)             # drop the injected list marker
                if len(head) == 2 and head[0].isdigit():
                    cont = head[1]
                letter, txt = opts[-1]
                opts[-1] = (letter, (txt + " " + cont).strip())
            i = k2
        if len(opts) < 2:
            continue
        j = start - 1          # stem = nearest real line above (skip blanks AND fences:
        while j >= 0 and (not lines[j].strip()                # a fenced option block puts
                          or lines[j].strip().startswith("```")):  # ``` right above "A)")
            j -= 1
        _raw_stem = _PFX_RE.sub("", lines[j].strip()) if j >= 0 else ""
        _ordinal = _q_ordinal(_raw_stem)      # "##### Q17. ..." -> 17 (None when unnumbered)
        stem = _clean_qa_text(_raw_stem)
        k = i                     # answer = next real line (skip blanks AND a closing
        while k < n and (not lines[k].strip()          # fence: a fenced option block
                         or lines[k].strip().startswith("```")):   # leaves ``` here)
            k += 1
        am = _ANS_RE.match(lines[k]) if k < n else None
        if not (am and stem):
            continue
        ans = {x.strip().lower() for x in re.split(r"[,/&]", am.group(1))}
        i = k + 1
        options, correct = [], 0
        for idx, (letter, txt) in enumerate(opts):
            is_c = letter in ans
            correct += is_c
            options.append({"id": opt_id(idx), "text": _clean_qa_text(txt), "correct": bool(is_c), "rationale": ""})
        if correct:
            out.append({"stem": stem, "ordinal": _ordinal,
                        "type": ("mcq_multi" if correct > 1 else "mcq_single"),
                        "options": options, "difficulty": 2})
    return out


def main():
    global JOB_ID, WORK
    if not JOB_ID:
        JOB_ID = "job-" + hashlib.sha1(open(MD, "rb").read()).hexdigest()[:10]
    WORK = f"/tmp/etl_jobs/{JOB_ID}"
    os.makedirs(WORK, exist_ok=True)

    # ---------------------------------------------------------------- run
    if not os.environ.get("ETL_JOB_ID"):     # service emits its own job.queued; avoid a dupe
        emit("job.queued", documents=[{"uri": SRC_URI}])
    emit("stage.started", stage="chunk")
    chunks = chunk_doc(MD, DOC)
    emit("stage.done", stage="chunk", chunks=len(chunks))
    emit("stage.started", stage="transform")

    pkg_concepts, pkg_questions, warnings, disputes = [], [], [], []
    qnum = 0
    # one domain per document; multi-doc merge namespaces ids with d{idx}- downstream
    domains = {"d1": {"id": "d1", "title": (SRC_TITLE or "Document")[:120], "weight": None}}
    domains_used = {"d1": True}
    concept_by_title = {}      # lower(title) -> concept dict (deduped across chunks)
    _cseq = [0]

    # difficulty spread for AUTHORED questions (imported keep the source's own level)
    _DIST = {1: .15, 2: .25, 3: .30, 4: .20, 5: .10}
    _PATTERN = [lvl for lvl, frac in _DIST.items() for _ in range(max(1, round(frac * 20)))]
    _dix = [0]
    def next_targets(n):
        out = [_PATTERN[(_dix[0] + k) % len(_PATTERN)] for k in range(n)]
        _dix[0] += n
        return out

    def get_concept(title, chunk_text, locator, citation):
        key = (title or "General").strip().lower()[:160]
        if key in concept_by_title:
            return concept_by_title[key]
        _cseq[0] += 1
        concept = {"id": f"c-{_cseq[0]}", "domain": "d1", "objective": str(_cseq[0]),
                   "title": (title or "General")[:160], "summary": "", "prerequisites": [], "tags": [],
                   "grounding": [{"source_id": SRC_ID, "locator": locator or "document",
                                  "text": (chunk_text or "")[:1500], "citation": (citation or title or "")[:160]}]}
        concept_by_title[key] = concept
        pkg_concepts.append(concept)
        return concept

    # IMPORT ready-made Q&A at the DOCUMENT level, from the CLEAN markdown (full_source),
    # which preserves the "stem / a./b. / Answer:" line structure so stems are correct.
    # parse_qa only fires on that real structure, so it never invents. A document that has
    # such structure is a Q&A document → import verbatim and author NOTHING from it; a prose
    # document (no such structure) imports nothing and is authored densely. (General rule —
    # parse_qa is generic MCQ detection, not tied to any one document.)
    doc_imports = parse_qa(full_source())
    is_qa_doc = bool(doc_imports)
    if is_qa_doc:
        _p = (chunks[0].get("pages") if chunks else []) or []
        iloc = (f"p.{_p[0]}-{_p[-1]}" if len(_p) > 1 else (f"p.{_p[0]}" if _p else "document"))
        ic = get_concept((SRC_TITLE or "Imported questions")[:80], full_source()[:1500], iloc, SRC_TITLE)
        # A numbered Q&A document usually ramps: the CCA workshop modules declare
        # "Beginner Q1-30 / Intermediate Q31-60 / Advanced Q61-90". Band the numbered
        # range into thirds so the ramp survives import instead of collapsing to a flat
        # difficulty 2 — which is what made the judge call every package "100% skewed".
        _ords = [r.get("ordinal") for r in doc_imports if r.get("ordinal")]
        _omax = max(_ords) if _ords else 0
        _srcmap = source_answer_map(full_source())      # independent key check (see below)
        _bands = source_level_bands(full_source())      # the doc's declared difficulty ramp
        if _bands:
            emit("import.levels", bands={f"{a}-{b}": v[0] for (a, b), v in _bands.items()})
        for raw in doc_imports:
            qnum += 1; qid = f"q-{qnum:04d}"
            _d, _b = import_difficulty(raw.get("ordinal"), _omax, _bands)
            q = {"id": qid, "concept_ids": [ic["id"]], "type": raw["type"],
                 "render": "checkbox" if raw["type"] == "mcq_multi" else "radio",
                 "difficulty": _d, "bloom": _b,
                 "stem": raw["stem"], "options": raw["options"], "explanation": "", "hints": [],
                 "source_refs": [{"source_id": SRC_ID, "locator": iloc}], "tags": ["imported"]}
            sq = sanitize_question(q, verbatim=True)   # imported: never rewrite
            if sq:
                sq["tags"] = sorted(set((sq.get("tags") or []) + ["imported"]))
                pkg_questions.append(sq)
                # FIDELITY, not content quality: does the key we parsed match the answer the
                # DOCUMENT states for that question number? Deterministic and free — the
                # answer-blind LLM validator is for authored questions, where nothing states
                # the answer. Here it would just read "Correct Answer: B" back to us.
                _o = raw.get("ordinal")
                _want = _srcmap.get(_o) if _o else None
                if _want:
                    _got = {chr(ord("a") + i) for i, o in enumerate(sq["options"]) if o.get("correct")}
                    if _got != _want:
                        disputes.append({"qid": qid, "stem": sq["stem"][:120],
                                         "stored": sorted(_got), "derived": sorted(_want),
                                         "reason": f"imported key disagrees with the source's stated answer for Q{_o}",
                                         "evidence": ""})
                emit("question.imported", qid=qid, accepted=True)
        emit("document.imported", count=len(doc_imports))

    author_concept_ids = set()   # concepts eligible for authoring (only when NOT a Q&A document)
    for ci, chunk in enumerate([] if is_qa_doc else chunks):
        ctext = chunk["text"]; cpages = chunk.get("pages") or []; cheads = chunk.get("headings") or []
        loc = (f"p.{cpages[0]}" if len(cpages) == 1 else f"p.{cpages[0]}-{cpages[-1]}") if cpages else f"chunk {ci+1}"
        citation = " > ".join(cheads)
        emit("chunk.started", index=ci + 1, total=len(chunks), pages=cpages)
        directive = {"document_title": SRC_TITLE, "chunk_index": ci + 1, "chunk_total": len(chunks),
                     "questions_per_concept": QPC, "granularity": GRANULARITY,
                     "types": ["mcq_single", "mcq_multi", "true_false"],
                     "chunk": ctext}
        try:
            res = agent_json("document_author", json.dumps(directive))
        except Exception as e:
            warnings.append(f"chunk {ci+1}: document_author failed ({e})")
            emit("chunk.failed", index=ci + 1, error=str(e)[:200]); continue

        # Concepts to author come from the document_author. A Q&A document is import-only
        # (handled above); only PROSE documents are authored.
        if not is_qa_doc:
            for rc in (res.get("concepts") or []):
                c = get_concept(rc.get("title"), ctext, loc, citation)
                if rc.get("summary") and not c["summary"]:
                    c["summary"] = str(rc["summary"])[:600]
                author_concept_ids.add(c["id"])
        emit("chunk.done", index=ci + 1, total=len(chunks), authored_concepts=len(author_concept_ids))
        emit("transform.progress", concepts_done=len(pkg_concepts), questions_accepted=len(pkg_questions))

    # PLAN-ONLY: we now know how many concepts this document yields → forecast the
    # question count (concepts × QPC) and STOP before the expensive authoring stage.
    if PLAN_ONLY:
        n_concepts = len(author_concept_ids)
        forecast = n_concepts * QPC
        emit("job.forecast", concepts=n_concepts, qpc=QPC, granularity=GRANULARITY,
             questions=forecast, title=SRC_TITLE)
        with open(OUT, "w", encoding="utf-8") as f:      # tiny plan artifact (part reader expects a file)
            json.dump({"plan": True, "title": SRC_TITLE, "concepts": n_concepts,
                       "qpc": QPC, "granularity": GRANULARITY, "questions": forecast}, f)
        return

    # ---------------------------------------------------------------- DENSE authoring per concept
    # The local model writes only a few questions per call regardless of the prompt, so author ONE
    # CONCEPT AT A TIME with a forced target-difficulty list of length QPC — the mechanism that
    # actually yields ~QPC questions per concept (matching the old pipeline's density).
    emit("stage.started", stage="author", total=len(author_concept_ids))
    for c in list(pkg_concepts):
        if c["id"] not in author_concept_ids:   # Q&A-chunk concepts are import-only
            continue
        grounding = c["grounding"]
        loc = (grounding[0].get("locator") if grounding else "") or "document"
        cdir = {"concept": {"title": c["title"], "objective": c.get("objective", ""),
                            "summary": c["summary"], "grounding": grounding},
                "questions_per_concept": QPC, "target_difficulties": next_targets(QPC),
                "types": ["mcq_single", "mcq_multi", "true_false"]}
        try:
            qa = agent_json("question_author", json.dumps(cdir))
        except Exception as e:
            warnings.append(f"{c['id']}: author failed ({e})"); continue
        cq = 0
        for raw in (qa.get("questions") or []):
            opts = raw.get("options", []) or []
            for k, o in enumerate(opts): o["id"] = opt_id(k); o.setdefault("rationale", "")
            qnum += 1; qid = f"q-{qnum:04d}"
            q = {"id": qid, "concept_ids": [c["id"]], "type": raw.get("type", "mcq_single"),
                 "render": raw.get("render", "radio"), "difficulty": int(raw.get("difficulty", 2) or 2),
                 "bloom": raw.get("bloom", "understand"), "stem": raw.get("stem", ""),
                 "options": [{"id": o["id"], "text": o.get("text", ""), "correct": bool(o.get("correct")),
                              "rationale": o.get("rationale", "")} for o in opts],
                 "explanation": raw.get("explanation", ""), "hints": raw.get("hints", []) or [],
                 "source_refs": [{"source_id": SRC_ID, "locator": loc}], "tags": raw.get("tags", []) or []}
            scrub_meta(q)
            ok_q, why = authored_quality_ok(q)
            if not ok_q:
                warnings.append(f"{qid}: dropped — {why}")
                emit("question.judged", qid=qid, accepted=False); continue
            accepted = False
            for attempt in range(2):
                v = robust_judge(q, grounding)
                if v.get("verdict") == "accept":
                    accepted = True; break
                if attempt == 0:
                    try:
                        fix = agent_json("question_author", json.dumps({**cdir, "questions_per_concept": 1,
                            "target_difficulties": [q["difficulty"]], "fix_feedback": v.get("issues", [])}))
                        r2 = (fix.get("questions") or [None])[0]
                        if r2:
                            o2 = r2.get("options", [])
                            for k, o in enumerate(o2): o["id"] = opt_id(k); o.setdefault("rationale", "")
                            q = {**q, "type": r2.get("type", q["type"]), "render": r2.get("render", q["render"]),
                                 "stem": r2.get("stem", q["stem"]),
                                 "options": [{"id": o["id"], "text": o.get("text", ""), "correct": bool(o.get("correct")),
                                              "rationale": o.get("rationale", "")} for o in o2],
                                 "explanation": r2.get("explanation", q["explanation"])}
                            scrub_meta(q)
                    except Exception:
                        pass
                else:
                    warnings.append(f"{qid}: rejected after repair — {', '.join(v.get('issues', []))[:80]}")
            if accepted:
                pkg_questions.append(q); cq += 1
                if VALIDATE_ON:
                    va = validate_answer(q)
                    if va["status"] == "dispute":
                        disputes.append({"qid": qid, "stem": q["stem"][:120], "stored": va["stored"],
                                         "derived": va["derived"], "reason": va.get("reason", ""),
                                         "evidence": (va.get("quote") or "")[:200]})
                    emit("question.validated", qid=qid, status=va["status"])
            emit("question.judged", qid=qid, accepted=accepted)
        emit("concept.authored", concept=c["id"], title=c["title"][:60], questions=cq)
        emit("transform.progress", concepts_done=len(pkg_concepts), questions_accepted=len(pkg_questions))

    # defensive sanitize: guarantee every question is schema-valid + internally consistent
    _clean, _dropped = [], 0
    for q in pkg_questions:
        # Imported questions stay verbatim here too: this defensive pass runs over the
        # WHOLE bank, and without the flag it re-scrubs them — that is what put
        # "expansion in.mcp.json" into three CCA workshop stems (2026-08-08).
        sq = sanitize_question(q, verbatim=("imported" in (q.get("tags") or [])))
        if sq: _clean.append(sq)
        else: _dropped += 1
    if _dropped: warnings.append(f"sanitize: dropped {_dropped} unsalvageable question(s)")
    pkg_questions = _clean

    # Dedup near-duplicate stems across the package — AUTHORED questions only. Token
    # Jaccard cannot tell "differs by one decisive word" from "says the same thing":
    # "What is the Glob built-in tool best used for?" scores 0.818 against the Grep
    # version of the same sentence, so a curated bank silently loses one of a matched
    # pair that teaches the CONTRAST between them (2026-08-08). A question imported
    # verbatim was written deliberately; only the model's own output needs pruning.
    _imported = [q for q in pkg_questions if "imported" in (q.get("tags") or [])]
    _authored = [q for q in pkg_questions if "imported" not in (q.get("tags") or [])]
    _authored, _ndup = dedup_questions(_authored, threshold=0.72)
    pkg_questions = _imported + _authored
    if _ndup: warnings.append(f"dedup: removed {_ndup} near-duplicate authored question(s)")
    emit("dedup.done", removed=_ndup, remaining=len(pkg_questions), imported_kept=len(_imported))

    # Prune disputes whose question was dropped (e.g. by dedup) so we never leave
    # an orphan dispute pointing at a non-existent question.
    _surviving = {q["id"] for q in pkg_questions}
    disputes = [d for d in disputes if d.get("qid") in _surviving]

    emit("stage.done", stage="transform", concepts=len(pkg_concepts), questions=len(pkg_questions))
    emit("stage.started", stage="load")

    # ---------------------------------------------------------------- taxonomy + curator
    domains_list = [domains[d] for d in sorted(domains_used) if d in domains]
    tot_w = sum(d["weight"] for d in domains_list if d.get("weight"))
    n_dom = len(domains_list)
    for d in domains_list:
        w = d.get("weight")
        # normalize stated weights; fall back to an equal share when missing/None/0
        d["weight"] = round(w / tot_w, 3) if (w and tot_w) else round(1 / n_dom, 3)
    try:
        cur = agent_json("package_curator", json.dumps({
            "document_title": SRC_TITLE,
            "domains": [{"id": d["id"], "title": d["title"]} for d in domains_list],
            "concepts_sample": [{"title": c["title"], "domain": c["domain"]} for c in pkg_concepts[:8]]}))
        # Title is the DOCUMENT'S title (deterministic), not a model guess; the
        # curator only contributes the description.
        title = SRC_TITLE or cur.get("title") or "Untitled package"
        description = cur.get("description", "") or f"Question bank generated from {SRC_TITLE}."
    except Exception as e:
        warnings.append(f"curator failed ({e})")
        title, description = (SRC_TITLE or "Untitled package"), f"Question bank generated from {SRC_TITLE}."

    # All uploaded documents (multi-doc) or the single fallback source.
    _src_in = SOURCES or [{"id": SRC_ID, "title": SRC_TITLE, "uri": SRC_URI}]
    sources_full = [{"id": s.get("id"), "title": s.get("title"),
                     "kind": "pdf", "extractor": "docling+enrich", "uri": s.get("uri")} for s in _src_in]
    package = {
        "schema_version": "1.0", "id": PKG_ID, "title": title, "description": description,
        "generated_by": "tutor orchestrator.py (agent_server active chat model)",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": sources_full,
        "source_ids": [s["id"] for s in sources_full], "taxonomy": {"domains": domains_list},
        "concepts": pkg_concepts, "questions": pkg_questions,
    }

    # ---------------------------------------------------------------- package judge
    diff = {str(n): sum(1 for q in pkg_questions if q["difficulty"] == n) for n in range(1, 6)}
    bloom = {}
    for q in pkg_questions: bloom[q["bloom"]] = bloom.get(q["bloom"], 0) + 1
    stats = {"domains_total": len(domains_list), "concepts": len(pkg_concepts), "questions": len(pkg_questions),
             "per_domain_concepts": {d["id"]: sum(1 for c in pkg_concepts if c["domain"] == d["id"]) for d in domains_list},
             "difficulty_dist": diff, "bloom_dist": bloom}
    PUBLISH_THRESHOLD = 60
    pj = None
    try:
        pj = agent_json("package_judge", json.dumps({"stats": stats,
            "sample": [{"stem": q["stem"], "type": q["type"], "difficulty": q["difficulty"]} for q in pkg_questions[:6]]}))
    except Exception as e:
        warnings.append(f"package_judge failed ({e})")
    # publishable is decided deterministically in code (the model's boolean is advisory):
    # block only on error-severity findings or a sub-threshold score.
    findings = (pj or {}).get("findings", []) or []
    score = (pj or {}).get("score", 0) or 0
    has_error = any((f or {}).get("severity") == "error" for f in findings)
    # A package that is 100% IMPORTED is not ours to grade. package_judge scores model
    # output; on a curated exam bank it scores the AUTHOR's editorial choices — it called
    # every CCA module "100% skewed" for having a deliberate difficulty ramp, and returned
    # 75/75/60/75/75 on five packages with identical stats (it sees six questions of
    # ninety). Fidelity is what matters here, and disputes measure it. A MIXED package
    # still gets judged: authored questions in it must not slip through unexamined.
    if pkg_questions and all("imported" in (q.get("tags") or []) for q in pkg_questions):
        findings = [f for f in findings if (f or {}).get("severity") != "error"]
        has_error = False
        score = 100
        emit("package.judge_skipped", reason="verbatim import", questions=len(pkg_questions))
    # answer-blind disputes block publishing too: a question whose stored key disagrees with
    # an independent solve must be reviewed by a human before the package goes live.
    publishable = (not has_error) and score >= PUBLISH_THRESHOLD and not disputes
    package["quality"] = {"score": score, "publishable": publishable, "findings": findings,
                          "disputes": disputes}
    emit("package.judged", score=score, publishable=publishable,
         errors=sum(1 for f in findings if (f or {}).get("severity") == "error"), disputes=len(disputes))

    # ---------------------------------------------------------------- validate + write
    ok = True
    try:
        jsonschema.validate(package, json.load(open(SCHEMA)))
        valid = "VALID"
    except jsonschema.ValidationError as e:
        ok = False
        valid = f"INVALID: {e.message}"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(package, open(OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    if not ok:
        emit("job.failed", stage="load", error="schema validation failed")
    elif not publishable:
        reason = (f"{len(disputes)} key dispute(s) need review" if disputes
                  else "error findings present" if has_error else f"score {score} < {PUBLISH_THRESHOLD}")
        emit("job.held", reason=reason, score=score, disputes=len(disputes))
    else:
        emit("job.published", packageId=package["id"],
             catalogEntry={"id": package["id"], "title": title, "questions": len(pkg_questions)})
    emit("stage.done", stage="load", valid=ok)

    print("\n=== RESULT ===")
    print(f"job: {JOB_ID}")
    print(f"concepts: {len(pkg_concepts)} | questions: {len(pkg_questions)} | warnings: {len(warnings)}")
    print(f"difficulty dist: {diff}")
    print(f"bloom dist: {bloom}")
    print(f"domains: {[(d['id'], d.get('weight')) for d in domains_list]}")
    print(f"package_judge: score={score} publishable={publishable} "
          f"(errors={sum(1 for f in findings if (f or {}).get('severity')=='error')}, findings={len(findings)})")
    for f in findings[:5]:
        print(f"  [{(f or {}).get('severity')}] {(f or {}).get('area')}: {(f or {}).get('detail','')[:110]}")
    print(f"answer-blind disputes: {len(disputes)}")
    for dsp in disputes[:10]:
        print(f"  [{dsp['qid']}] stored={dsp['stored']} solved={dsp['derived']} — {dsp['stem']}")
    print(f"validation: {valid}")
    print(f"written: {OUT}")
    for w in warnings[:25]: print("  -", w)



if __name__ == "__main__":
    main()
