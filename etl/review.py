"""Held-package store for the Dispute Review area.

When the authoring pipeline can't publish a package (key disputes, sub-threshold
score, or error findings — see orchestrator.py §6.1), the package is NOT thrown
away: it's retained here so a human can resolve the disputes and publish it.

Layout (under the rw `data/` mount, so it survives rebuilds):
  data/held/<id>.json      the full package + `quality.disputes` + `owner_email`
  data/held/<id>.src.md    the extracted source text (lets the validator re-run)

Held packages are deliberately OUTSIDE data/packages/, so the Catalog index
(which scans data/packages/*.json) never lists them. Publishing moves the file
into data/packages/ and rebuilds the index.
"""
from __future__ import annotations

import json
import os
import re
import datetime

from etl import catalog

HELD_DIR = os.path.join(catalog.ROOT, "data", "held")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _safe_id(pkg_id: str) -> str:
    """Package ids are slugs; refuse anything else (path-traversal guard)."""
    pid = (pkg_id or "").strip()
    if not pid or not re.fullmatch(r"[A-Za-z0-9._-]+", pid):
        return ""
    return pid


def _json_path(pkg_id: str) -> str:
    return os.path.join(HELD_DIR, pkg_id + ".json")


def _src_path(pkg_id: str) -> str:
    return os.path.join(HELD_DIR, pkg_id + ".src.md")


def disputes_of(package: dict) -> list:
    return (package.get("quality") or {}).get("disputes", []) or []


# ---- persistence -------------------------------------------------------------

def save_held(package: dict, owner_email: str | None, src_md: str | None = None) -> None:
    os.makedirs(HELD_DIR, exist_ok=True)
    pid = _safe_id(package.get("id", ""))
    if not pid:
        raise ValueError("package has no valid id")
    package = dict(package)
    package["owner_email"] = (owner_email or "").strip().lower() or None
    package.setdefault("held_at", _now())
    with open(_json_path(pid), "w", encoding="utf-8") as f:
        json.dump(package, f, indent=2, ensure_ascii=False)
    if src_md:
        with open(_src_path(pid), "w", encoding="utf-8") as f:
            f.write(src_md)


def get_held(pkg_id: str) -> dict | None:
    pid = _safe_id(pkg_id)
    if not pid or not os.path.exists(_json_path(pid)):
        return None
    try:
        return json.load(open(_json_path(pid), encoding="utf-8"))
    except Exception:
        return None


def get_src(pkg_id: str) -> str:
    pid = _safe_id(pkg_id)
    if not pid or not os.path.exists(_src_path(pid)):
        return ""
    try:
        return open(_src_path(pid), encoding="utf-8").read()
    except Exception:
        return ""


def write_held(package: dict) -> None:
    pid = _safe_id(package.get("id", ""))
    if not pid:
        raise ValueError("package has no valid id")
    with open(_json_path(pid), "w", encoding="utf-8") as f:
        json.dump(package, f, indent=2, ensure_ascii=False)


def delete_held(pkg_id: str) -> bool:
    pid = _safe_id(pkg_id)
    if not pid:
        return False
    removed = False
    for p in (_json_path(pid), _src_path(pid)):
        try:
            os.remove(p); removed = True
        except OSError:
            pass
    return removed


def list_held() -> list[dict]:
    """Summaries of every held package (newest first)."""
    if not os.path.isdir(HELD_DIR):
        return []
    out = []
    for name in os.listdir(HELD_DIR):
        if not name.endswith(".json"):
            continue
        try:
            p = json.load(open(os.path.join(HELD_DIR, name), encoding="utf-8"))
        except Exception:
            continue
        q = p.get("quality") or {}
        out.append({
            "id": p.get("id"),
            "title": p.get("title") or p.get("id"),
            "owner_email": p.get("owner_email"),
            "questions": len(p.get("questions", [])),
            "disputes": len(q.get("disputes", []) or []),
            "score": q.get("score"),
            "held_at": p.get("held_at"),
        })
    out.sort(key=lambda r: r.get("held_at") or "", reverse=True)
    return out


def publish_held(pkg_id: str) -> dict | None:
    """Promote a (resolved) held package into the Catalog. Returns the catalog
    entry, or None if not found. Caller must ensure no unresolved disputes."""
    pid = _safe_id(pkg_id)
    pkg = get_held(pid)
    if not pkg:
        return None
    q = pkg.setdefault("quality", {})
    q["publishable"] = True
    q["published_at"] = _now()
    os.makedirs(catalog.PKG_DIR, exist_ok=True)
    with open(os.path.join(catalog.PKG_DIR, pid + ".json"), "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
    delete_held(pid)
    catalog.rebuild_package_index()
    return {"id": pkg.get("id"), "title": pkg.get("title"),
            "questions": len(pkg.get("questions", []))}


# ---- question-level mutation (resolution) ------------------------------------

def find_question(package: dict, qid: str) -> dict | None:
    for q in package.get("questions", []):
        if q.get("id") == qid:
            return q
    return None


def clear_dispute(package: dict, qid: str) -> None:
    q = package.get("quality") or {}
    q["disputes"] = [d for d in (q.get("disputes") or []) if d.get("qid") != qid]
    package["quality"] = q
