#!/usr/bin/env python3
"""Catalog + documents registry helpers.

The frontend reads:
  data/packages/index.json   -> { "packages": [ {id,title,file,description,questions} ] }
  data/documents/index.json  -> { "documents": [ {id,title,file,kind,uploaded_at,...} ] }

These helpers keep those indexes in sync with what's on disk.
"""
import json, os, glob

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_DIR = os.path.join(ROOT, "data", "packages")
DOC_DIR = os.path.join(ROOT, "data", "documents")

def rebuild_package_index(pkg_dir=PKG_DIR):
    """Scan package JSONs and (re)write the Catalog index."""
    pkgs = []
    for f in sorted(glob.glob(os.path.join(pkg_dir, "*.json"))):
        if os.path.basename(f) == "index.json":
            continue
        try:
            p = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        pkgs.append({
            "id": p.get("id"),
            "title": p.get("title"),
            "file": os.path.basename(f),
            "description": p.get("description", ""),
            "questions": len(p.get("questions", [])),
        })
    os.makedirs(pkg_dir, exist_ok=True)
    json.dump({"packages": pkgs}, open(os.path.join(pkg_dir, "index.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    return pkgs

def _doc_index_path(): return os.path.join(DOC_DIR, "index.json")

def read_documents():
    try:
        return json.load(open(_doc_index_path(), encoding="utf-8")).get("documents", [])
    except Exception:
        return []

def upsert_document(record):
    """Add/replace a document record in the documents registry (keyed by id)."""
    os.makedirs(DOC_DIR, exist_ok=True)
    docs = [d for d in read_documents() if d.get("id") != record["id"]]
    docs.append(record)
    json.dump({"documents": docs}, open(_doc_index_path(), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    return docs

if __name__ == "__main__":
    print("packages:", rebuild_package_index())
