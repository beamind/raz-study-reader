"""Cross-check split-book titles using first-page ASR and named local resources.

The tool never renames source folders or edits split manifests.  It emits stable
book IDs plus evidence and ranked candidates so downstream alignment does not
depend on duration-derived folder names.
"""

from __future__ import annotations

import argparse
import csv
from difflib import SequenceMatcher
import fnmatch
import json
from pathlib import Path
import re
import unicodedata


ALGORITHM = "first-page-title-cross-check/1.0"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def observed_title(text: str) -> str:
    text = " ".join(text.split()).strip(" .,:;-\t")
    cut = re.split(
        r"\b(?:written|retold|adapted|illustrated|photographed)\s+by\b|\bby\s+[A-Z]",
        text, maxsplit=1, flags=re.I)[0]
    cut = re.sub(r"^(?:title|book)\s*[:.-]\s*", "", cut, flags=re.I)
    return cut.strip(" .,:;-")


def plausible_title(text: str) -> bool:
    return bool(text and len(text) <= 100 and len(normalize(text).split()) <= 12)


def similarity(observed: str, reference: str) -> float:
    left, right = normalize(observed), normalize(reference)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if right in left or left in right:
        coverage = min(len(left), len(right)) / max(len(left), len(right))
        return round(0.88 + 0.11 * coverage, 4)
    left_tokens, right_tokens = set(left.split()), set(right.split())
    token_dice = 2 * len(left_tokens & right_tokens) / (len(left_tokens) + len(right_tokens))
    char_ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    return round(0.58 * char_ratio + 0.42 * token_dice, 4)


def reference_titles(directories: list[Path], catalogs: list[Path] | None = None) -> dict[str, dict]:
    titles: dict[str, dict] = {}
    allowed = {".pdf": "pdf_filename", ".mp3": "reference_audio_filename"}
    for directory in directories:
        for path in sorted(directory.resolve().rglob("*")):
            source = allowed.get(path.suffix.casefold())
            if not source or not path.is_file():
                continue
            config_path = path.parent / "book.json"
            if path.parent.name == "_source" and config_path.is_file():
                title = json.loads(config_path.read_text(encoding="utf-8"))["title"]
            elif path.parent == directory.resolve():
                title = path.stem
            else:
                continue
            key = normalize(title)
            record = titles.setdefault(key, {"title": title, "sources": [], "paths": []})
            if source not in record["sources"]:
                record["sources"].append(source)
            record["paths"].append(str(path))
    for catalog_path in catalogs or []:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if catalog.get("schema_version") != "raz-title-reference/1.0":
            raise ValueError(f"Unsupported title reference catalog: {catalog_path}")
        for item in catalog.get("titles", []):
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"Invalid title in {catalog_path}")
            key = normalize(title)
            record = titles.setdefault(key, {"title": title.strip(), "sources": [], "paths": []})
            source = item.get("source", "external_reference")
            if source not in record["sources"]:
                record["sources"].append(source)
            if item.get("url"):
                record["paths"].append(item["url"])
    return titles


def load_books(split_root: Path, asr_root: Path,
               book_patterns: list[str] | None = None) -> list[dict]:
    books = []
    for manifest_path in sorted((split_root.resolve() / "books").glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        folder = manifest_path.parent.name
        if book_patterns and not any(fnmatch.fnmatch(folder, pattern) for pattern in book_patterns):
            continue
        first = next((item for item in manifest.get("files", [])
                      if item.get("role") == "page" and item.get("sequence") == 1), None)
        if not first:
            raise ValueError(f"No sequence-1 page in {manifest_path}")
        sidecar = asr_root.resolve() / "books" / folder / f"{Path(first['file']).stem}.asr.json"
        if not sidecar.is_file():
            raise FileNotFoundError(f"Missing first-page ASR: {sidecar}")
        asr = json.loads(sidecar.read_text(encoding="utf-8"))
        text = asr.get("text", "")
        books.append({
            "book_index": manifest["book_index"], "source_folder": folder,
            "current_title": manifest.get("title"),
            "anchor_slot": manifest.get("whole_book_slot") if manifest.get("whole_book_slot") is not None
            else first.get("slot"),
            "first_page": first, "first_page_asr": text,
            "observed_title": observed_title(text),
        })
    return books


def reconcile(split_root: Path, asr_root: Path, references: list[Path], collection_id: str,
              min_score: float, min_margin: float, overrides: dict | None = None,
              catalogs: list[Path] | None = None,
              book_patterns: list[str] | None = None) -> dict:
    refs = reference_titles(references, catalogs)
    books = load_books(split_root, asr_root, book_patterns)
    proposed: dict[str, list[dict]] = {}
    for book in books:
        ranked = sorted(({
            "title": ref["title"], "score": similarity(book["observed_title"], ref["title"]),
            "sources": sorted(ref["sources"]), "paths": ref["paths"],
        } for ref in refs.values()), key=lambda item: (-item["score"], item["title"].casefold()))
        proposed[book["source_folder"]] = ranked[:5]

    # A reference title can cross-check only one package book. Keep its strongest claimant.
    claims: dict[str, list[tuple[float, str]]] = {}
    for folder, ranked in proposed.items():
        if ranked:
            claims.setdefault(normalize(ranked[0]["title"]), []).append((ranked[0]["score"], folder))
    winners = {}
    for key, values in claims.items():
        ranked_claims = sorted(values, reverse=True)
        if len(ranked_claims) == 1 or ranked_claims[0][0] - ranked_claims[1][0] >= min_margin:
            winners[key] = ranked_claims[0][1]

    output_books = []
    for book in books:
        ranked = proposed[book["source_folder"]]
        top = ranked[0] if ranked else None
        second_score = ranked[1]["score"] if len(ranked) > 1 else 0.0
        margin = round(top["score"] - second_score, 4) if top else 0.0
        unique = bool(top and winners.get(normalize(top["title"])) == book["source_folder"])
        cross_checked = bool(top and top["score"] >= min_score and unique
                             and (margin >= min_margin or top["score"] >= 0.98))
        canonical = (top["title"] if cross_checked else book["observed_title"]
                     if plausible_title(book["observed_title"]) else None)
        status = ("cross_checked" if cross_checked else "asr_observed_needs_review"
                  if canonical else "missing_title_page_needs_review")
        stable_id = f"{collection_id}-b{book['book_index']:03d}-s{book['anchor_slot']:04d}"
        output_books.append({
            "book_id": stable_id,
            **book,
            "canonical_title": canonical,
            "title_status": status,
            "title_score": top["score"] if cross_checked else None,
            "title_margin": margin if top else None,
            "verification_basis": (["first_page_asr", *top["sources"]] if cross_checked
                                   else ["first_page_asr"]),
            "current_title_matches": bool(book["current_title"] and canonical
                                          and normalize(book["current_title"]) == normalize(canonical)),
            "candidates": ranked,
        })
    if overrides:
        if overrides.get("schema_version") != "raz-book-title-overrides/1.0":
            raise ValueError("Expected raz-book-title-overrides/1.0")
        known = {book["book_id"]: book for book in output_books}
        seen = set()
        for item in overrides.get("books", []):
            book_id, title = item.get("book_id"), item.get("canonical_title")
            if book_id in seen:
                raise ValueError(f"Duplicate title override: {book_id}")
            if book_id not in known:
                raise ValueError(f"Unknown title override book_id: {book_id}")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"Override canonical_title is required: {book_id}")
            seen.add(book_id)
            book = known[book_id]
            method = item.get("review_method", "human_review")
            if method not in ("human_review", "external_source_cross_check"):
                raise ValueError(f"Unsupported review_method for {book_id}: {method}")
            book["canonical_title"] = title.strip()
            book["title_status"] = ("human_reviewed" if method == "human_review"
                                    else "externally_cross_checked")
            book["verification_basis"] = [method]
            book["human_review"] = {key: item.get(key) for key in
                                    ("review_method", "reviewed_by", "reviewed_at", "note",
                                     "evidence_urls")}
            book["current_title_matches"] = bool(book["current_title"] and
                                                   normalize(book["current_title"]) == normalize(title))
    return {
        "schema_version": "raz-book-title-map/1.0",
        "algorithm": ALGORITHM,
        "collection_id": collection_id,
        "source_package_sha256": json.loads((split_root / "index.json").read_text(
            encoding="utf-8"))["source_sha256"],
        "thresholds": {"min_score": min_score, "min_margin": min_margin},
        "summary": {
            "books": len(output_books),
            "reference_titles": len(refs),
            "cross_checked": sum(b["title_status"] == "cross_checked" for b in output_books),
            "human_reviewed": sum(b["title_status"] == "human_reviewed" for b in output_books),
            "externally_cross_checked": sum(b["title_status"] == "externally_cross_checked"
                                             for b in output_books),
            "needs_review": sum(b["title_status"].endswith("needs_review") for b in output_books),
            "correct_current_title": sum(b["current_title_matches"] for b in output_books),
            "corrected_or_new_title": sum(not b["title_status"].endswith("needs_review")
                                          and not b["current_title_matches"] for b in output_books),
        },
        "books": output_books,
    }


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def save_review_csv(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["book_index", "book_id", "source_folder", "current_title", "observed_title",
               "canonical_title", "title_status", "title_score", "title_margin",
               "current_title_matches"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(payload["books"])


def override_template(payload: dict) -> dict:
    return {
        "schema_version": "raz-book-title-overrides/1.0",
        "instructions": "Only keep reviewed rows and fill canonical_title/review metadata.",
        "books": [{
            "book_id": book["book_id"],
            "source_folder": book["source_folder"],
            "observed_title": book["observed_title"],
            "canonical_title": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "review_method": "human_review",
            "evidence_urls": [],
            "note": None,
        } for book in payload["books"] if book["title_status"].endswith("needs_review")],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("split_root", type=Path)
    parser.add_argument("--asr-root", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, action="append", required=True)
    parser.add_argument("--reference-file", type=Path, action="append", default=[],
                        help="raz-title-reference/1.0 JSON; repeatable")
    parser.add_argument("--collection-id", default="raz-p-071")
    parser.add_argument("--book", action="append", default=[], metavar="GLOB",
                        help="Only matching split-book folders; repeatable")
    parser.add_argument("--min-score", type=float, default=0.62)
    parser.add_argument("--min-margin", type=float, default=0.15)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-csv", type=Path,
                        help="Optional spreadsheet-friendly title review list")
    parser.add_argument("--overrides", type=Path,
                        help="Optional reviewed raz-book-title-overrides/1.0 JSON")
    parser.add_argument("--overrides-template", type=Path,
                        help="Write a template containing unresolved books")
    args = parser.parse_args()
    overrides = json.loads(args.overrides.read_text(encoding="utf-8")) if args.overrides else None
    report = reconcile(args.split_root.resolve(), args.asr_root.resolve(), args.reference_dir,
                       args.collection_id, args.min_score, args.min_margin, overrides,
                       args.reference_file, args.book)
    save_json(args.output.resolve(), report)
    if args.review_csv:
        save_review_csv(args.review_csv.resolve(), report)
    if args.overrides_template:
        save_json(args.overrides_template.resolve(), override_template(report))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
