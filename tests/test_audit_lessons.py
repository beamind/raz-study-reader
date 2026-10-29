"""Regressions for strict lesson presentation audits."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "audit_lessons.py"
SPEC = importlib.util.spec_from_file_location("audit_lessons_under_test", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class HighlightChecks(unittest.TestCase):
    def test_finds_unmarked_exact_source_phrase(self):
        original = "The truck stopped, making them wait beside the road."
        detail = "making them 在这里表示‘使他们……’。"
        self.assertEqual(audit.unmarked_source_phrases(detail, original), ["making them"])

    def test_finds_unmarked_number_and_single_lowercase_term(self):
        self.assertEqual(audit.unmarked_source_phrases("9,000 就是九千。", "It had 9,000 pieces."), ["9,000"])
        self.assertEqual(audit.unmarked_source_phrases("where 引导地点从句。", "This is where birds rest."), ["where"])

    def test_ignores_concept_note_and_single_proper_name(self):
        original = "Alia hid behind the chair."
        self.assertEqual(audit.unmarked_source_phrases("这个声音听起来很紧急。", original), [])
        self.assertEqual(audit.unmarked_source_phrases("Alia 害怕了，所以躲起来。", original), [])

    def test_does_not_second_guess_an_already_marked_item(self):
        original = "Her first 300-piece puzzle was difficult."
        detail = "`300-piece puzzle` 指一副有 300 片的拼图。"
        self.assertEqual(audit.unmarked_source_phrases(detail, original), [])

    def test_finds_unmarked_half_of_a_partially_highlighted_pattern(self):
        original = "The narrow doorway was too low to cross."
        detail = "`too` ... to ... 表示‘太……而不能……’。"
        self.assertEqual(audit.unmarked_source_phrases(detail, original), ["to"])

    def test_ignores_word_already_covered_by_another_highlight(self):
        original = "Imagine a bright 30-story building beside our workshop."
        detail = "用30层楼作比较。"
        self.assertEqual(
            audit.unmarked_source_phrases(detail, original, ("30-story building",)),
            [],
        )

    def test_existing_highlight_coverage_is_case_insensitive(self):
        original = "My blue mitten vanished! VANISHED!"
        detail = "VANISHED 用大写表示震惊。"
        self.assertEqual(
            audit.unmarked_source_phrases(detail, original, ("vanished",)),
            [],
        )

    def test_ignores_word_explicitly_described_as_absent(self):
        original = "Mira made Pavel try unfamiliar games."
        detail = "`made Pavel try`：make 后接不带 to 的动词。"
        self.assertEqual(audit.unmarked_source_phrases(detail, original), [])

    def test_ignores_reworded_grammar_pattern(self):
        original = "A carved figure stood on each flat side."
        detail = "正常语序是 A figure was at the top。"
        self.assertEqual(audit.unmarked_source_phrases(detail, original), [])

    def test_does_not_match_inside_a_longer_source_token(self):
        original = "A cardboard label lists 18 by 27 inches."
        detail = "inch 是英制长度单位，1 英寸约为 2.5 厘米。"
        self.assertEqual(audit.unmarked_source_phrases(detail, original), [])


class RevisionSourceIdChecks(unittest.TestCase):
    def check_source_ids(self, revision, suffixes):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "_source"
            lessons = source / "lessons"
            lessons.mkdir(parents=True)
            identity = {"id": "raz-q-001", "title": "Example", "level": "Q"}
            (source / "book.json").write_text(json.dumps(identity), encoding="utf-8")
            metadata = {
                "book_id": identity["id"], "book_title": identity["title"],
                "raz_level": "Q", "page_id": "raz-q-001-p001", "revision": revision,
                "source_units": [{"id": suffix if suffix.startswith("raz-") else f"raz-q-001-p001-{suffix}",
                                  "kind": "label", "text": "Example"} for suffix in suffixes],
            }
            path = lessons / "p001.md"
            path.write_text("<!-- raz-lesson\n" + json.dumps(metadata) + "\n-->\n", encoding="utf-8", newline="\n")
            return audit.check_file(path)[0]

    def test_revised_page_retains_ids_after_insertion_and_retirement(self):
        self.assertEqual(self.check_source_ids(2, ["u01", "u04", "u02"]), [])

    def test_first_draft_still_requires_continuous_reading_order_ids(self):
        self.assertTrue(any("expected raz-q-001-p001-u02" in error
                            for error in self.check_source_ids(1, ["u01", "u04", "u02"])))

    def test_revised_page_rejects_duplicate_ids(self):
        self.assertTrue(any("duplicate ID" in error
                            for error in self.check_source_ids(2, ["u01", "u01"])))

    def test_revised_page_rejects_foreign_or_malformed_ids(self):
        for invalid in ["raz-q-001-p002-u01", "u00", "u1", "s01", "uXX"]:
            with self.subTest(invalid=invalid):
                self.assertTrue(any("positive number" in error
                                    for error in self.check_source_ids(2, [invalid])))


if __name__ == "__main__":
    unittest.main()
