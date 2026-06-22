#!/usr/bin/env python3
"""Thin-slice ETL orchestrator (proves the agent chain end-to-end).

Pipeline for a few AI-901 concepts:
  segment (deterministic) -> concept_extractor (gate+extract) -> question_author
  -> question_judge -> assemble package -> package_curator + package_judge -> validate.

Deliberately minimal: handles `accept`/`skip` gate verdicts (merge/split deferred
to the full orchestrator), drops judge-rejected questions with a warning, and
uses objective-based locators (full provenance/page numbers come from the
DoclingDocument in the full build).
"""
import json, re, sys, urllib.request, hashlib, datetime

MD = "/home/logus/env/study/ms/materials/AI-901.md"
OUT = "/home/logus/env/assets/tutor/data/packages/ai-901-slice.json"
SCHEMA = "/home/logus/env/assets/tutor/schema/package.schema.json"
SRC_ID = "src-ai901"
API = "http://localhost:7701/v1/chat/completions"

MAX_CONCEPTS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
QPC = int(sys.argv[2]) if len(sys.argv) > 2 else 3
MIN_WORDS = 25

# ---------------------------------------------------------------- LLM helper
def agent(name, user, timeout=180):
    body = json.dumps({"model": name, "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=timeout))
    return d["choices"][0]["message"]["content"]

def extract_json(text):
    """Pull the first balanced {...} object out of an LLM reply (tolerates fences)."""
    t = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text.strip(), flags=re.M)
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, instr, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if instr:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': instr = False
        else:
            if c == '"': instr = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(t[start:i+1])
    raise ValueError("unbalanced JSON object")

def agent_json(name, user, **kw):
    return extract_json(agent(name, user, **kw))

# ---------------------------------------------------------------- segment (deterministic)
def segment(md_path):
    lines = open(md_path, encoding="utf-8").read().split("\n")
    domain = {"num": None, "title": None}
    concepts, cur = [], None
    for ln in lines:
        m_dom = re.match(r"^## Domain (\d+):\s*(.*)", ln)
        m_obj = re.match(r"^#### (\d+\.\d+\.\d+)\s+(.*)", ln)
        m_brk = re.match(r"^#{1,4}\s", ln)        # any 1-4 hash heading ends a concept
        if m_dom:
            domain = {"num": m_dom.group(1), "title": m_dom.group(2).strip()}
            if cur: concepts.append(cur); cur = None
            continue
        if m_obj:
            if cur: concepts.append(cur)
            cur = {"objective": m_obj.group(1), "title": m_obj.group(2).strip(),
                   "domain_num": domain["num"], "domain_title": domain["title"], "lines": []}
            continue
        if m_brk and not ln.startswith("#####"):  # ##### sub-labels stay inside the concept
            if cur: concepts.append(cur); cur = None
            continue
        if cur is not None:
            cur["lines"].append(ln)
    if cur: concepts.append(cur)
    for c in concepts:
        c["text"] = "\n".join(c["lines"]).strip()
        c["words"] = len(c["text"].split())
    return concepts

# ---------------------------------------------------------------- assemble helpers
def opt_id(i): return chr(ord("a") + i)
def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

# ---------------------------------------------------------------- run
print(f"Segmenting {MD} …")
all_concepts = segment(MD)
print(f"  found {len(all_concepts)} concept candidates; taking first {MAX_CONCEPTS}")

pkg_concepts, pkg_questions, warnings = [], [], []
domains_seen, qnum = {}, 0

for c in all_concepts:
    if len(pkg_concepts) >= MAX_CONCEPTS:
        break
    locator = f"§{c['objective']}"
    # ---- pre-filter (deterministic) ----
    if c["words"] < MIN_WORDS:
        warnings.append(f"{locator}: pre-filtered (only {c['words']} words)"); continue

    # ---- gate + extract ----
    meta = (f"Objective: {c['objective']} | Heading: {c['title']} | Locator: {locator} | "
            f"Domain: {c['domain_title']}\n\n{c['text']}")
    try:
        ce = agent_json("concept_extractor", meta)
    except Exception as e:
        warnings.append(f"{locator}: concept_extractor failed ({e})"); continue
    if not ce.get("usable") or ce.get("suggested_action") == "skip":
        warnings.append(f"{locator}: gate rejected ({ce.get('reason','?')})"); continue
    if ce.get("suggested_action") in ("merge_next", "merge_prev", "split"):
        warnings.append(f"{locator}: gate suggested '{ce['suggested_action']}' — accepted as-is (thin slice)")

    did = f"d{c['domain_num']}"
    domains_seen[did] = c["domain_title"]
    cid = f"c-{c['objective'].replace('.', '-')}"
    grounding = []
    for g in ce.get("grounding", []) or []:
        grounding.append({"source_id": SRC_ID, "locator": g.get("locator") or locator,
                          "text": g.get("text", ""), "citation": f"AI-901 {locator}"})
    if not grounding:
        grounding = [{"source_id": SRC_ID, "locator": locator, "text": c["text"][:300],
                      "citation": f"AI-901 {locator}"}]
    concept = {"id": cid, "domain": did, "objective": c["objective"],
               "title": ce.get("title") or c["title"], "summary": ce.get("summary", ""),
               "prerequisites": ce.get("prerequisites", []) or [], "grounding": grounding,
               "tags": ce.get("tags", []) or []}

    # ---- author questions ----
    directive = {"concept": {"title": concept["title"], "objective": c["objective"],
                             "summary": concept["summary"], "grounding": grounding},
                 "questions_per_concept": QPC,
                 "difficulty_distribution": {"1": .15, "2": .25, "3": .3, "4": .2, "5": .1},
                 "types": ["mcq_single", "mcq_multi", "true_false"]}
    try:
        qa = agent_json("question_author", json.dumps(directive))
    except Exception as e:
        warnings.append(f"{locator}: question_author failed ({e})"); pkg_concepts.append(concept); continue

    kept = 0
    for raw in qa.get("questions", []):
        opts = raw.get("options", [])
        for i, o in enumerate(opts):
            o["id"] = opt_id(i)
            o.setdefault("rationale", "")
        qnum += 1
        qid = f"q-{qnum:04d}"
        srcs = [{"source_id": SRC_ID, "locator": l} for l in (raw.get("source_locators") or [locator])]
        q = {"id": qid, "concept_ids": [cid], "type": raw.get("type", "mcq_single"),
             "render": raw.get("render", "radio"), "difficulty": int(raw.get("difficulty", 2)),
             "bloom": raw.get("bloom", "understand"), "stem": raw.get("stem", ""),
             "options": [{"id": o["id"], "text": o.get("text", ""), "correct": bool(o.get("correct")),
                          "rationale": o.get("rationale", "")} for o in opts],
             "explanation": raw.get("explanation", ""), "hints": raw.get("hints", []) or [],
             "source_refs": srcs, "tags": raw.get("tags", []) or []}
        # ---- judge ----
        try:
            verdict = agent_json("question_judge", json.dumps({"question": q, "grounding": grounding}))
        except Exception as e:
            warnings.append(f"{qid}: judge failed ({e}) — kept unjudged"); pkg_questions.append(q); kept += 1; continue
        if verdict.get("verdict") == "accept":
            pkg_questions.append(q); kept += 1
        else:
            warnings.append(f"{qid} ({locator}): judge rejected — {', '.join(verdict.get('issues', []))[:120]}")
    pkg_concepts.append(concept)
    print(f"  {locator} '{concept['title'][:40]}' → {kept}/{len(qa.get('questions', []))} questions kept")

# ---------------------------------------------------------------- taxonomy + curator
domains = [{"id": k, "title": v} for k, v in sorted(domains_seen.items())]
try:
    cur = agent_json("package_curator", json.dumps({
        "document_title": "Microsoft Certified: Azure AI Fundamentals (AI-901) Master Cheat Sheet",
        "domains": domains, "concepts_sample": [{"title": c["title"], "domain": c["domain"]} for c in pkg_concepts]}))
    title = cur.get("title") or "Azure AI Fundamentals (AI-901)"
    description = cur.get("description", "")
    cur_dom = {d["id"]: d for d in cur.get("taxonomy", {}).get("domains", [])}
    for d in domains:
        w = cur_dom.get(d["id"], {}).get("weight")
        if isinstance(w, (int, float)): d["weight"] = round(float(w), 3)
except Exception as e:
    warnings.append(f"package_curator failed ({e}) — using deterministic taxonomy")
    title, description = "Azure AI Fundamentals (AI-901)", "Thin-slice question bank for AI-901."
if not any("weight" in d for d in domains):
    for d in domains: d["weight"] = round(1 / len(domains), 3)

package = {
    "schema_version": "1.0", "id": "ai-901-slice", "title": title,
    "description": description or "Thin-slice question bank generated from the AI-901 extraction.",
    "generated_by": "tutor thin_slice.py (gemma-4-e2b)",
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "sources": [{"id": SRC_ID, "title": "Microsoft Certified: Azure AI Fundamentals (AI-901) Master Cheat Sheet",
                 "kind": "pdf", "extractor": "docling+enrich", "uri": "materials/AI-901.pdf"}],
    "source_ids": [SRC_ID], "taxonomy": {"domains": domains},
    "concepts": pkg_concepts, "questions": pkg_questions,
}

# ---------------------------------------------------------------- package judge
stats = {"concepts": len(pkg_concepts), "questions": len(pkg_questions),
         "per_domain": {d["id"]: sum(1 for c in pkg_concepts if c["domain"] == d["id"]) for d in domains},
         "difficulty_dist": {str(n): sum(1 for q in pkg_questions if q["difficulty"] == n) for n in range(1, 6)},
         "bloom_dist": {}}
for q in pkg_questions:
    stats["bloom_dist"][q["bloom"]] = stats["bloom_dist"].get(q["bloom"], 0) + 1
try:
    pj = agent_json("package_judge", json.dumps({"stats": stats,
        "sample": [{"stem": q["stem"], "type": q["type"], "options": [o["text"] for o in q["options"]]}
                   for q in pkg_questions[:5]]}))
    print(f"\npackage_judge: score={pj.get('score')} publishable={pj.get('publishable')}")
    for f in pj.get("findings", []): print(f"  [{f.get('severity')}] {f.get('area')}: {f.get('detail')}")
except Exception as e:
    pj = None; warnings.append(f"package_judge failed ({e})")

# ---------------------------------------------------------------- validate + write
def validate(pkg, schema_path):
    try:
        import jsonschema
        jsonschema.validate(pkg, json.load(open(schema_path)))
        return "jsonschema: VALID"
    except ImportError:
        # minimal fallback
        assert pkg["questions"], "no questions"
        for q in pkg["questions"]:
            assert q["type"] in ("mcq_single", "mcq_multi", "true_false")
            n_correct = sum(1 for o in q["options"] if o["correct"])
            assert n_correct >= 1, f"{q['id']} has no correct option"
            if q["type"] in ("mcq_single", "true_false"):
                assert n_correct == 1, f"{q['id']} single-choice has {n_correct} correct"
        return "fallback checks: VALID (jsonschema not installed)"

result = validate(package, SCHEMA)
json.dump(package, open(OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

print(f"\n=== RESULT ===")
print(f"concepts: {len(pkg_concepts)} | questions: {len(pkg_questions)} | warnings: {len(warnings)}")
print(f"difficulty dist: {stats['difficulty_dist']}")
print(f"validation: {result}")
print(f"written: {OUT}")
if warnings:
    print("\nwarnings:")
    for w in warnings: print(f"  - {w}")
