#!/usr/bin/env python3
"""Extraction: PDF -> cleaned Markdown + DoclingDocument JSON.

Two modes (ETL_DOCLING_MODE):
  * "local" (in the tutor image): run the docling CLI directly in this container.
  * "exec"  (host dev): run docling inside the noted-graph container via `docker exec`.
Both use accurate tables + code/formula enrichment and the shared models, then run
the markdown cleanup. Returns (md_path, json_path).
"""
import subprocess, os
from etl.clean_markdown import clean_file

MODE = os.environ.get("ETL_DOCLING_MODE", "exec")
CONTAINER = os.environ.get("DOCLING_CONTAINER", "noted-graph")
MODELS = os.environ.get("DOCLING_MODELS", "/data/models/docling/models")
DEVICE = os.environ.get("ETL_DOCLING_DEVICE", "auto")

_DOCLING_ARGS = ["--from", "pdf", "--to", "md", "--to", "json", "--no-ocr",
                 "--table-mode", "accurate", "--image-export-mode", "placeholder",
                 "--enrich-code", "--enrich-formula"]

def _finish(raw_md, js, workdir, emit):
    md = os.path.join(workdir, "doc.md")
    clean_file(raw_md, md)
    emit("extract.progress", detail="docling complete")
    return md, js

def _extract_local(pdf_path, workdir, emit):
    out = os.path.join(workdir, "out"); os.makedirs(out, exist_ok=True)
    emit("extract.progress", detail="docling started")
    cmd = ["docling", pdf_path, *_DOCLING_ARGS,
           "--artifacts-path", MODELS, "--device", DEVICE, "--output", out]
    r = subprocess.run(cmd, env={**os.environ, "DOCLING_ARTIFACTS_PATH": MODELS},
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("docling failed: " + (r.stderr or "")[-600:])
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    return _finish(os.path.join(out, base + ".md"), os.path.join(out, base + ".json"), workdir, emit)

def _extract_exec(pdf_path, workdir, emit):
    def dk(*a, **k): return subprocess.run(["docker", *a], text=True, **k)
    cdir = "/tmp/etl_extract/" + os.path.splitext(os.path.basename(pdf_path))[0]
    dk("exec", CONTAINER, "sh", "-c", f"rm -rf {cdir} && mkdir -p {cdir}/out", check=True)
    dk("cp", pdf_path, f"{CONTAINER}:{cdir}/in.pdf", check=True)
    emit("extract.progress", detail="docling started")
    cmd = (f"cd {cdir} && DOCLING_ARTIFACTS_PATH={MODELS} docling in.pdf "
           + " ".join(_DOCLING_ARGS)
           + f" --artifacts-path {MODELS} --device {DEVICE} --output {cdir}/out")
    r = dk("exec", CONTAINER, "sh", "-c", cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("docling failed: " + (r.stderr or "")[-600:])
    raw_md = os.path.join(workdir, "raw.md"); js = os.path.join(workdir, "doc.json")
    dk("cp", f"{CONTAINER}:{cdir}/out/in.md", raw_md, check=True)
    dk("cp", f"{CONTAINER}:{cdir}/out/in.json", js, check=True)
    return _finish(raw_md, js, workdir, emit)

def extract(pdf_path, workdir, emit=lambda *a, **k: None):
    os.makedirs(workdir, exist_ok=True)
    return (_extract_local if MODE == "local" else _extract_exec)(pdf_path, workdir, emit)

if __name__ == "__main__":
    import sys
    print(extract(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/tmp/etl_extract_out",
                  emit=lambda e, **k: print(e, k)))
