"""Merge a single-document package into an accumulating draft package.

Per-document ingestion authors each document independently (orchestrator.py runs
once per document, producing a standalone "part" package). To build ONE package
from many documents we merge each part into a running draft, namespacing the
part's ids so concepts/questions/domains from different documents never collide.

Source ids are already globally unique (`src-<slug>` per document), so grounding
and source_refs that point at them need no remapping — only concept/question/
domain ids (and the references to them) are prefixed with the document index.
"""
from __future__ import annotations


def _ns(idx: int, s: str) -> str:
    return f"d{idx}-{s}"


def _renumber(part: dict, idx: int) -> dict:
    """Prefix concept/question/domain ids (and their cross-refs) in `part`."""
    dom_remap = {}
    for dom in part.get("taxonomy", {}).get("domains", []):
        old = dom.get("id")
        if old:
            dom["id"] = _ns(idx, old); dom_remap[old] = dom["id"]
    for c in part.get("concepts", []):
        c["id"] = _ns(idx, c.get("id", ""))
        if c.get("domain"):
            c["domain"] = dom_remap.get(c["domain"], _ns(idx, c["domain"]))
    for q in part.get("questions", []):
        q["id"] = _ns(idx, q.get("id", ""))
        q["concept_ids"] = [_ns(idx, cid) for cid in (q.get("concept_ids") or [])]
    for d in (part.get("quality", {}) or {}).get("disputes", []):
        d["qid"] = _ns(idx, d.get("qid", ""))
    return part


def merge_part(target: dict | None, part: dict, idx: int,
               pkg_id: str, pkg_title: str) -> dict:
    """Merge `part` (a single-doc package) into `target` (the draft, or None to
    start it). `idx` is the 0-based document index used for id namespacing."""
    _renumber(part, idx)

    if target is None:
        # First document seeds the draft; keep the package's own id/title.
        target = dict(part)
        target["id"] = pkg_id
        target["title"] = pkg_title
        target.setdefault("quality", {}).setdefault("disputes",
                                                     part.get("quality", {}).get("disputes", []))
        target["quality"]["publishable"] = False
        return target

    # Append everything (sources deduped by id).
    have = {s.get("id") for s in target.get("sources", [])}
    for s in part.get("sources", []):
        if s.get("id") not in have:
            target.setdefault("sources", []).append(s)
            target.setdefault("source_ids", []).append(s.get("id"))
    target.setdefault("taxonomy", {}).setdefault("domains", []).extend(
        part.get("taxonomy", {}).get("domains", []))
    target.setdefault("concepts", []).extend(part.get("concepts", []))
    target.setdefault("questions", []).extend(part.get("questions", []))
    tq = target.setdefault("quality", {})
    tq.setdefault("disputes", []).extend(part.get("quality", {}).get("disputes", []))
    # Aggregate score = the weakest part's score (conservative).
    ps = part.get("quality", {}).get("score")
    if ps is not None:
        tq["score"] = ps if tq.get("score") is None else min(tq["score"], ps)
    tq["publishable"] = False
    return target


def counts(pkg: dict) -> dict:
    return {"concepts": len(pkg.get("concepts", [])),
            "questions": len(pkg.get("questions", [])),
            "disputes": len((pkg.get("quality", {}) or {}).get("disputes", [])),
            "sources": len(pkg.get("sources", []))}
