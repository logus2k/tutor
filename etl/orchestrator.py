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
import json, re, sys, os, urllib.request, hashlib, datetime
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
API  = os.environ.get("ETL_API", "http://localhost:7701/v1/chat/completions")

MAX_CONCEPTS = int(os.environ.get("ETL_MAX_CONCEPTS", sys.argv[1] if len(sys.argv) > 1 else 999))
QPC          = int(os.environ.get("ETL_QPC", sys.argv[2] if len(sys.argv) > 2 else 5))
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

def scrub_meta(q):
    """Strip source-pointer phrasing from every prose field of a built question
    (in place). Run BEFORE judging so the judge sees self-contained text."""
    if isinstance(q.get("stem"), str):
        q["stem"] = strip_meta_references(q["stem"])
    if isinstance(q.get("explanation"), str):
        q["explanation"] = strip_meta_references(q["explanation"])
    if isinstance(q.get("hints"), list):
        q["hints"] = [strip_meta_references(h) if isinstance(h, str) else h for h in q["hints"]]
    for o in (q.get("options") or []):
        if isinstance(o.get("rationale"), str):
            o["rationale"] = strip_meta_references(o["rationale"])
    return q

# ---------------------------------------------------------------- sanitize (defensive guardrail)
VALID_TYPES = {"mcq_single", "mcq_multi", "true_false"}
VALID_BLOOM = {"recall", "understand", "apply", "analyze"}
def sanitize_question(q):
    """Coerce an LLM-authored question to a schema-valid, internally-consistent
    shape. Returns the cleaned question, or None if it cannot be salvaged."""
    scrub_meta(q)
    opts = q.get("options") or []
    if len(opts) < 2:
        return None
    nc = sum(1 for o in opts if o.get("correct"))
    if nc == 0:
        return None                                     # no key -> unsalvageable
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

def robust_judge(q, grounding):
    """Judge a question, tolerating malformed judge output. Retries once with a
    stricter reminder, then conservatively REJECTS (never silently keeps)."""
    base = json.dumps({"question": q, "grounding": grounding})
    for attempt in range(2):
        suffix = "" if attempt == 0 else ("\n\nYour previous reply was NOT valid JSON. "
                 "Reply with ONLY one valid JSON object — no comments, no extra text.")
        try:
            return agent_json("question_judge", base + suffix)
        except Exception:
            continue
    return {"verdict": "reject", "issues": ["judge output unparseable"], "_parsefail": True}

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
    lines = open(md_path, encoding="utf-8").read().split("\n")
    domain = {"num": None, "title": None, "weight": None}
    domains, concepts, cur = {}, [], None
    for ln in lines:
        md_ = re.match(r"^## Domain (\d+):\s*(.*)", ln)
        ob  = re.match(r"^#### (\d+\.\d+\.\d+)\s+(.*)", ln)
        brk = re.match(r"^#{1,4}\s", ln)
        if md_:
            num, ttl = md_.group(1), md_.group(2).strip()
            wm = re.search(r"\((\d+)\s*-\s*(\d+)%\)", ttl)
            weight = (int(wm.group(1)) + int(wm.group(2))) / 200 if wm else None
            ttl = re.sub(r"\s*\(\d+\s*-\s*\d+%\)", "", ttl)
            domain = {"num": num, "title": ttl, "weight": weight}
            domains[f"d{num}"] = {"id": f"d{num}", "title": ttl, "weight": weight}
            if cur: concepts.append(cur); cur = None
            continue
        if ob:
            if cur: concepts.append(cur)
            cur = {"objective": ob.group(1), "title": ob.group(2).strip(),
                   "domain_num": domain["num"], "lines": []}
            continue
        if brk and not ln.startswith("#####"):
            if cur: concepts.append(cur); cur = None
            continue
        if cur is not None: cur["lines"].append(ln)
    if cur: concepts.append(cur)
    for c in concepts:
        c["text"] = "\n".join(c["lines"]).strip(); c["words"] = len(c["text"].split())
    return concepts, domains

# ---------------------------------------------------------------- helpers
def opt_id(i): return chr(ord("a") + i)
def target_difficulties(n):
    if n <= 1: return [3]
    return [max(1, min(5, round(1 + i * 4 / (n - 1)))) for i in range(n)]
LOC_RE = re.compile(r"^(§\d+\.\d+(?:\.\d+)?|p\.\s*\d+)$")
def clean_locator(loc, fallback):
    loc = (loc or "").strip()
    return loc if LOC_RE.match(loc) else fallback


def main():
    global JOB_ID, WORK
    if not JOB_ID:
        JOB_ID = "job-" + hashlib.sha1(open(MD, "rb").read()).hexdigest()[:10]
    WORK = f"/tmp/etl_jobs/{JOB_ID}"
    os.makedirs(WORK, exist_ok=True)

    # ---------------------------------------------------------------- run
    if not os.environ.get("ETL_JOB_ID"):     # service emits its own job.queued; avoid a dupe
        emit("job.queued", documents=[{"uri": SRC_URI}])
    emit("stage.started", stage="segment")
    PAGES = page_map(DOC)
    all_concepts, domains = segment(MD)
    emit("stage.done", stage="segment", concepts_found=len(all_concepts), pages_mapped=len(PAGES))
    emit("stage.started", stage="transform")

    pkg_concepts, pkg_questions, warnings = [], [], []
    domains_used, qnum = {}, 0

    # package-wide difficulty plan: a repeating pattern proportional to the target
    # distribution, drawn QPC-at-a-time so the *package* covers all 5 levels.
    _DIST = {1: .15, 2: .25, 3: .30, 4: .20, 5: .10}
    _PATTERN = [lvl for lvl, frac in _DIST.items() for _ in range(max(1, round(frac * 20)))]
    _dix = [0]
    def next_targets(n):
        out = [_PATTERN[(_dix[0] + k) % len(_PATTERN)] for k in range(n)]
        _dix[0] += n
        return out

    def gate(meta):
        return agent_json("concept_extractor", meta)

    i = 0
    processed = 0
    while i < len(all_concepts) and len(pkg_concepts) < MAX_CONCEPTS:
        c = all_concepts[i]; i += 1
        obj = c["objective"]
        page = PAGES.get(obj)
        locator = (f"p.{page} " if page else "") + f"§{obj}"
        locator_code = f"§{obj}"
        if c["words"] < MIN_WORDS:
            warnings.append(f"{locator_code}: pre-filtered ({c['words']} words)")
            emit("concept.skipped", objective=obj, reason="below min words"); continue

        cache_f = f"{WORK}/{obj}.json"
        if os.path.exists(cache_f):                      # resume
            cached = json.load(open(cache_f))
            pkg_concepts.append(cached["concept"]); pkg_questions.extend(cached["questions"])
            domains_used[cached["concept"]["domain"]] = True
            emit("concept.cached", objective=obj, questions=len(cached["questions"])); continue

        meta = (f"Objective: {obj} | Heading: {c['title']} | Locator: {locator_code} | "
                f"Domain: {domains.get('d'+str(c['domain_num']),{}).get('title','')}\n\n{c['text']}")
        try:
            ce = gate(meta)
        except Exception as e:
            warnings.append(f"{locator_code}: gate failed ({e})"); continue

        action = ce.get("suggested_action", "accept")
        if not ce.get("usable") or action == "skip":
            warnings.append(f"{locator_code}: gate rejected ({ce.get('reason','?')})")
            emit("concept.skipped", objective=obj, reason=ce.get("reason", "")); continue
        if action == "merge_next" and i < len(all_concepts):   # bounded remediation (1 round)
            nxt = all_concepts[i]
            meta2 = meta + "\n\n" + nxt["text"]
            try:
                ce2 = gate(meta2)
                if ce2.get("usable"):
                    ce = ce2; c = {**c, "text": c["text"] + "\n" + nxt["text"]}; i += 1
                    emit("concept.merged", objective=obj, merged_with=nxt["objective"])
            except Exception:
                pass
        elif action in ("merge_prev", "split"):
            warnings.append(f"{locator_code}: gate suggested '{action}' — accepted as-is (remediation-lite)")

        did = f"d{c['domain_num']}"; domains_used[did] = True
        cid = f"c-{obj.replace('.', '-')}"
        grounding = [{"source_id": SRC_ID, "locator": clean_locator(g.get("locator"), locator),
                      "text": g.get("text", ""), "citation": f"AI-901 {locator}"}
                     for g in (ce.get("grounding") or [])]
        if not grounding:
            grounding = [{"source_id": SRC_ID, "locator": locator, "text": c["text"][:300],
                          "citation": f"AI-901 {locator}"}]
        concept = {"id": cid, "domain": did, "objective": obj,
                   "title": ce.get("title") or c["title"], "summary": ce.get("summary", ""),
                   "prerequisites": ce.get("prerequisites", []) or [], "grounding": grounding,
                   "tags": ce.get("tags", []) or []}
        emit("concept.gated", objective=obj, title=concept["title"], action=action)

        directive = {"concept": {"title": concept["title"], "objective": obj,
                                 "summary": concept["summary"], "grounding": grounding},
                     "questions_per_concept": QPC, "target_difficulties": next_targets(QPC),
                     "types": ["mcq_single", "mcq_multi", "true_false"]}
        try:
            qa = agent_json("question_author", json.dumps(directive))
        except Exception as e:
            warnings.append(f"{locator_code}: author failed ({e})"); pkg_concepts.append(concept); continue

        concept_qs = []
        for raw in qa.get("questions", []):
            opts = raw.get("options", [])
            for k, o in enumerate(opts): o["id"] = opt_id(k); o.setdefault("rationale", "")
            qnum += 1; qid = f"q-{qnum:04d}"
            srcs = [{"source_id": SRC_ID, "locator": clean_locator(l, locator_code)}
                    for l in (raw.get("source_locators") or [locator_code])]
            q = {"id": qid, "concept_ids": [cid], "type": raw.get("type", "mcq_single"),
                 "render": raw.get("render", "radio"), "difficulty": int(raw.get("difficulty", 2)),
                 "bloom": raw.get("bloom", "understand"), "stem": raw.get("stem", ""),
                 "options": [{"id": o["id"], "text": o.get("text", ""), "correct": bool(o.get("correct")),
                              "rationale": o.get("rationale", "")} for o in opts],
                 "explanation": raw.get("explanation", ""), "hints": raw.get("hints", []) or [],
                 "source_refs": srcs, "tags": raw.get("tags", []) or []}
            scrub_meta(q)   # self-contain BEFORE judging so the judge sees clean text
            # judge with one bounded repair
            accepted = False
            for attempt in range(2):
                v = robust_judge(q, grounding)
                if v.get("_parsefail"):
                    warnings.append(f"{qid}: judge unparseable after retry — conservative reject")
                if v.get("verdict") == "accept":
                    accepted = True; break
                if attempt == 0:   # one repair: ask author to fix this one
                    try:
                        fix = agent_json("question_author", json.dumps({**directive,
                            "questions_per_concept": 1, "target_difficulties": [q["difficulty"]],
                            "fix_feedback": v.get("issues", [])}))
                        r2 = (fix.get("questions") or [None])[0]
                        if r2:
                            o2 = r2.get("options", [])
                            for k, o in enumerate(o2): o["id"] = opt_id(k); o.setdefault("rationale", "")
                            q = {**q, "type": r2.get("type", q["type"]), "render": r2.get("render", q["render"]),
                                 "stem": r2.get("stem", q["stem"]),
                                 "options": [{"id": o["id"], "text": o.get("text",""), "correct": bool(o.get("correct")),
                                              "rationale": o.get("rationale","")} for o in o2],
                                 "explanation": r2.get("explanation", q["explanation"])}
                            scrub_meta(q)   # re-scrub the repaired question before re-judging
                    except Exception:
                        pass
                else:
                    warnings.append(f"{qid} ({locator_code}): rejected after repair — {', '.join(v.get('issues', []))[:100]}")
            if accepted: concept_qs.append(q)
            emit("question.judged", objective=obj, qid=qid, accepted=accepted)

        pkg_concepts.append(concept); pkg_questions.extend(concept_qs)
        json.dump({"concept": concept, "questions": concept_qs}, open(cache_f, "w"))
        processed += 1
        emit("transform.progress", concepts_done=len(pkg_concepts), questions_accepted=len(pkg_questions))

    # defensive sanitize: guarantee every question is schema-valid + internally consistent
    _clean, _dropped = [], 0
    for q in pkg_questions:
        sq = sanitize_question(q)
        if sq: _clean.append(sq)
        else: _dropped += 1
    if _dropped: warnings.append(f"sanitize: dropped {_dropped} unsalvageable question(s)")
    pkg_questions = _clean

    # dedup near-duplicate stems across the whole package
    pkg_questions, _ndup = dedup_questions(pkg_questions)
    if _ndup: warnings.append(f"dedup: removed {_ndup} near-duplicate question(s)")
    emit("dedup.done", removed=_ndup, remaining=len(pkg_questions))

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
            "document_title": "Microsoft Certified: Azure AI Fundamentals (AI-901) Master Cheat Sheet",
            "domains": [{"id": d["id"], "title": d["title"]} for d in domains_list],
            "concepts_sample": [{"title": c["title"], "domain": c["domain"]} for c in pkg_concepts[:8]]}))
        title = cur.get("title") or "Azure AI Fundamentals (AI-901)"
        description = cur.get("description", "") or "Question bank generated from the AI-901 extraction."
    except Exception as e:
        warnings.append(f"curator failed ({e})")
        title, description = "Azure AI Fundamentals (AI-901)", "Question bank generated from the AI-901 extraction."

    package = {
        "schema_version": "1.0", "id": PKG_ID, "title": title, "description": description,
        "generated_by": "tutor orchestrator.py (gemma-4-e2b)",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": [{"id": SRC_ID, "title": SRC_TITLE,
                     "kind": "pdf", "extractor": "docling+enrich", "uri": SRC_URI}],
        "source_ids": [SRC_ID], "taxonomy": {"domains": domains_list},
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
    publishable = (not has_error) and score >= PUBLISH_THRESHOLD
    package["quality"] = {"score": score, "publishable": publishable, "findings": findings}
    emit("package.judged", score=score, publishable=publishable, errors=sum(1 for f in findings if (f or {}).get("severity") == "error"))

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
        emit("job.held", reason=("error findings present" if has_error else f"score {score} < {PUBLISH_THRESHOLD}"), score=score)
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
    print(f"validation: {valid}")
    print(f"written: {OUT}")
    for w in warnings[:25]: print("  -", w)



if __name__ == "__main__":
    main()
