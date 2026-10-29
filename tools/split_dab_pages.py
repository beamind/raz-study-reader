"""Split a deb3/version-2 DAB package into one folder per book, one file per page.

Blocks are cut at the whole-book read that starts each book: in the observed RAZ
packages every book is a run of consecutive slots whose first entry is a single
long recording of the entire book, followed by one recording per printed page,
and on books with a word list a run of sub-second single-word clips.

Sequence numbers are the order of the audio inside the package, not paper page
numbers; slots with no audio leave no file, so the two can drift. Every manifest
keeps `paper_page` empty for that reason.

Titles are only recovered when --reference-audio points at whole-book MP3s whose
durations can be matched one-to-one; otherwise folders fall back to slot numbers.
Read-only on the source package.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dab_segment_stats import measure_package, mp3_duration  # noqa: E402
from inspect_dab import classify_audio, digest, save_json, unwrap_audio  # noqa: E402

RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                  *(f"LPT{i}" for i in range(1, 10))}
COVERAGE_OK = (0.90, 1.10)


def safe_name(text: str, fallback: str) -> str:
    """Reduce a book title to something every target filesystem accepts."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:80].strip(" .")
    if not cleaned or cleaned.split(".")[0].upper() in RESERVED_NAMES:
        return fallback
    return cleaned


def group_books(entries: list[dict], book_threshold: float, word_threshold: float) -> list[dict]:
    """Cut the slot-ordered content entries into books at each whole-book read."""
    content = sorted((e for e in entries if e["kind"] == "content" and e["seconds"]),
                     key=lambda e: e["slot"])
    books: list[dict] = []
    for entry in content:
        if entry["seconds"] > book_threshold or not books:
            books.append({"whole_book": None, "parts": []})
            if entry["seconds"] > book_threshold:
                books[-1]["whole_book"] = entry
                continue
        books[-1]["parts"].append(entry)
    for book in books:
        pages = [e for e in book["parts"] if e["seconds"] >= word_threshold]
        words = [e for e in book["parts"] if e["seconds"] < word_threshold]
        page_seconds = sum(e["seconds"] for e in pages)
        whole = book["whole_book"]
        book.update({
            "page_count": len(pages), "word_count": len(words),
            "page_seconds": round(page_seconds, 3),
            "coverage_ratio": round(sum(e["seconds"] for e in book["parts"]) / whole["seconds"], 4)
            if whole else None,
            "word_slots": {e["slot"] for e in words},
        })
        book["coverage_warning"] = book["coverage_ratio"] is not None and not (
            COVERAGE_OK[0] <= book["coverage_ratio"] <= COVERAGE_OK[1])
    return books


def reference_durations(directory: Path) -> list[dict]:
    references = [{"name": path.stem, "seconds": mp3_duration(path.read_bytes())[0]}
                  for path in sorted(directory.glob("*.mp3"))]
    for path in sorted(directory.glob("*/_source/narration.mp3")):
        config = json.loads((path.parent / "book.json").read_text(encoding="utf-8"))
        references.append({"name": config["title"], "seconds": mp3_duration(path.read_bytes())[0]})
    return references


def match_titles(books: list[dict], references: list[dict], tolerance: float) -> None:
    """Assign each whole-book read the closest unused reference of the same length."""
    candidates = sorted(
        ((abs(book["whole_book"]["seconds"] - ref["seconds"]), index, ref["name"])
         for index, book in enumerate(books) if book["whole_book"]
         for ref in references),
        key=lambda pair: pair[0])
    used_books: set[int] = set()
    used_names: set[str] = set()
    for delta, index, name in candidates:
        if delta > tolerance or index in used_books or name in used_names:
            continue
        used_books.add(index)
        used_names.add(name)
        books[index].update({"title": name, "title_delta_seconds": round(delta, 3)})


def plan_book(book: dict, position: int) -> dict:
    """Name one book folder and every file inside it, without touching the disk."""
    whole = book["whole_book"]
    anchor = whole or book["parts"][0]
    fallback = f"book-{position:03d}-slot-{anchor['slot']:04d}"
    title = book.get("title")
    folder = f"{position:03d} {safe_name(title, fallback)}" if title else fallback
    if not whole:
        folder += "-no-whole-book-read"

    def tag(entry: dict) -> str:
        return f"oid-{entry['candidate_oid']}" if entry["candidate_oid"] is not None \
            else f"slot-{entry['slot']:04d}"

    files = []
    if whole:
        files.append({"role": "whole_book", "sequence": None, "entry": whole,
                      "name": f"00-whole-book-{tag(whole)}.mp3"})
    pages = words = 0
    for entry in book["parts"]:
        if entry["slot"] in book["word_slots"]:
            words += 1
            files.append({"role": "word", "sequence": words, "entry": entry,
                          "name": f"word-{words:02d}-{tag(entry)}.mp3"})
        else:
            pages += 1
            files.append({"role": "page", "sequence": pages, "entry": entry,
                          "name": f"page-{pages:02d}-{tag(entry)}.mp3"})
    return {"folder": folder, "files": files}


def build_manifest(book: dict, plan: dict, position: int) -> dict:
    return {
        "book_index": position,
        "title": book.get("title"),
        "title_source": "reference_audio_duration_match" if book.get("title") else None,
        "title_delta_seconds": book.get("title_delta_seconds"),
        "whole_book_slot": book["whole_book"]["slot"] if book["whole_book"] else None,
        "page_count": book["page_count"],
        "word_count": book["word_count"],
        "page_seconds": book["page_seconds"],
        "coverage_ratio": book["coverage_ratio"],
        "coverage_warning": book["coverage_warning"],
        "sequence_is_not_paper_page": True,
        "files": [{"file": item["name"], "role": item["role"], "sequence": item["sequence"],
                   "slot": item["entry"]["slot"], "candidate_oid": item["entry"]["candidate_oid"],
                   "seconds": item["entry"]["seconds"], "paper_page": None}
                  for item in plan["files"]],
    }


def write_book(source_stream, output: Path, plan: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for item in plan["files"]:
        entry = item["entry"]
        source_stream.seek(entry["offset"])
        raw = source_stream.read(entry["stored_length"])
        if len(raw) != entry["stored_length"]:
            raise ValueError(f"Short read at slot {entry['slot']}; source changed")
        mode = classify_audio(raw[:64])
        if mode == "unknown":
            raise ValueError(f"Unrecognized audio wrapper at slot {entry['slot']}")
        (output / item["name"]).write_bytes(unwrap_audio(raw, mode))


def split(source: Path, output: Path, reference: Path | None, book_threshold: float,
          word_threshold: float, tolerance: float, dry_run: bool) -> dict:
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source.parent) or source.is_relative_to(output):
        raise ValueError("Output must be separate from the source directory tree")
    before = digest(source)
    metadata, entries = measure_package(source)
    books = group_books(entries, book_threshold, word_threshold)
    if not any(book["whole_book"] for book in books):
        raise ValueError(f"No entry longer than {book_threshold}s, so book boundaries are "
                         "unknown; adjust --book-threshold for this package")
    if reference:
        match_titles(books, reference_durations(reference.resolve()), tolerance)

    plans = [plan_book(book, position) for position, book in enumerate(books, 1)]
    extras = [e for e in entries if e["kind"] != "content" and e["seconds"]]
    summaries = []
    if not dry_run:
        with source.open("rb") as stream:
            for position, (book, plan) in enumerate(zip(books, plans), 1):
                folder = output / "books" / plan["folder"]
                write_book(stream, folder, plan)
                save_json(folder / "manifest.json", build_manifest(book, plan, position))
                print(f"{plan['folder']}: {book['page_count']} pages, "
                      f"{book['word_count']} words", file=sys.stderr)
            if extras:
                extra_plan = {"files": [
                    {"role": e["kind"], "sequence": index, "entry": e,
                     "name": f"{e['kind']}-{e['slot']:02d}-oid-{e['candidate_oid']}.mp3"}
                    for index, e in enumerate(extras, 1)]}
                write_book(stream, output / "guide", extra_plan)
    for position, (book, plan) in enumerate(zip(books, plans), 1):
        summaries.append({"book_index": position, "folder": plan["folder"],
                          "title": book.get("title"), "page_count": book["page_count"],
                          "word_count": book["word_count"],
                          "coverage_ratio": book["coverage_ratio"],
                          "coverage_warning": book["coverage_warning"]})

    after = digest(source)
    report = {
        "source": str(source), "source_sha256": before,
        "source_unchanged": before == after,
        "package_title": metadata["title"],
        "dry_run": dry_run,
        "thresholds": {"book_seconds": book_threshold, "word_seconds": word_threshold,
                       "title_tolerance_seconds": tolerance},
        "book_count": len(books),
        "page_files": sum(b["page_count"] for b in books),
        "word_files": sum(b["word_count"] for b in books),
        "whole_book_files": sum(b["whole_book"] is not None for b in books),
        "guide_files": len(extras),
        "titled_books": sum("title" in b for b in books),
        "coverage_warnings": [s for s in summaries if s["coverage_warning"]],
        "uncertain_titles": [{"folder": s["folder"], "delta_seconds": b["title_delta_seconds"]}
                             for s, b in zip(summaries, books)
                             if b.get("title_delta_seconds", 0) > 3],
        "books": summaries,
        "limitations": [
            "Sequence numbers follow package order; paper page numbers are unverified.",
            "Titles come from whole-book duration matching, not from the package itself.",
            "Books are cut at long recordings; a package without them needs a new threshold.",
        ],
    }
    if not dry_run:
        save_json(output / "index.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-audio", type=Path,
                        help="Directory of whole-book MP3s used to recover titles by duration")
    parser.add_argument("--book-threshold", type=float, default=200.0,
                        help="Seconds above which an entry starts a new book (default: 200)")
    parser.add_argument("--word-threshold", type=float, default=2.0,
                        help="Seconds below which an entry is a word-list clip (default: 2)")
    parser.add_argument("--title-tolerance", type=float, default=8.0,
                        help="Largest duration gap accepted when matching titles (default: 8)")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan without writing")
    args = parser.parse_args()
    if args.book_threshold <= args.word_threshold:
        parser.error("--book-threshold must be greater than --word-threshold")
    if args.reference_audio and not args.reference_audio.is_dir():
        parser.error("--reference-audio must be an existing directory")
    report = split(args.source, args.output, args.reference_audio, args.book_threshold,
                   args.word_threshold, args.title_tolerance, args.dry_run)
    print(json.dumps({k: v for k, v in report.items() if k != "books"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
