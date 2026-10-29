"""Read-only, standard-library checks for raz-lesson/1.0 Markdown drafts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re


NOTICE = "仅检查结构；不验证 PDF 忠实度或教学正确性；通过不等于审核通过。"
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
HEADER = re.compile(r"\A\s*<!-- raz-lesson[ \t]*\n(.*?)\n-->[ \t]*(?:\n|\Z)", re.S)
MARKER = re.compile(r"[ \t]*<!-- (lesson-block|lesson-kind|source-refs): (.*?) -->[ \t]*\Z")
RESERVED = re.compile(r"<!--\s*(?:lesson-block|lesson-kind|source-refs)\b")
SOURCE_KINDS = {"sentence", "fragment", "title", "caption", "label"}
LESSON_KINDS = {"overview", "sentence", "vocabulary", "structure", "image_note", "question", "answer"}


@dataclass
class CheckResult:
    path: Path
    page_id: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def check_file(path: Path) -> CheckResult:
    result = CheckResult(Path(path))
    error = result.errors.append
    try:
        text = result.path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        error(f"read: {exc}")
        return result
    if re.search(r"\{\{[^{}\n]*\}\}", text):
        error("template: unresolved {{placeholder}}")
    header = HEADER.match(text)
    if not header:
        error("header: expected hidden JSON <!-- raz-lesson ... --> at the start")
        return result
    try:
        meta = json.loads(header[1], object_pairs_hook=strict_object, parse_constant=reject_constant)
    except ValueError as exc:
        error(f"JSON: {exc}")
        return result
    if not isinstance(meta, dict):
        error("header: JSON must be an object")
        return result

    required = {"schema_version", "book_id", "book_title", "raz_level", "page_id",
                "pdf_page_number", "paper_page_label", "revision", "generation_status",
                "is_demo", "issues", "source_units"}
    for key in sorted(required - meta.keys()):
        error(f"metadata.{key}: required field missing")
    if meta.get("schema_version") != "raz-lesson/1.0":
        error("metadata.schema_version: expected raz-lesson/1.0")
    book = meta.get("book_id")
    page = meta.get("page_id")
    if not isinstance(book, str) or not ID.fullmatch(book):
        error("metadata.book_id: expected lowercase letters, digits and single hyphens")
    if (not isinstance(page, str) or not isinstance(book, str)
            or not re.fullmatch(re.escape(book) + r"-p[0-9]{3,}", page)):
        error("metadata.page_id: expected book_id-pNNN (at least three digits)")
    else:
        result.page_id = page
    if not isinstance(meta.get("book_title"), str) or not meta["book_title"].strip():
        error("metadata.book_title: expected a nonempty string")
    for key in ("pdf_page_number", "revision"):
        if type(meta.get(key)) is not int or meta[key] <= 0:
            error(f"metadata.{key}: expected an integer greater than zero (not a string or boolean)")
    for key in ("raz_level", "paper_page_label"):
        value = meta.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            error(f"metadata.{key}: expected a nonempty string or null")
    if type(meta.get("is_demo")) is not bool:
        error("metadata.is_demo: expected a boolean")
    elif meta["is_demo"]:
        result.warnings.append("is_demo: 原创演示，不是真实教材内容")
    status = meta.get("generation_status")
    if status not in ("draft", "blocked"):
        error("metadata.generation_status: expected draft or blocked")
    issues = meta.get("issues")
    if not isinstance(issues, list) or any(not isinstance(item, str) or not item.strip() for item in issues):
        error("metadata.issues: expected an array of nonempty strings")
    elif issues:
        result.warnings.extend(f"issue（待审核）: {item}" for item in issues)
    if status == "blocked":
        error("generation_status: blocked draft cannot be enabled")
        if not isinstance(issues, list) or not issues:
            error("metadata.issues: blocked requires at least one issue")

    all_ids = set()

    def check_id(value, where):
        if not isinstance(value, str) or not ID.fullmatch(value):
            error(f"{where}: invalid ID")
            return False
        if not isinstance(page, str) or not value.startswith(page + "-"):
            error(f"{where}: ID must belong to this page")
        if value in all_ids:
            error(f"{where}: duplicate ID {value}")
            return False
        all_ids.add(value)
        return True

    sources = {}
    units = meta.get("source_units")
    if not isinstance(units, list):
        error("metadata.source_units: expected an array")
        units = []
    for index, unit in enumerate(units):
        where = f"source_units[{index}]"
        if not isinstance(unit, dict):
            error(f"{where}: expected an object")
            continue
        valid_id = check_id(unit.get("id"), where + ".id")
        if unit.get("kind") not in SOURCE_KINDS:
            error(f"{where}.kind: unsupported source kind")
        if not isinstance(unit.get("text"), str) or not unit["text"].strip():
            error(f"{where}.text: expected nonempty original text")
        if valid_id:
            sources[unit["id"]] = unit
        if unit.get("kind") == "fragment":
            result.warnings.append(f"{where}: fragment，跨页/不完整原文仍需人工核对")

    # Keep original file line numbers for actionable marker and quote errors.
    lines = text.splitlines()
    first_body_line = text[:header.end()].count("\n")
    markers = {}
    for index in range(first_body_line, len(lines)):
        line = lines[index]
        if "<!-- raz-lesson" in line:
            error(f"line {index + 1}: duplicate metadata header")
        if RESERVED.search(line):
            marker = MARKER.fullmatch(line)
            if marker:
                markers[index] = (marker[1], marker[2])
            else:
                error(f"line {index + 1}: malformed block marker")
    starts = [index for index, marker in markers.items() if marker[0] == "lesson-block"]
    if not starts:
        error("body: no lesson blocks")
    used_markers = set()
    covered_sentences = set()
    question_numbers, answer_numbers = set(), set()
    for ordinal, start in enumerate(starts):
        block_id = markers[start][1]
        where = f"line {start + 1} ({block_id})"
        check_id(block_id, where)
        used_markers.add(start)
        if (markers.get(start + 1, (None,))[0] != "lesson-kind"
                or markers.get(start + 2, (None,))[0] != "source-refs"):
            error(f"{where}: expected consecutive lesson-block / lesson-kind / source-refs lines")
            continue
        used_markers.update((start + 1, start + 2))
        kind = markers[start + 1][1]
        if kind not in LESSON_KINDS:
            error(f"{where}: unsupported lesson-kind {kind}")
        raw_refs = markers[start + 2][1]
        refs = [] if raw_refs == "none" else [item.strip() for item in raw_refs.split(",")]
        if raw_refs == "none" and kind not in ("overview", "image_note"):
            error(f"{where}: source-refs none only allowed for overview/image_note")
        if len(refs) != len(set(refs)):
            error(f"{where}: duplicate source-refs")
        for ref in refs:
            if ref not in sources:
                error(f"{where}: unknown source reference {ref!r}")
        end = starts[ordinal + 1] if ordinal + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 3:end])
        if not body.strip():
            error(f"{where}: empty lesson content")
        if kind == "sentence":
            if len(refs) != 1:
                error(f"{where}: sentence must have exactly one source reference")
            original = re.findall(r"^\*\*原文：\*\*[ \t]*(.*)$", body, re.M)
            if len(original) != 1:
                error(f"{where}: sentence needs exactly one standalone **原文：** line")
            elif len(refs) == 1 and refs[0] in sources and original[0] != sources[refs[0]].get("text"):
                error(f"{where}: original text differs from source_units text")
            if len(refs) == 1 and refs[0] in sources:
                covered_sentences.add(refs[0])
        if kind in ("question", "answer"):
            prefix = "q" if kind == "question" else "a"
            number = re.fullmatch(re.escape(str(page)) + "-" + prefix + r"([0-9]{2,})", block_id)
            if not number:
                error(f"{where}: {kind} ID must end with -{prefix}NN")
            else:
                numbers = question_numbers if kind == "question" else answer_numbers
                value = int(number[1])
                if value in numbers:
                    error(f"{where}: duplicate {kind} sequence number {value}")
                numbers.add(value)
    for index in markers.keys() - used_markers:
        error(f"line {index + 1}: orphan lesson-kind/source-refs marker")
    for source_id, unit in sources.items():
        if unit.get("kind") == "sentence" and source_id not in covered_sentences:
            error(f"{source_id}: complete sentence has no sentence lesson block")
    for number in sorted(question_numbers - answer_numbers):
        error(f"question q{number:02d}: missing matching answer")
    for number in sorted(answer_numbers - question_numbers):
        error(f"answer a{number:02d}: missing matching question")
    return result


def check_paths(paths: list[Path]) -> list[CheckResult]:
    files = set()
    results = []
    for supplied in paths:
        path = Path(supplied)
        if path.is_dir():
            matches = list(path.rglob("*.md"))
            if not matches:
                results.append(CheckResult(path, errors=["input: directory has no Markdown files"]))
            files.update(item.resolve() for item in matches if item.is_file())
        else:
            files.add(path.resolve())
    pages = {}
    for path in sorted(files):
        result = check_file(path)
        if result.page_id:
            previous = pages.get(result.page_id)
            if previous is not None:
                result.errors.append(f"page_id: duplicate page {result.page_id}; also in {previous}")
            else:
                pages[result.page_id] = path
        results.append(result)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Markdown file(s) or directories to check recursively")
    args = parser.parse_args(argv)
    results = check_paths(args.paths)
    for result in results:
        print(f"{'FAIL' if result.errors else 'PASS（结构通过，仍为草稿）'} {result.path}")
        for message in result.errors:
            print(f"  ERROR: {message}")
        for message in result.warnings:
            print(f"  WARNING: {message}")
    print(NOTICE)
    return int(any(result.errors for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
