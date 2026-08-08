"""Q&A import: parsing, fidelity and the imported-vs-authored boundary.

Every case here is a REAL failure observed on real documents, not an invented input.
The CCA workshop PDFs (5 modules x 90 curated MCQs) went through this pipeline on
2026-08-08 and exposed five defects; each one is pinned below so it cannot come back.

The through-line: a question IMPORTED verbatim from a Q&A document was written by a
human author. Machinery built to police the local model's own output — dedup, the
meta-reference scrub, the package judge — must not be pointed at it.
"""
import pytest

from etl.orchestrator import (
    source_level_bands,
    parse_qa, _clean_qa_text, _q_ordinal, _ans_letters, source_answer_map,
    import_difficulty, sanitize_question, dedup_and_order, dedup_questions,
    _OPT_RE, _ANS_RE,
)


def mcq(stem, options, correct_idx, tags=("imported",), **kw):
    """A question in package shape. correct_idx is the 0-based key."""
    q = {"id": kw.get("id", "q-0001"), "concept_ids": ["c1"], "type": "mcq_single",
         "render": "radio", "difficulty": 2, "bloom": "recall", "stem": stem,
         "options": [{"id": chr(ord("a") + i), "text": t, "correct": i == correct_idx,
                      "rationale": ""} for i, t in enumerate(options)],
         "explanation": "", "hints": [], "source_refs": [], "tags": list(tags)}
    q.update({k: v for k, v in kw.items() if k != "id"})
    return q


# --------------------------------------------------------------- answer-line matching
class TestAnswerLine:
    """_ANS_RE. docling renders "■ Correct Answer: C" as "##### n Correct Answer: C":
    the glyph becomes a stray token and "Correct" sits before "Answer". Neither matched,
    so parse_qa found 0 questions in a 90-question document — which silently reclassified
    it as prose and would have AUTHORED ~450 replacements for curated content."""

    @pytest.mark.parametrize("line,expected", [
        ("Answer: C", "c"),
        ("##### Answer: c", "c"),
        ("Answer - b", "b"),
        ("Answer: a, c", "a, c"),                 # multi-select
        ("##### n Correct Answer: C", "c"),       # the CCA rendering
        ("■ Correct Answer: B", "b"),             # the glyph itself, if it survives
        ("Correct Answer: D", "d"),
    ])
    def test_accepts(self, line, expected):
        m = _ANS_RE.match(line)
        assert m is not None, f"should match: {line!r}"
        assert m.group(1).lower() == expected

    @pytest.mark.parametrize("line", [
        "The Answer is unclear",
        "Answering questions: yes",
        "Answer: not a letter",
    ])
    def test_rejects(self, line):
        assert _ANS_RE.match(line) is None, f"should NOT match: {line!r}"

    def test_option_forms(self):
        for line in ["   A) Yes indeed", "- a. Foo", "B) Something", "        C) indented"]:
            assert _OPT_RE.match(line), f"should match option: {line!r}"


# --------------------------------------------------------------- option-run repair
class TestOptionRuns:
    """Converters break an option run in three ways, each of which silently dropped
    whole questions from the CCA modules."""

    def test_plain_block(self):
        md = "\n".join(["##### Q1. What is a loop?", "", "- A) One", "- B) Two",
                        "- C) Three", "- D) Four", "", "##### n Correct Answer: C"])
        out = parse_qa(md)
        assert len(out) == 1
        assert out[0]["stem"] == "What is a loop?"
        assert [o["id"] for o in out[0]["options"] if o["correct"]] == ["c"]

    def test_wrapped_option_with_injected_list_marker(self):
        """Q17/Q24, module 01: a long option wraps and the converter prefixes the
        continuation with an ordered-list marker ("3. of parent or sibling context"),
        which ended the option run and dropped the question."""
        md = "\n".join([
            "##### Q17. What does isolated context mean?", "",
            "- A) Separate process",
            "- B) Each subagent starts with only what its prompt provides - no inherita",
            "3. of parent or sibling agent context",
            "- C) Tools marked isolated",
            "- D) Encrypted outputs", "",
            "##### n Correct Answer: B"])
        out = parse_qa(md)
        assert len(out) == 1
        opts = out[0]["options"]
        assert len(opts) == 4, "the wrapped line must rejoin B, not split the run"
        assert "no inherita of parent or sibling agent context" in opts[1]["text"]
        assert opts[1]["correct"]

    def test_wrapped_option_across_blank_lines(self):
        """Q63 module 02 / Q80 module 04 / Q79 module 05: the continuation is separated
        from its option by blank lines on BOTH sides."""
        md = "\n".join([
            "##### Q63. Rate limiting?", "",
            "- A) Let it fail",
            "- B) Token bucket in the wrapper; return isError with errorCategory",
            "",
            "'transient', retry_after: seconds when rate limited",
            "",
            "- C) Ask nicely in the description",
            "- D) Unbounded queue", "",
            "##### n Correct Answer: B"])
        out = parse_qa(md)
        assert len(out) == 1
        assert len(out[0]["options"]) == 4
        assert "retry_after" in out[0]["options"][1]["text"]

    def test_fenced_options_with_one_stranded_outside(self):
        """Q32 module 03: code-like options get wrapped in a fence, stranding D outside.
        The stem lookup also landed on the fence line and came back empty."""
        md = "\n".join([
            "Q32. Which paths frontmatter applies to every Terraform file?", "",
            "```",
            "A) paths: ['terraform/']",
            "        B) paths: ['**/*.tf', 'terraform/**/*']",
            "        C) paths: ['*.tf']",
            "```", "",
            "- D) No frontmatter needed", "",
            "##### n Correct Answer: B"])
        out = parse_qa(md)
        assert len(out) == 1, "fence must not break the run"
        assert out[0]["stem"].startswith("Which paths frontmatter"), "stem must skip the fence"
        assert len(out[0]["options"]) == 4, "D sits outside the fence and must still be collected"

    def test_all_options_inside_fence(self):
        """Q33 module 03: every option inside the fence. The ANSWER lookup stopped on the
        closing fence, failed to match, and the question was dropped."""
        md = "\n".join([
            "Q33. Which glob set covers the test files?", "",
            "```",
            "A) paths: ['tests/**']",
            "            B) paths: ['**/*.test.ts', 'e2e/**/*']",
            "            C) paths: ['*.test.ts']",
            "            D) A separate CLAUDE.md per directory",
            "```", "",
            "##### n Correct Answer: B"])
        out = parse_qa(md)
        assert len(out) == 1, "answer lookup must skip the closing fence"
        assert [o["id"] for o in out[0]["options"] if o["correct"]] == ["b"]

    def test_does_not_swallow_the_answer_or_the_next_question(self):
        """The look-ahead is bounded on purpose: it must not absorb the answer line or
        run into the following question."""
        md = "\n".join([
            "##### Q1. First?", "", "- A) a", "- B) b", "", "##### n Correct Answer: B", "",
            "Some explanation prose that follows the answer.", "",
            "##### Q2. Second?", "", "- A) c", "- B) d", "", "##### n Correct Answer: A"])
        out = parse_qa(md)
        assert len(out) == 2
        assert [len(q["options"]) for q in out] == [2, 2]
        assert out[0]["stem"] == "First?" and out[1]["stem"] == "Second?"

    def test_open_question_without_options_is_skipped(self):
        md = "\n".join(["##### Q1. Discuss the tradeoffs of agentic loops.", "",
                        "A thoughtful paragraph would go here.", ""])
        assert parse_qa(md) == []


# --------------------------------------------------------------- artefact cleaning
class TestCleaning:
    @pytest.mark.parametrize("raw,expected", [
        ("##### Q17. What is x?", "What is x?"),
        ("Q9. Plain numbered", "Plain numbered"),
        (r"Which stop\_reason value?", "Which stop_reason value?"),
        (r"Escaped \* and \#", "Escaped * and #"),
        ("No decoration here", "No decoration here"),
    ])
    def test_clean_qa_text(self, raw, expected):
        assert _clean_qa_text(raw) == expected

    def test_cleaning_does_not_eat_real_content(self):
        """A stem that legitimately starts with a capital Q word must survive."""
        assert _clean_qa_text("Queries. How are they cached?") == "Queries. How are they cached?"


# --------------------------------------------------------------- ordinals + difficulty
class TestOrdinalAndDifficulty:
    @pytest.mark.parametrize("line,expected", [
        ("##### Q17. What is x?", 17), ("Q9. Foo", 9), ("##### Q90. Z", 90),
        ("Not numbered", None), ("", None), ("Q. Missing number", None),
    ])
    def test_q_ordinal(self, line, expected):
        assert _q_ordinal(line) == expected

    def test_parse_qa_reports_the_ordinal(self):
        md = "\n".join(["##### Q42. Numbered?", "", "- A) a", "- B) b", "", "Answer: B"])
        assert parse_qa(md)[0]["ordinal"] == 42

    @pytest.mark.parametrize("ordinal,expected", [
        (1, (2, "recall")), (30, (2, "recall")),
        (31, (3, "apply")), (60, (3, "apply")),
        (61, (4, "analyze")), (90, (4, "analyze")),
    ])
    def test_banding_matches_the_declared_30_30_30(self, ordinal, expected):
        """The modules declare Beginner Q1-30 / Intermediate Q31-60 / Advanced Q61-90."""
        assert import_difficulty(ordinal, 90) == expected

    def test_unnumbered_keeps_the_flat_default(self):
        assert import_difficulty(None, 90) == (2, "recall")
        assert import_difficulty(5, 0) == (2, "recall")

    def test_banding_covers_the_whole_range(self):
        got = {import_difficulty(i, 90)[0] for i in range(1, 91)}
        assert got == {2, 3, 4}, "every question must land in a band"


# --------------------------------------------------------------- key fidelity
class TestSourceAnswerMap:
    """source_answer_map is the INDEPENDENT check. It walks question numbers, while
    parse_qa walks option runs — so a mis-associated answer line disagrees between the
    two instead of being reproduced by both."""

    def test_maps_number_to_letters(self):
        md = "\n".join(["##### Q1. First?", "- A) a", "- B) b", "##### n Correct Answer: B", "",
                        "##### Q2. Second?", "- A) c", "- B) d", "Answer: A"])
        assert source_answer_map(md) == {1: {"b"}, 2: {"a"}}

    def test_multi_select(self):
        md = "\n".join(["##### Q7. Pick two", "- A) a", "- B) b", "- C) c", "Answer: a, c"])
        assert source_answer_map(md) == {7: {"a", "c"}}

    def test_agrees_with_parse_qa_on_a_clean_document(self):
        md = "\n".join(["##### Q1. First?", "", "- A) a", "- B) b", "", "##### n Correct Answer: B", "",
                        "##### Q2. Second?", "", "- A) c", "- B) d", "", "##### n Correct Answer: A"])
        smap = source_answer_map(md)
        for q in parse_qa(md):
            got = {chr(ord("a") + i) for i, o in enumerate(q["options"]) if o["correct"]}
            assert got == smap[q["ordinal"]], f"key mismatch on Q{q['ordinal']}"

    def test_detects_a_misassociated_key(self):
        """If parse_qa ever attaches the wrong answer, the two disagree — which is the
        whole point of keeping the second scan structurally different."""
        md = "\n".join(["##### Q1. First?", "", "- A) a", "- B) b", "", "Answer: B"])
        smap = source_answer_map(md)
        parsed = parse_qa(md)[0]
        wrong = {"a"}
        assert wrong != smap[parsed["ordinal"]]

    @pytest.mark.parametrize("letters,expected", [
        ("a, c", {"a", "c"}), ("A/B", {"a", "b"}), ("b", {"b"}), ("a & d", {"a", "d"}),
    ])
    def test_ans_letters(self, letters, expected):
        assert _ans_letters(letters) == expected


# --------------------------------------------------------------- verbatim boundary
class TestVerbatimImport:
    """Three separate filters were rewriting or discarding curated content."""

    def test_scrub_damages_a_filename_and_verbatim_prevents_it(self):
        """The meta scrub's "drop space before punctuation" rule turned
        "expansion in .mcp.json" into "in.mcp.json" on three CCA stems."""
        stem = "Why use env var expansion in .mcp.json instead of hardcoding tokens?"
        kept = sanitize_question(mcq(stem, ["x", "y"], 0), verbatim=True)
        assert kept["stem"] == stem, "an imported stem must survive untouched"
        scrubbed = sanitize_question(mcq(stem, ["x", "y"], 0))
        assert scrubbed["stem"] != stem, "the authored path still scrubs (regression guard)"

    def test_sanitize_still_rejects_broken_questions(self):
        assert sanitize_question(mcq("No key", ["a", "b"], -1), verbatim=True) is None
        assert sanitize_question(mcq("One option", ["only"], 0), verbatim=True) is None
        allc = mcq("All correct", ["a", "b"], 0)
        for o in allc["options"]:
            o["correct"] = True
        assert sanitize_question(allc, verbatim=True) is None

    def test_llm_dedup_never_touches_imported(self, monkeypatch):
        """package dedup collapsed a curated bank 90 -> 63, dropping "What is an agentic
        loop?" and "What is a PreToolUse hook used for?" — not duplicates of anything."""
        import etl.orchestrator as o
        monkeypatch.setattr(o, "semantic_dedup", lambda qs, emit=None: qs[:1])
        imported = [mcq(f"Imported {i}", ["a", "b"], 0, id=f"i{i}") for i in range(5)]
        authored = [mcq(f"Authored {i}", ["a", "b"], 0, tags=(), id=f"a{i}",
                        generated_by="model") for i in range(3)]
        out = o.dedup_and_order(imported + authored)
        assert len([q for q in out if not q.get("generated_by")]) == 5, "imported must all survive"
        assert len([q for q in out if q.get("generated_by")]) == 1, "authored still deduped"

    def test_token_dedup_would_drop_a_contrast_pair(self):
        """Glob vs Grep scores 0.818 on token Jaccard — above the 0.72 threshold — so the
        two questions teaching the CONTRAST collapse into one. This documents WHY the
        import path must not run token dedup."""
        pair = [mcq("What is the Glob built-in tool best used for?", ["a", "b"], 0, id="q1"),
                mcq("What is the Grep built-in tool best used for?", ["a", "b"], 0, id="q2")]
        kept, dropped = dedup_questions(pair, threshold=0.72)
        assert dropped == 1, "token similarity cannot tell these apart — hence imported-only"
        kept_all, dropped_none = dedup_questions(pair, threshold=0.95)
        assert dropped_none == 0

    def test_dedup_and_order_keeps_both_of_the_contrast_pair(self):
        """The end-to-end guarantee: tagged imported, both survive."""
        pair = [mcq("What is the Glob built-in tool best used for?", ["a", "b"], 0, id="q1"),
                mcq("What is the Grep built-in tool best used for?", ["a", "b"], 0, id="q2")]
        out = dedup_and_order(pair)
        assert len(out) == 2
        assert {q["id"] for q in out} == {"q1", "q2"}


# --------------------------------------------------------------- end to end
class TestDocumentRoundTrip:
    def test_a_small_document_imports_faithfully(self):
        """Composite: numbered stems, a fenced option block, a wrapped option, mixed
        answer-line renderings — parsed, banded and key-checked in one pass."""
        md = "\n".join([
            "##### Q1. What is an agentic loop?", "", "- A) A for loop", "- B) Call, check stop_reason, repeat",
            "", "##### n Correct Answer: B", "",
            "##### Q2. Which stop\\_reason means a tool call?", "", "- A) 'tool\\_call'", "- B) 'tool\\_use'",
            "", "Answer: B", "",
            "##### Q3. Which paths value?", "", "```", "A) paths: ['a/**']", "        B) paths: ['**/*.tf']",
            "```", "", "- C) None needed", "", "##### n Correct Answer: B"])
        out = parse_qa(md)
        assert len(out) == 3, "all three shapes must parse"
        assert [q["ordinal"] for q in out] == [1, 2, 3]
        assert out[1]["stem"] == "Which stop_reason means a tool call?", "escapes cleaned"
        assert len(out[2]["options"]) == 3, "fenced + stranded option collected"

        smap = source_answer_map(md)
        for q in out:
            got = {chr(ord("a") + i) for i, o in enumerate(q["options"]) if o["correct"]}
            assert got == smap[q["ordinal"]], f"Q{q['ordinal']} key must match the source"

        omax = max(q["ordinal"] for q in out)
        assert import_difficulty(out[0]["ordinal"], omax) == (2, "recall")
        assert import_difficulty(out[-1]["ordinal"], omax) == (4, "analyze")


# --------------------------------------------------------------- real documents
@pytest.mark.live
class TestRealDocuments:
    """Excluded by default (`-m "not live"`). These run against real artefacts:

        TUTOR_TEST_PDF=/path/to/exam.pdf pytest -m live

    Synthetic fixtures pin the failure modes we already know about. These catch the
    ones we do not — a converter change, a new document shape — which is precisely
    how all five defects of 2026-08-08 were found in the first place.
    """

    def test_every_numbered_question_imports_with_the_right_key(self):
        import os
        pdf = os.environ.get("TUTOR_TEST_PDF")
        if not pdf or not os.path.exists(pdf):
            pytest.skip("set TUTOR_TEST_PDF to a numbered Q&A PDF")
        from etl import extract as extract_mod
        try:
            md_path, _ = extract_mod.extract(pdf, "/tmp/_qa_test", lambda *a, **k: None)
        except FileNotFoundError:                       # docling is a CLI in the image
            pytest.skip("docling not available here — run inside the tutor container")
        src = open(md_path).read()

        declared = {n for n in (_q_ordinal(l.strip()) for l in src.split("\n")) if n}
        parsed = parse_qa(src)
        got = {q["ordinal"] for q in parsed if q.get("ordinal")}
        assert declared, "no numbered questions found — is this a Q&A document?"
        assert declared - got == set(), f"questions declared but not imported: {sorted(declared - got)}"

        smap = source_answer_map(src)
        for q in parsed:
            if not q.get("ordinal") or q["ordinal"] not in smap:
                continue
            key = {chr(ord("a") + i) for i, o in enumerate(q["options"]) if o["correct"]}
            assert key == smap[q["ordinal"]], f"Q{q['ordinal']}: parsed {sorted(key)} vs source {sorted(smap[q['ordinal']])}"

        counts = {len(q["options"]) for q in parsed}
        assert min(counts) >= 2, "every imported question needs at least two options"

    def test_published_imported_packages_hold_their_invariants(self):
        import os, json, glob
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "packages")
        files = sorted(glob.glob(os.path.join(root, "*.json")))
        files = [f for f in files if not f.endswith("index.json")]
        if not files:
            pytest.skip("no published packages")
        checked = 0
        for f in files:
            pkg = json.load(open(f))
            qs = pkg.get("questions") or []
            if not qs or not all("imported" in (q.get("tags") or []) for q in qs):
                continue                                   # authored/mixed: different rules
            checked += 1
            ids = [q["id"] for q in qs]
            assert len(ids) == len(set(ids)), f"{pkg.get('id')}: duplicate question ids"
            for q in qs:
                assert q.get("stem", "").strip(), f"{pkg.get('id')}/{q['id']}: empty stem"
                assert len(q.get("options") or []) >= 2, f"{pkg.get('id')}/{q['id']}: too few options"
                assert any(o.get("correct") for o in q["options"]), f"{pkg.get('id')}/{q['id']}: no key"
                assert not q.get("generated_by"), f"{pkg.get('id')}/{q['id']}: tagged imported but authored"
        if not checked:
            pytest.skip("no fully-imported packages to check")


# --------------------------------------------------------------- declared difficulty bands
class TestDeclaredLevelBands:
    """The documents DECLARE their ramp in section headings. Reading the declaration beats
    inferring thirds — which is right only when the split happens to be even."""

    HEADINGS = "\n".join([
        "CCA FOUNDATIONS WORKSHOP",
        "90 PRACTICE QUESTIONS - 30 BEGINNER . 30 INTERMEDIATE . 30 ADVANCED",
        "Beginner            Q1-Q30      Core concepts and terminology",
        "Intermediate        Q31-Q60     Application scenarios",
        "Advanced            Q61-Q90     Production architecture",
        "Work through each level in order. For Beginner questions, aim for 90%+.",
        "BEGINNER QUESTIONS                    Questions 1-30",
        "INTERMEDIATE QUESTIONS                Questions 31-60",
        "ADVANCED QUESTIONS                    Questions 61-90",
    ])

    def test_reads_the_declared_ranges(self):
        bands = source_level_bands(self.HEADINGS)
        assert bands == {(1, 30): (2, "recall"), (31, 60): (3, "apply"), (61, 90): (4, "analyze")}

    def test_ignores_the_summary_table_and_prose(self):
        """Only the section headings count: the overview row ("Beginner Q1-Q30 ...") and
        the instructions ("For Beginner questions, aim for 90%+") must not register."""
        only_noise = "\n".join(self.HEADINGS.split("\n")[:6])
        assert source_level_bands(only_noise) == {}

    def test_en_dash_ranges(self):
        md = "BEGINNER QUESTIONS   Questions 1–30"
        assert source_level_bands(md) == {(1, 30): (2, "recall")}

    def test_declaration_beats_positional_thirds(self):
        """An UNEVEN split is where the two disagree — and where inferring is wrong."""
        bands = {(1, 20): (2, "recall"), (21, 60): (3, "apply"), (61, 90): (4, "analyze")}
        assert import_difficulty(25, 90, bands) == (3, "apply"), "declared: intermediate"
        assert import_difficulty(25, 90) == (2, "recall"), "thirds alone would say beginner"

    def test_falls_back_to_thirds_when_undeclared(self):
        assert import_difficulty(10, 90, {}) == (2, "recall")
        assert import_difficulty(45, 90, None) == (3, "apply")
        assert import_difficulty(80, 90, {}) == (4, "analyze")

    def test_ordinal_outside_every_declared_band_uses_the_fallback(self):
        bands = {(1, 30): (2, "recall")}
        assert import_difficulty(75, 90, bands) == (4, "analyze")
