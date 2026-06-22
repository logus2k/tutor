#!/usr/bin/env python3
"""Clean docling's raw Markdown into the structured form the segmenter expects.

docling emits every heading as `##`, leaves "Domain N:" as plain text, repeats the
page footer/promo, and serialises code imperfectly. This pass:
  * strips page furniture (SKILLCERTPRO / page numbers / promo),
  * rebuilds the heading hierarchy from the section numbering
    (title -> #, Domain -> ##, x.y -> ###, x.y.z -> ####, other labels -> #####),
  * nests `- o` sub-bullets,
  * escapes loose code-comment lines so they don't read as headings,
  * unescapes HTML entities and tidies whitespace.
Code fences docling already balanced are left intact.
"""
import re, sys

PROMO = ["we have 330 practice test questions", "from previous exams",
         "full practice set link", "skillcertpro.com"]

def _is_noise(s):
    t = s.strip().lstrip("#").strip()
    if re.fullmatch(r"SKIL+CERTPRO", t, re.I): return True
    if re.fullmatch(r"pg\.\s*\d+", t, re.I): return True
    low = t.lower()
    return any(p in low for p in PROMO)

def clean(raw):
    lines = [l for l in raw.split("\n") if not _is_noise(l)]
    out, title_done, in_fence = [], False, False
    for l in lines:
        if l.startswith("```"):
            in_fence = not in_fence; out.append(l); continue
        if in_fence:
            out.append(l); continue
        if re.match(r"^- o\s+", l):
            out.append("  - " + re.sub(r"^- o\s+", "", l).strip()); continue
        if re.match(r"^Domain \d+\s*:", l.strip()):
            out.append("## " + l.strip()); continue
        if l.startswith("## "):
            c = l[3:].strip()
            if not title_done and ("Master Cheat Sheet" in c or c.startswith("Microsoft Certified")):
                out.append("# " + c); title_done = True
            elif re.match(r"^\d+\.\d+\.\d+(\s|$)", c): out.append("#### " + c)
            elif re.match(r"^\d+\.\d+(\s|$)", c):      out.append("### " + c)
            elif re.match(r"^Domain \d+\s*:", c):      out.append("## " + c)
            else:                                      out.append("##### " + c)
            continue
        if title_done and re.match(r"^#{1,4} ", l):    # loose code-comment heading
            out.append("`" + l.strip() + "`"); continue
        out.append(l)

    text = "\n".join(out)
    text = re.sub(r"```[a-zA-Z]*\n\s*```\n", "", text)        # drop empty code fences
    text = re.sub(r" +:", ":", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"

def clean_file(src, dst):
    open(dst, "w", encoding="utf-8").write(clean(open(src, encoding="utf-8").read()))

if __name__ == "__main__":
    clean_file(sys.argv[1], sys.argv[2]); print("cleaned ->", sys.argv[2])
