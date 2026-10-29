"""Strict presentation and source hygiene checks for RAZ lesson drafts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


HEADER = re.compile(r"\A<!-- raz-lesson[ \t]*\n(?P<json>.*?)\n-->[ \t]*(?:\n|\Z)", re.S)
BLOCK = re.compile(
    r"^<!-- lesson-block: (?P<id>.*?) -->\n"
    r"<!-- lesson-kind: (?P<kind>.*?) -->\n"
    r"<!-- source-refs: (?P<refs>.*?) -->\n"
    r"(?P<body>.*?)(?=^<!-- lesson-block: |\Z)",
    re.M | re.S,
)
ORIGINAL = re.compile(r"^\*\*原文：\*\*[ \t]*(?P<text>.*)$", re.M)
DETAIL_MARKER = re.compile(r"^\*\*读懂这句：\*\*[ \t]*$", re.M)
DETAIL = re.compile(r"^\*\*读懂这句：\*\*[ \t]*\n\n(?P<text>.*)\Z", re.M | re.S)
BACKTICK = re.compile(r"`([^`\r\n]+)`")
SOURCE_TOKEN = r"(?:[A-Za-z]+(?:['’][A-Za-z]+)*(?:-[A-Za-z0-9]+)*|\d[\d,]*(?:\.\d+)?(?:-[A-Za-z]+)?)"
BARE_SOURCE_PHRASE = re.compile(
    rf"(?<![A-Za-z0-9])(?P<phrase>{SOURCE_TOKEN}(?:[ \t]+{SOURCE_TOKEN})*)(?![A-Za-z0-9])"
)
SOURCE_WORD = re.compile(SOURCE_TOKEN)
NEGATED_REFERENCE = re.compile(r"(?:不带|没有|并非|不是)[ \t]*$")
CAPITALIZED_GRAMMAR_WORDS = {
    "a", "an", "and", "as", "at", "but", "for", "from", "he", "her", "hers", "him", "his",
    "i", "in", "into", "it", "its", "of", "on", "or", "she", "that", "the", "their", "them",
    "there", "they", "this", "those", "to", "we", "what", "when", "where", "which", "who", "why", "you",
}
PRINTING_ONLY = re.compile(
    r"(?:^LEVELED BOOK\b|^Level [A-Z] Leveled Book$|^©|^Written by\b|"
    r"^Illustrated by\b|^All rights reserved\.?$)",
    re.I,
)
URL = re.compile(r"(?:https?://|www\.)", re.I)
PAGE_FILE = re.compile(r"p(?P<number>\d{3,})\.md\Z", re.I)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def lesson_files(paths: list[Path]) -> tuple[list[Path], list[str]]:
    files: set[Path] = set()
    errors: list[str] = []
    for supplied in paths:
        if supplied.is_dir():
            matches = sorted(supplied.glob("p*.md"))
            if not matches:
                errors.append(f"{supplied}: no pNNN.md lesson files")
            files.update(path.resolve() for path in matches if path.is_file())
            numbered = [(PAGE_FILE.fullmatch(path.name), path) for path in matches]
            invalid = [path.name for match, path in numbered if match is None]
            if invalid:
                errors.append(f"{supplied}: invalid lesson filenames: {', '.join(invalid)}")
            numbers = sorted(int(match.group("number")) for match, _ in numbered if match)
            if numbers and numbers != list(range(1, numbers[-1] + 1)):
                errors.append(f"{supplied}: lesson files are not continuous from p001 to p{numbers[-1]:03d}")
        elif supplied.is_file():
            files.add(supplied.resolve())
        else:
            errors.append(f"{supplied}: path does not exist")
    return sorted(files), errors


def book_identity(path: Path) -> dict | None:
    source_dir = path.parent.parent if path.parent.name == "lessons" else None
    config = source_dir / "book.json" if source_dir else None
    if config is None or not config.is_file():
        return None
    try:
        return json.loads(config.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except (OSError, UnicodeError, ValueError):
        return None


def unmarked_source_phrases(
    detail_item: str, original: str, highlighted_terms: tuple[str, ...] = ()
) -> list[str]:
    """Find source wording discussed in a detail item without highlight backticks.

    A capitalized single word is usually a person or place name and is deliberately
    ignored. Multi-word phrases, numbers, lowercase terms, and grammar words are
    sufficiently strong signals that the item is explaining exact source wording.
    """
    item_highlights = tuple(BACKTICK.findall(detail_item))
    highlighted_terms = highlighted_terms + item_highlights
    bare_detail = BACKTICK.sub(" ", detail_item)
    phrases: list[str] = []
    for match in BARE_SOURCE_PHRASE.finditer(bare_detail):
        phrase = match.group("phrase")
        if NEGATED_REFERENCE.search(bare_detail[max(0, match.start() - 8):match.start()]):
            continue
        coverage = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", re.I
        )
        if any(coverage.search(highlighted) for highlighted in highlighted_terms):
            continue
        source_match = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])"
        )
        if not source_match.search(original):
            continue
        words = SOURCE_WORD.findall(phrase)
        single = words[0] if len(words) == 1 else ""
        worth_highlighting = (
            len(words) > 1
            or any(character.isdigit() for character in single)
            or (single[:1].islower())
            or (len(single) > 1 and single.isupper())
            or single.casefold() in CAPITALIZED_GRAMMAR_WORDS
        )
        if worth_highlighting and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def check_file(path: Path) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    counts = {"sentences": 0, "detail_items": 0, "highlights": 0}
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append("UTF-8 BOM is not allowed")
    if b"\r\n" in raw or b"\r" in raw:
        errors.append("use LF line endings only")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"invalid UTF-8: {exc}"], counts
    for number, line in enumerate(text.splitlines(), 1):
        if line.endswith((" ", "\t")):
            errors.append(f"line {number}: trailing whitespace")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    header = HEADER.match(normalized)
    if not header:
        return errors + ["missing or malformed raz-lesson header"], counts
    try:
        meta = json.loads(header.group("json"), object_pairs_hook=strict_object)
    except ValueError as exc:
        return errors + [f"invalid metadata JSON: {exc}"], counts

    page_id = meta.get("page_id")
    file_match = PAGE_FILE.fullmatch(path.name)
    if file_match and isinstance(page_id, str):
        suffix = f"-p{int(file_match.group('number')):03d}"
        if not page_id.endswith(suffix):
            errors.append(f"filename does not match page_id {page_id!r}")

    identity = book_identity(path)
    if identity is None:
        errors.append("cannot read adjacent _source/book.json")
    else:
        for lesson_key, book_key in (("book_id", "id"), ("book_title", "title"), ("raz_level", "level")):
            if meta.get(lesson_key) != identity.get(book_key):
                errors.append(f"metadata.{lesson_key} differs from book.json {book_key}")

    units = meta.get("source_units")
    if isinstance(units, list) and isinstance(page_id, str):
        # First drafts number units in reading order. Revisions retain stable IDs
        # when inserting, retiring or moving units, so array position is no longer
        # an identity. Still reject foreign, malformed and duplicate source IDs.
        revised = type(meta.get("revision")) is int and meta["revision"] > 1
        unit_pattern = re.compile(re.escape(page_id) + r"-u[0-9]{2,}\Z")
        seen_unit_ids: set[str] = set()
        for index, unit in enumerate(units, 1):
            if not isinstance(unit, dict):
                continue
            unit_id = unit.get("id")
            if revised:
                if (not isinstance(unit_id, str) or not unit_pattern.fullmatch(unit_id)
                        or int(unit_id.rsplit("-u", 1)[1]) == 0):
                    errors.append(f"source_units[{index - 1}].id: expected {page_id}-uNN (positive number)")
            else:
                expected = f"{page_id}-u{index:02d}"
                if unit_id != expected:
                    errors.append(f"source_units[{index - 1}].id: expected {expected}")
            if isinstance(unit_id, str):
                if unit_id in seen_unit_ids:
                    errors.append(f"source_units[{index - 1}].id: duplicate ID {unit_id}")
                seen_unit_ids.add(unit_id)
            source = unit.get("text")
            if isinstance(source, str):
                if URL.search(source):
                    errors.append(f"source_units[{index - 1}]: URL is not learning content")
                if PRINTING_ONLY.search(source.strip()):
                    errors.append(f"source_units[{index - 1}]: printing-only label is not learning content")

    for block in BLOCK.finditer(normalized, header.end()):
        if block.group("kind") != "sentence":
            continue
        counts["sentences"] += 1
        block_id = block.group("id")
        body = block.group("body").rstrip("\n")
        originals = ORIGINAL.findall(body)
        if len(originals) != 1:
            errors.append(f"{block_id}: expected exactly one **原文：** line")
            continue
        original = originals[0]
        if len(re.findall(r"^\*\*意思：\*\*", body, re.M)) != 1:
            errors.append(f"{block_id}: expected exactly one **意思：** line")
        marker = DETAIL_MARKER.search(body)
        if not marker:
            continue
        detail = DETAIL.search(body)
        if not detail:
            errors.append(f"{block_id}: **读懂这句：** must be followed by a blank line and a list")
            continue
        detail_text = detail.group("text").rstrip()
        highlighted_terms = tuple(BACKTICK.findall(detail_text))
        nonempty = [(number, line) for number, line in enumerate(detail_text.splitlines(), 1) if line.strip()]
        if not nonempty:
            errors.append(f"{block_id}: empty **读懂这句：** section")
            continue
        for number, line in nonempty:
            if line.startswith("- "):
                counts["detail_items"] += 1
                missing = unmarked_source_phrases(line[2:], original, highlighted_terms)
                if missing:
                    errors.append(
                        f"{block_id}: detail line {number} discusses exact source text without "
                        f"backticks: {', '.join(repr(item) for item in missing)}"
                    )
            elif not line.startswith(("  ", "\t")):
                errors.append(f"{block_id}: detail line {number} is not a Markdown list item")
        for term in BACKTICK.findall(detail_text):
            counts["highlights"] += 1
            if "..." in term or "…" in term:
                errors.append(f"{block_id}: highlighted phrase contains an ellipsis: {term!r}")
            if term not in original:
                errors.append(f"{block_id}: highlighted phrase is not exact contiguous source text: {term!r}")
    return errors, counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="lesson file(s) or _source/lessons directories")
    args = parser.parse_args(argv)
    files, collection_errors = lesson_files(args.paths)
    failures = list(collection_errors)
    totals = {"sentences": 0, "detail_items": 0, "highlights": 0}
    for path in files:
        errors, counts = check_file(path)
        for key, value in counts.items():
            totals[key] += value
        if errors:
            print(f"FAIL {path}")
            for error in errors:
                print(f"  ERROR: {error}")
            failures.extend(f"{path}: {error}" for error in errors)
        else:
            print(f"PASS {path}")
    for error in collection_errors:
        print(f"ERROR {error}")
    print(
        f"Checked {len(files)} file(s), {totals['sentences']} sentence block(s), "
        f"{totals['detail_items']} detail item(s), {totals['highlights']} highlighted phrase(s)."
    )
    if failures:
        print(f"Strict lesson audit failed with {len(failures)} issue(s).")
        return 1
    print("Strict lesson audit passed; translation and teaching accuracy still require visual review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
