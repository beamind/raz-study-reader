"""Regressions for generated lesson structure, without PDF or model dependencies."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_lesson_under_test", ROOT / "tools" / "check_lesson.py")
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)
DEMO = ROOT / "examples" / "demo-red-bird" / "p003.md"


class LessonChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.original = DEMO.read_text(encoding="utf-8")

    def write(self, text, filename="page.md"):
        path = self.directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def with_metadata(self, change):
        header = checker.HEADER.match(self.original)
        metadata = json.loads(header[1])
        change(metadata)
        return "<!-- raz-lesson\n" + json.dumps(metadata, ensure_ascii=False, indent=2) + "\n-->\n" + self.original[header.end():]

    def assert_error(self, text, expected):
        result = checker.check_file(self.write(text))
        self.assertTrue(any(expected in error for error in result.errors), result.errors)
        return result

    def test_demo_passes_as_draft_with_demo_warning_and_is_read_only(self):
        path = self.write(self.original)
        before = path.read_bytes(), path.stat().st_mtime_ns
        result = checker.check_file(path)
        self.assertEqual(result.errors, [])
        self.assertTrue(any("is_demo" in warning for warning in result.warnings))
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_broken_reference_and_changed_original_are_rejected(self):
        self.assert_error(self.original.replace("<!-- source-refs: demo-red-bird-p003-u01 -->",
                                                "<!-- source-refs: other-book-p003-u01 -->", 1), "unknown source reference")
        self.assert_error(self.original.replace("**原文：** A red bird lands", "**原文：** A blue bird lands"),
                          "original text differs")

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        for replacement, error in (("\"revision\": 1, \"revision\": 2", "duplicate JSON key"),
                                   ("\"revision\": NaN", "non-finite JSON value"),
                                   ("\"revision\": Infinity", "non-finite JSON value")):
            with self.subTest(replacement=replacement):
                self.assert_error(self.original.replace('"revision": 1', replacement), error)

    def test_duplicate_source_and_lesson_ids_are_rejected(self):
        duplicate = self.with_metadata(lambda meta: meta["source_units"].append(dict(meta["source_units"][0])))
        self.assert_error(duplicate, "duplicate ID")
        self.assert_error(self.original.replace("<!-- lesson-block: demo-red-bird-p003-v02 -->",
                                                "<!-- lesson-block: demo-red-bird-p003-v01 -->"), "duplicate ID")
        self.assert_error(self.original.replace("<!-- lesson-block: demo-red-bird-p003-v01 -->",
                                                "<!-- lesson-block: demo-red-bird-p003-u01 -->"), "duplicate ID")

    def test_page_number_and_revision_require_positive_real_integers(self):
        for field in ("pdf_page_number", "revision"):
            for value in ("3", True, 0, -1, 1.5):
                with self.subTest(field=field, value=value):
                    self.assert_error(self.with_metadata(lambda meta: meta.update({field: value})),
                                      f"metadata.{field}: expected an integer")

    def test_blocked_is_nonzero_even_with_valid_issues(self):
        text = self.with_metadata(lambda meta: meta.update(generation_status="blocked", issues=["原文待核对"]))
        path = self.write(text)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(checker.main([str(path)]), 1)
        self.assertIn("blocked draft cannot be enabled", output.getvalue())
        self.assertIn("不验证 PDF 忠实度", output.getvalue())
        self.assert_error(self.with_metadata(lambda meta: meta.update(generation_status="blocked")),
                          "blocked requires at least one issue")

    def test_draft_issues_and_fragment_warn_without_claiming_approval(self):
        def change(meta):
            meta["issues"] = ["解释待家长核对"]
            meta["source_units"][0]["kind"] = "fragment"
        result = checker.check_file(self.write(self.with_metadata(change)))
        self.assertEqual(result.errors, [])
        self.assertTrue(any("fragment" in warning for warning in result.warnings))
        self.assertTrue(any("待审核" in warning for warning in result.warnings))

    def test_missing_final_sentence_and_question_answer_are_detected(self):
        sentence = "<!-- lesson-block: demo-red-bird-p003-s03 -->"
        vocabulary = "<!-- lesson-block: demo-red-bird-p003-v01 -->"
        text = self.original[:self.original.index(sentence)] + self.original[self.original.index(vocabulary):]
        self.assert_error(text, "complete sentence has no sentence lesson block")
        self.assert_error(self.original.split("<!-- lesson-block: demo-red-bird-p003-a01 -->")[0],
                          "missing matching answer")
        self.assert_error(self.original.replace("<!-- lesson-block: demo-red-bird-p003-a01 -->",
                                                "<!-- lesson-block: demo-red-bird-p003-a02 -->"),
                          "missing matching question")

    def test_marker_order_and_sentence_reference_count(self):
        self.assert_error(self.original.replace("<!-- lesson-kind: sentence -->\n<!-- source-refs:",
                                                "<!-- lesson-kind: sentence -->\n\n<!-- source-refs:", 1),
                          "expected consecutive")
        self.assert_error(self.original.replace("<!-- source-refs: demo-red-bird-p003-u01 -->",
                                                "<!-- source-refs: demo-red-bird-p003-u01, demo-red-bird-p003-u02 -->", 1),
                          "sentence must have exactly one")

    def test_none_reference_only_for_overview_and_image_note(self):
        self.assert_error(self.original.replace("<!-- source-refs: demo-red-bird-p003-u01 -->",
                                                "<!-- source-refs: none -->", 1), "none only allowed")
        header = checker.HEADER.match(self.with_metadata(lambda meta: meta.update(source_units=[])))
        text = header[0] + ("<!-- lesson-block: demo-red-bird-p003-image -->\n"
                            "<!-- lesson-kind: image_note -->\n<!-- source-refs: none -->\n"
                            "## 图示说明\n一页原创图示。\n")
        self.assertEqual(checker.check_file(self.write(text)).errors, [])

    def test_recursive_directory_rejects_duplicate_page_ids(self):
        self.write(self.original, "one.md")
        self.write(self.original, "nested/two.md")
        results = checker.check_paths([self.directory])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(bool(result.errors) for result in results), 1)
        self.assertTrue(any("duplicate page" in error for result in results for error in result.errors))

    def test_template_placeholders_are_rejected(self):
        result = checker.check_file(ROOT / "templates" / "page-lesson.template.md")
        self.assertTrue(any("unresolved" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
