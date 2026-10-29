"""Build reviewable OID-to-lesson candidates from ASR and raz-lesson Markdown.

No result is automatically approved.  Strong, unambiguous matches are emitted as
`candidate`; weak, duplicate, or cross-book results remain `needs_review`.
"""

from __future__ import annotations

import argparse
import csv
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
import unicodedata


sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_lesson import HEADER, MARKER, check_paths  # noqa: E402

ALGORITHM = "asr-lesson-source-span-align/1.2"

# A typographic apostrophe survives NFKD and is then dropped as non-ASCII, so
# "Bird's" and "Bird’s" would tokenize differently.  Removing every apostrophe
# first keeps both spellings, and ASR contractions, on the same tokens.
APOSTROPHES = str.maketrans("", "", "'\u2018\u2019\u02bc\u0060")


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).translate(APOSTROPHES)
    return re.findall(r"[a-z0-9]+", text.encode("ascii", "ignore").decode().casefold())


def shingles(tokens: list[str], size: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[index:index + size]) for index in range(len(tokens) - size + 1)}


def similarity(left: str, right: str) -> dict:
    a, b = words(left), words(right)
    if not a or not b:
        return {"score": 0.0, "sequence": 0.0, "shingle": 0.0,
                "character": 0.0, "length": 0.0}
    sequence = SequenceMatcher(None, a, b, autojunk=False).ratio()
    character = SequenceMatcher(None, " ".join(a), " ".join(b), autojunk=False).ratio()
    sa, sb = shingles(a), shingles(b)
    shingle = 2 * len(sa & sb) / (len(sa) + len(sb)) if sa or sb else 0.0
    length = min(len(a), len(b)) / max(len(a), len(b))
    # Character similarity recovers common tiny-model ASR errors such as
    # "car makers"/"carmakers" and slightly misspelled proper names.
    score = 0.4 * sequence + 0.3 * shingle + 0.2 * character + 0.1 * length
    return {"score": round(score, 4), "sequence": round(sequence, 4),
            "shingle": round(shingle, 4), "character": round(character, 4),
            "length": round(length, 4)}


def rank_lesson(asr_text: str, lesson: dict) -> dict:
    """Return the best contiguous source-unit span inside one lesson page.

    DAB audio entries are not consistently one recording per printed page.  A
    callout, chart, or the second half of a page can have its own OID, so page
    alignment must retain the smaller source span that actually matched.
    """
    units = lesson["meta"].get("source_units", [])
    spans = []
    for start in range(len(units)):
        for stop in range(start + 1, len(units) + 1):
            selected = units[start:stop]
            source_text = " ".join(unit["text"] for unit in selected)
            spans.append({
                "start": start, "stop": stop, "source_text": source_text,
                "metrics": similarity(asr_text, source_text),
            })
    best = max(spans, key=lambda item: (
        item["metrics"]["score"], item["stop"] - item["start"], -item["start"]
    )) if spans else {
        "start": 0, "stop": 0, "source_text": "",
        "metrics": similarity(asr_text, ""),
    }
    selected = units[best["start"]:best["stop"]]
    source_unit_ids = [unit["id"] for unit in selected]
    source_unit_id_set = set(source_unit_ids)
    lesson_block_ids = [
        block["id"] for block in lesson["blocks"]
        if source_unit_id_set.intersection(block["source_refs"])
    ]
    return {
        "page_id": lesson["meta"]["page_id"],
        "pdf_page_number": lesson["meta"]["pdf_page_number"],
        "paper_page_label": lesson["meta"].get("paper_page_label"),
        "lesson_book_id": lesson["meta"]["book_id"],
        "lesson_book_title": lesson["meta"]["book_title"],
        "lesson_path": str(lesson["path"]),
        "metrics": best["metrics"],
        "source_unit_ids": source_unit_ids,
        "source_unit_span": {
            "start": best["start"] + 1,
            "end": best["stop"],
            "of": len(units),
        },
        "matched_source_text": best["source_text"],
        "lesson_block_ids": lesson_block_ids,
    }


def parse_lesson(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    header = HEADER.match(text)
    if not header:
        raise ValueError(f"Missing raz-lesson header: {path}")
    meta = json.loads(header[1])
    blocks = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        marker = MARKER.fullmatch(line)
        if not marker or marker[1] != "lesson-block":
            continue
        kind = MARKER.fullmatch(lines[index + 1]) if index + 1 < len(lines) else None
        refs = MARKER.fullmatch(lines[index + 2]) if index + 2 < len(lines) else None
        if kind and refs and kind[1] == "lesson-kind" and refs[1] == "source-refs":
            blocks.append({"id": marker[2], "kind": kind[2],
                           "source_refs": [] if refs[2] == "none"
                           else [item.strip() for item in refs[2].split(",")]})
    source_text = " ".join(unit["text"] for unit in meta.get("source_units", []))
    return {"path": path, "meta": meta, "blocks": blocks, "source_text": source_text}


def load_lessons(paths: list[Path]) -> list[dict]:
    checks = check_paths(paths)
    errors = [(result.path, result.errors) for result in checks if result.errors]
    if errors:
        detail = "; ".join(f"{path}: {', '.join(messages)}" for path, messages in errors)
        raise ValueError(f"Lesson validation failed: {detail}")
    files = sorted({result.path.resolve() for result in checks if result.path.is_file()})
    return [parse_lesson(path) for path in files]


def load_title_map(path: Path) -> tuple[dict[str, dict], dict[str, list[dict]], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_folder = {book["source_folder"]: book for book in payload["books"]}
    by_title: dict[str, list[dict]] = {}
    for book in payload["books"]:
        # A reconciled canonical title identifies the audio content.  The current
        # title can be a stale folder name left by the DAB splitter, so only keep
        # it as an alias when reconciliation explicitly says it still matches.
        titles = [book.get("canonical_title")]
        if book.get("current_title_matches") or not book.get("canonical_title"):
            titles.append(book.get("current_title"))
        for title in titles:
            key = " ".join(words(title or ""))
            if key and book not in by_title.setdefault(key, []):
                by_title[key].append(book)
    return by_folder, by_title, payload


def load_asr(asr_root: Path) -> list[dict]:
    records = []
    for path in sorted((asr_root / "books").rglob("*.asr.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") == "ok" and record.get("location", {}).get("role") == "page":
            records.append(record)
    return records


def align(asr_root: Path, title_map_path: Path, lesson_paths: list[Path],
          min_score: float, min_margin: float) -> dict:
    by_folder, by_title, title_map = load_title_map(title_map_path)
    lessons = load_lessons(lesson_paths)
    lessons_by_book: dict[str, list[dict]] = {}
    unmatched_lesson_books: dict[tuple[str, str], dict] = {}
    for lesson in lessons:
        meta = lesson["meta"]
        stable = next((book for book in by_folder.values() if book["book_id"] == meta["book_id"]), None)
        if not stable:
            candidates = by_title.get(" ".join(words(meta["book_title"])), [])
            stable = candidates[0] if len(candidates) == 1 else None
        if stable:
            lessons_by_book.setdefault(stable["book_id"], []).append(lesson)
        else:
            key = (meta["book_id"], meta["book_title"])
            entry = unmatched_lesson_books.setdefault(key, {
                "book_id": meta["book_id"], "book_title": meta["book_title"],
                "lesson_pages": 0, "example_path": str(lesson["path"]),
            })
            entry["lesson_pages"] += 1

    bindings = []
    for record in load_asr(asr_root):
        folder = record["book"]["folder"]
        book = by_folder.get(folder)
        candidates = lessons_by_book.get(book["book_id"], []) if book else []
        ranked = sorted((rank_lesson(record["text"], lesson) for lesson in candidates),
                        key=lambda item: (-item["metrics"]["score"], item["page_id"]))
        top = ranked[0] if ranked else None
        second = ranked[1]["metrics"]["score"] if len(ranked) > 1 else 0.0
        margin = round(top["metrics"]["score"] - second, 4) if top else 0.0
        suggested = bool(top and top["metrics"]["score"] >= min_score and margin >= min_margin)
        bindings.append({
            "binding_id": f"{book['book_id'] if book else 'unknown'}-oid-{record['location']['candidate_oid']}",
            "alignment_status": "candidate" if suggested else "needs_review",
            "review_required": True,
            "reason": ("strong_unique_page_candidate" if suggested else
                       "no_lessons_for_book" if not candidates else "weak_or_ambiguous_page_match"),
            "book_id": book["book_id"] if book else None,
            "canonical_title": book.get("canonical_title") if book else None,
            "package_id": title_map.get("source_package_sha256"),
            "edition_id": None,
            "code_space": "xiaodaren-dab-candidate-oid",
            "raw_code": str(record["location"]["candidate_oid"]),
            "code_evidence": "package_footer_bitmap_match",
            "physical_verification": {"status": "pending"},
            "source_audio": record["source_audio"],
            "source_audio_sha256": record["source_audio_sha256"],
            "asr_text_sha256": record["text_sha256"],
            "asr_text": record["text"],
            "slot": record["location"]["slot"],
            "candidate_oid": str(record["location"]["candidate_oid"]),
            "sequence": record["location"]["sequence"],
            "paper_page": record["location"]["paper_page"],
            "target_granularity": "source_unit_span",
            "suggested_target": top,
            "score_margin": margin if top else None,
            "top_candidates": ranked[:5],
        })
    covered_bindings = [binding for binding in bindings
                        if binding["reason"] != "no_lessons_for_book"]
    return {
        "schema_version": "raz-alignment-candidates/1.0",
        "algorithm": ALGORITHM,
        "thresholds": {"min_score": min_score, "min_margin": min_margin},
        "summary": {
            "asr_pages": len(bindings), "lesson_pages": len(lessons),
            "candidate": sum(b["alignment_status"] == "candidate" for b in bindings),
            "needs_review": sum(b["alignment_status"] == "needs_review" for b in bindings),
            "asr_pages_with_lessons": sum(
                bool(lessons_by_book.get(binding["book_id"])) for binding in bindings
            ),
            "asr_pages_without_lessons": len(bindings) - len(covered_bindings),
            "covered_needs_review": sum(
                binding["alignment_status"] == "needs_review"
                for binding in covered_bindings
            ),
            "lesson_books": len(lessons_by_book),
            "unmatched_lesson_books": len(unmatched_lesson_books),
        },
        "unmatched_lesson_books": list(unmatched_lesson_books.values()),
        "bindings": bindings,
    }


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def save_review_csv(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "canonical_title", "audio_sequence", "candidate_oid", "alignment_status",
        "reason", "pdf_page_number", "paper_page_label", "source_unit_span",
        "score", "score_margin", "source_unit_ids", "lesson_block_ids",
        "asr_text", "matched_source_text", "lesson_path", "review_decision",
        "reviewer_note",
    ]
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for binding in report["bindings"]:
            if binding["reason"] == "no_lessons_for_book":
                continue
            target = binding.get("suggested_target") or {}
            span = target.get("source_unit_span") or {}
            writer.writerow({
                "canonical_title": binding.get("canonical_title"),
                "audio_sequence": binding.get("sequence"),
                "candidate_oid": binding.get("candidate_oid"),
                "alignment_status": binding.get("alignment_status"),
                "reason": binding.get("reason"),
                "pdf_page_number": target.get("pdf_page_number"),
                "paper_page_label": target.get("paper_page_label"),
                "source_unit_span": (f"{span.get('start')}-{span.get('end')}/{span.get('of')}"
                                     if span else ""),
                "score": (target.get("metrics") or {}).get("score"),
                "score_margin": binding.get("score_margin"),
                "source_unit_ids": " | ".join(target.get("source_unit_ids", [])),
                "lesson_block_ids": " | ".join(target.get("lesson_block_ids", [])),
                "asr_text": binding.get("asr_text"),
                "matched_source_text": target.get("matched_source_text"),
                "lesson_path": target.get("lesson_path"),
                "review_decision": "",
                "reviewer_note": "",
            })
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asr-root", type=Path, required=True)
    parser.add_argument("--title-map", type=Path, required=True)
    parser.add_argument("--lessons", type=Path, action="append", required=True)
    parser.add_argument("--min-score", type=float, default=0.72)
    parser.add_argument("--min-margin", type=float, default=0.08)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-csv", type=Path,
                        help="Optional UTF-8 CSV containing only books that have lessons")
    args = parser.parse_args()
    report = align(args.asr_root.resolve(), args.title_map.resolve(), args.lessons,
                   args.min_score, args.min_margin)
    save_json(args.output.resolve(), report)
    if args.review_csv:
        save_review_csv(args.review_csv.resolve(), report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
