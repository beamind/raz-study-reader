"""Synthetic regressions for title cross-checking and ASR-to-lesson alignment."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def load(name):
    path = Path(__file__).resolve().parents[1] / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


titles = load("reconcile_book_titles")
aligner = load("align_asr_to_lessons")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def lesson(book_id, title, page, source_text):
    page_id = f"{book_id}-p{page:03d}"
    unit = f"{page_id}-u01"
    meta = {
        "schema_version": "raz-lesson/1.0", "book_id": book_id, "book_title": title,
        "raz_level": "P", "page_id": page_id, "pdf_page_number": page,
        "paper_page_label": None, "revision": 1, "generation_status": "draft",
        "is_demo": False, "issues": [],
        "source_units": [{"id": unit, "kind": "sentence", "text": source_text}],
    }
    return f'''<!-- raz-lesson
{json.dumps(meta)}
-->

<!-- lesson-block: {page_id}-s01 -->
<!-- lesson-kind: sentence -->
<!-- source-refs: {unit} -->
## Read

**原文：** {source_text}
'''


def lesson_with_units(book_id, title, page, source_texts):
    page_id = f"{book_id}-p{page:03d}"
    units = [{"id": f"{page_id}-u{index:02d}", "kind": "sentence", "text": text}
             for index, text in enumerate(source_texts, 1)]
    meta = {
        "schema_version": "raz-lesson/1.0", "book_id": book_id, "book_title": title,
        "raz_level": "P", "page_id": page_id, "pdf_page_number": page,
        "paper_page_label": str(page), "revision": 1, "generation_status": "draft",
        "is_demo": False, "issues": [], "source_units": units,
    }
    blocks = []
    for index, unit in enumerate(units, 1):
        blocks.append(f'''<!-- lesson-block: {page_id}-s{index:02d} -->
<!-- lesson-kind: sentence -->
<!-- source-refs: {unit["id"]} -->
## Read {index}

**原文：** {unit["text"]}
''')
    return f'''<!-- raz-lesson
{json.dumps(meta)}
-->

''' + "\n".join(blocks)


class TitleReconciliationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.split = self.root / "split"
        self.asr = self.root / "asr"
        self.refs = self.root / "refs"
        self.refs.mkdir()
        write_json(self.split / "index.json", {"source_sha256": "abc123"})

    def add_book(self, index, folder, current, spoken, slot):
        manifest = {"book_index": index, "title": current, "whole_book_slot": slot,
                    "files": [{"file": f"page-01-oid-{slot}.mp3", "role": "page",
                               "sequence": 1, "slot": slot, "candidate_oid": slot,
                               "seconds": 3, "paper_page": None}]}
        write_json(self.split / "books" / folder / "manifest.json", manifest)
        write_json(self.asr / "books" / folder / f"page-01-oid-{slot}.asr.json",
                   {"text": spoken})

    def test_first_page_corrects_wrong_duration_title_without_renaming_source(self):
        self.add_book(1, "001 Wrong Name", "Wrong Name", "Garden Robots, written by Sample Author.", 8)
        (self.refs / "Garden Robots.pdf").write_bytes(b"")
        report = titles.reconcile(self.split, self.asr, [self.refs], "raz-p-test", .62, .15)
        book = report["books"][0]
        self.assertEqual(book["canonical_title"], "Garden Robots")
        self.assertEqual(book["title_status"], "cross_checked")
        self.assertEqual(book["book_id"], "raz-p-test-b001-s0008")
        self.assertEqual(book["source_folder"], "001 Wrong Name")
        self.assertFalse(book["current_title_matches"])

    def test_clear_asr_typo_can_match_but_unknown_title_stays_unverified(self):
        self.add_book(1, "one", None, "A clockwerk adventure, written by Lee.", 8)
        self.add_book(2, "two", None, "Milo's Lanterns, written by Lee.", 20)
        (self.refs / "Clockwork Adventure.pdf").write_bytes(b"")
        report = titles.reconcile(self.split, self.asr, [self.refs], "raz-p-test", .62, .15)
        self.assertEqual(report["books"][0]["canonical_title"], "Clockwork Adventure")
        self.assertEqual(report["books"][0]["title_status"], "cross_checked")
        self.assertEqual(report["books"][1]["title_status"], "asr_observed_needs_review")
        self.assertEqual(report["books"][1]["canonical_title"], "Milo's Lanterns")

    def test_body_text_is_not_mistaken_for_a_title(self):
        body = ("Now, Mr. Palmer said. Let us sit beside the window and make a plan. "
                "Which small project would you like to finish before next Friday?")
        self.add_book(1, "one", None, body, 8)
        (self.refs / "Some Other Book.pdf").write_bytes(b"")
        report = titles.reconcile(self.split, self.asr, [self.refs], "raz-p-test", .62, .15)
        book = report["books"][0]
        self.assertIsNone(book["canonical_title"])
        self.assertEqual(book["title_status"], "missing_title_page_needs_review")

    def test_two_books_claiming_the_same_title_are_both_left_for_review(self):
        self.add_book(1, "one", None, "Garden Robots, written by One.", 8)
        self.add_book(2, "two", None, "Garden Robots, written by Two.", 20)
        (self.refs / "Garden Robots.pdf").write_bytes(b"")
        report = titles.reconcile(self.split, self.asr, [self.refs], "raz-p-test", .62, .15)
        self.assertEqual([book["title_status"] for book in report["books"]],
                         ["asr_observed_needs_review", "asr_observed_needs_review"])

    def test_reviewed_override_is_separate_and_traceable(self):
        self.add_book(1, "one", None, "unclear recording", 8)
        (self.refs / "Other.pdf").write_bytes(b"")
        overrides = {"schema_version": "raz-book-title-overrides/1.0", "books": [{
            "book_id": "raz-p-test-b001-s0008", "canonical_title": "Reviewed Title",
            "review_method": "human_review", "reviewed_by": "parent",
            "reviewed_at": "2026-09-09", "note": "checked cover",
        }]}
        report = titles.reconcile(self.split, self.asr, [self.refs], "raz-p-test",
                                  .62, .15, overrides)
        book = report["books"][0]
        self.assertEqual(book["title_status"], "human_reviewed")
        self.assertEqual(book["canonical_title"], "Reviewed Title")
        self.assertEqual(report["summary"]["human_reviewed"], 1)
        self.assertEqual(report["summary"]["needs_review"], 0)

    def test_partial_run_only_requires_asr_for_selected_book_folders(self):
        self.add_book(71, "book-071-slot-1000", None, "Whales", 1000)
        manifest = {"book_index": 72, "title": None, "whole_book_slot": 1100,
                    "files": [{"file": "page-01.mp3", "role": "page",
                               "sequence": 1, "slot": 1101, "candidate_oid": 1101,
                               "seconds": 3, "paper_page": None}]}
        write_json(self.split / "books/book-072-slot-1100/manifest.json", manifest)
        (self.refs / "Whales.pdf").write_bytes(b"")
        report = titles.reconcile(self.split, self.asr, [self.refs], "raz-o-test",
                                  .62, .15, book_patterns=["book-071-*"])
        self.assertEqual([book["book_index"] for book in report["books"]], [71])


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.asr = self.root / "asr"
        self.lessons = self.root / "lessons"
        self.title_map = self.root / "titles.json"
        self.book = {"book_id": "raz-p-test-b001-s0008", "source_folder": "001 Wrong",
                     "canonical_title": "About Trees", "current_title": "Wrong"}
        write_json(self.title_map, {"books": [self.book]})

    def add_asr(self, sequence, text, oid):
        write_json(self.asr / "books" / "001 Wrong" / f"page-{sequence:02d}.asr.json", {
            "status": "ok", "source_audio": f"books/001 Wrong/page-{sequence:02d}.mp3",
            "source_audio_sha256": f"audio-{sequence}", "text_sha256": f"text-{sequence}",
            "text": text, "book": {"folder": "001 Wrong"},
            "location": {"role": "page", "sequence": sequence, "slot": oid,
                         "candidate_oid": oid, "paper_page": None},
        })

    def test_strong_page_match_emits_reviewable_candidate_with_targets(self):
        text = "Robots carry tiny buckets. Each wheel has a green stripe."
        self.add_asr(1, text, 8137)
        path = self.lessons / "page.md"
        path.parent.mkdir()
        path.write_text(lesson(self.book["book_id"], "About Trees", 3, text), encoding="utf-8")
        report = aligner.align(self.asr, self.title_map, [self.lessons], .72, .08)
        binding = report["bindings"][0]
        self.assertEqual(binding["alignment_status"], "candidate")
        self.assertTrue(binding["review_required"])
        self.assertEqual(binding["candidate_oid"], "8137")
        self.assertEqual(binding["suggested_target"]["page_id"],
                         "raz-p-test-b001-s0008-p003")
        self.assertEqual(binding["suggested_target"]["lesson_block_ids"],
                         ["raz-p-test-b001-s0008-p003-s01"])

    def test_duplicate_page_text_remains_needs_review(self):
        text = "The same short sentence appears here."
        self.add_asr(1, text, 9000)
        self.lessons.mkdir()
        for page in (3, 4):
            (self.lessons / f"p{page}.md").write_text(
                lesson(self.book["book_id"], "About Trees", page, text), encoding="utf-8")
        report = aligner.align(self.asr, self.title_map, [self.lessons], .72, .08)
        binding = report["bindings"][0]
        self.assertEqual(binding["alignment_status"], "needs_review")
        self.assertEqual(binding["reason"], "weak_or_ambiguous_page_match")
        self.assertEqual(binding["score_margin"], 0.0)

    def test_book_without_lessons_is_preserved_as_unmatched(self):
        self.add_asr(1, "Some text.", 9001)
        other = self.lessons / "other.md"
        other.parent.mkdir()
        other.write_text(lesson("different-book", "Different", 1, "Some text."), encoding="utf-8")
        report = aligner.align(self.asr, self.title_map, [other], .72, .08)
        binding = report["bindings"][0]
        self.assertEqual(binding["reason"], "no_lessons_for_book")
        self.assertIsNone(binding["suggested_target"])
        self.assertEqual(report["summary"]["unmatched_lesson_books"], 1)

    def test_stale_current_title_does_not_shadow_reconciled_canonical_title(self):
        stale = {"book_id": "raz-p-test-b001-s0008", "source_folder": "001 Target",
                 "canonical_title": "Different Book", "current_title": "Target Book",
                 "current_title_matches": False}
        actual = {"book_id": "raz-p-test-b002-s0020", "source_folder": "book-002",
                  "canonical_title": "Target Book", "current_title": None,
                  "current_title_matches": False}
        write_json(self.title_map, {"books": [stale, actual]})
        write_json(self.asr / "books" / "book-002" / "page-01.asr.json", {
            "status": "ok", "source_audio": "books/book-002/page-01.mp3",
            "source_audio_sha256": "audio", "text_sha256": "text",
            "text": "This is the exact page text.", "book": {"folder": "book-002"},
            "location": {"role": "page", "sequence": 1, "slot": 20,
                         "candidate_oid": 9002, "paper_page": None},
        })
        path = self.lessons / "page.md"
        path.parent.mkdir()
        path.write_text(lesson("authoring-book", "Target Book", 1,
                               "This is the exact page text."), encoding="utf-8")
        report = aligner.align(self.asr, self.title_map, [self.lessons], .72, .08)
        binding = report["bindings"][0]
        self.assertEqual(binding["book_id"], actual["book_id"])
        self.assertEqual(binding["alignment_status"], "candidate")
        self.assertEqual(report["summary"]["unmatched_lesson_books"], 0)

    def test_unmatched_lesson_summary_counts_books_instead_of_pages(self):
        self.lessons.mkdir()
        for page in (1, 2):
            (self.lessons / f"p{page}.md").write_text(
                lesson("missing-book", "Missing", page, f"Page {page}."), encoding="utf-8")
        report = aligner.align(self.asr, self.title_map, [self.lessons], .72, .08)
        self.assertEqual(report["summary"]["unmatched_lesson_books"], 1)
        self.assertEqual(report["unmatched_lesson_books"][0]["lesson_pages"], 2)

    def test_separate_audio_entries_can_target_different_spans_on_one_page(self):
        first = "The first half explains how cars were invented."
        second = "Do you know that automobile was once a French word?"
        self.add_asr(1, first, 9101)
        self.add_asr(2, second, 9102)
        path = self.lessons / "page.md"
        path.parent.mkdir()
        path.write_text(lesson_with_units(self.book["book_id"], "About Trees", 6,
                                          [first, second]), encoding="utf-8")
        report = aligner.align(self.asr, self.title_map, [self.lessons], .72, .08)
        one, two = report["bindings"]
        self.assertEqual(one["alignment_status"], "candidate")
        self.assertEqual(two["alignment_status"], "candidate")
        self.assertEqual(one["suggested_target"]["source_unit_ids"],
                         [f"{self.book['book_id']}-p006-u01"])
        self.assertEqual(two["suggested_target"]["source_unit_ids"],
                         [f"{self.book['book_id']}-p006-u02"])
        self.assertEqual(one["suggested_target"]["lesson_block_ids"],
                         [f"{self.book['book_id']}-p006-s01"])

    def test_typographic_apostrophe_still_names_the_same_book(self):
        text = "The nest was empty this morning."
        self.add_asr(1, text, 9200)
        path = self.lessons / "page.md"
        path.parent.mkdir()
        write_json(self.title_map, {"books": [{**self.book, "canonical_title": "The Bird's Nest"}]})
        path.write_text(lesson("authoring-book", "The Bird\u2019s Nest", 1, text), encoding="utf-8")
        report = aligner.align(self.asr, self.title_map, [self.lessons], .72, .08)
        self.assertEqual(report["summary"]["unmatched_lesson_books"], 0)
        self.assertEqual(report["bindings"][0]["alignment_status"], "candidate")

    def test_character_score_recovers_compound_and_proper_name_asr_errors(self):
        spoken = ("some tiny garden robots and badges in 2041 Milo makes the Milo mini "
                  "and Moon-Wagon Norva makes Tulpa and Sunleaf Elara makes "
                  "the Elara anacorn Nova makes Nova and Starbrite")
        source = ("Some Tiny Gardenrobots and Badges in 2041 Milo makes the Milo MINI "
                  "and Moon-Wagon Norva makes Tulpa and Sunleaf Elara makes "
                  "the Elara and Acorn Nova makes Nova and Starbright")
        metrics = aligner.similarity(spoken, source)
        self.assertGreaterEqual(metrics["score"], .72)
        self.assertIn("character", metrics)


if __name__ == "__main__":
    unittest.main()
