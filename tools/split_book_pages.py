"""Cut each RAZ printable sheet into its two book pages, numbered in reading order.

Every print-imposed source PDF carries two book pages: landscape sheets (792x612) hold them side
by side, portrait sheets (612x792) stack them. On the stacked interior sheets the lower page
is printed upside down so the sheet can be folded, but the cover sheet is not, so each half
is turned according to the text actually set inside it rather than by its position.

Sheet 1 always holds the two covers, which is why reading order is not sheet order: the front
cover comes first, the halves of sheets 2..N follow as the whole interior, and the back cover
comes last.

Sheets are cut at the exact midpoint, less a hairline gutter that drops the dashed cut mark
printed along the seam. The other dashed guide, drawn at 6 inches on landscape sheets, is a
staple mark rather than the page break: cutting there clips the following page's text, so it
survives as a faint line in the inner margin of the front cover.

Printed page numbers are derived from the imposition (sheet k yields 2k-3 and 2k-2) and
checked against the number actually printed in each page's footer; mismatches are recorded
in the manifest rather than raised, since a page may legitimately carry no number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

ROOT = Path(__file__).resolve().parent.parent
# Only the back cover invites the reader to fetch more books, which is what tells the two
# halves of sheet 1 apart; their position swaps between the landscape and portrait layouts.
BACK_COVER_MARKER = "thousands of books"
FOOTER_BAND = 0.12


def sheet_geometry(page) -> tuple[str, float, float, float]:
    """Return the split axis and midpoint of one sheet, plus its size in points."""
    width, height = page.get_size()
    axis = "x" if width > height else "y"
    return axis, (width if axis == "x" else height) / 2, width, height


def half_boxes(axis: str, cut: float, width: float, height: float,
               gutter: float) -> list[tuple]:
    """The two halves as (left, bottom, right, top), in the order they sit on the sheet."""
    low, high = cut - gutter, cut + gutter
    if axis == "x":
        return [(0.0, 0.0, low, height), (high, 0.0, width, height)]
    return [(0.0, high, width, height), (0.0, 0.0, width, low)]  # top half first


def gutter_intrusions(page, axis: str, cut: float, gutter: float) -> int:
    """How many text runs reach into the discarded seam, which should only hold the cut mark."""
    intruders = 0
    for obj in page.get_objects(max_depth=6):
        if obj.type != pdfium_c.FPDF_PAGEOBJ_TEXT:
            continue
        left, bottom, right, top = obj.get_bounds()
        low, high = (left, right) if axis == "x" else (bottom, top)
        intruders += low < cut + gutter and high > cut - gutter
    return intruders


def half_rotation(page, box: tuple) -> int:
    """0, or 180 when the text inside the box is set upside down."""
    left, bottom, right, top = box
    upright = flipped = 0
    for obj in page.get_objects(max_depth=6):
        if obj.type != pdfium_c.FPDF_PAGEOBJ_TEXT:
            continue
        x0, y0, x1, y1 = obj.get_bounds()
        if not (left <= (x0 + x1) / 2 <= right and bottom <= (y0 + y1) / 2 <= top):
            continue
        matrix = obj.get_matrix()
        if matrix.a > 0 and matrix.d > 0:
            upright += 1
        elif matrix.a < 0 and matrix.d < 0:
            flipped += 1
    return 180 if flipped > upright else 0


def box_text(textpage, box: tuple) -> str:
    left, bottom, right, top = box
    text = textpage.get_text_bounded(left=left, bottom=bottom, right=right, top=top)
    return re.sub(r"\s+", " ", text).strip()


def footer_box(box: tuple, rotation: int) -> tuple:
    """The strip along the page's own bottom edge, which flips with the page."""
    left, bottom, right, top = box
    band = (top - bottom) * FOOTER_BAND
    return (left, top - band, right, top) if rotation == 180 else (left, bottom, right, bottom + band)


def footer_check(textpage, box: tuple, entry: dict) -> bool | None:
    """Whether the footer confirms the derived page number, or None when it says nothing.

    RAZ leaves the title and copyright pages unnumbered, so an empty footer is no evidence
    either way; only a footer showing some other number contradicts the derived order.
    """
    if entry["printed_page"] is None:
        return None
    printed = re.findall(r"\d+", box_text(textpage, footer_box(box, entry["rotation"])))
    if not printed:
        return None
    return str(entry["printed_page"]) in printed


def cover_halves(page, boxes: list[tuple]) -> tuple[int, int]:
    """Index of the front cover and of the back cover within sheet 1."""
    textpage = page.get_textpage()
    marked = [i for i, box in enumerate(boxes)
              if BACK_COVER_MARKER in box_text(textpage, box).lower()]
    if len(marked) != 1:
        raise ValueError(f"Cannot tell the covers apart: {len(marked)} halves carry "
                         f"'{BACK_COVER_MARKER}'")
    back = marked[0]
    return 1 - back, back


def plan_book(doc, gutter: float) -> list[dict]:
    """One entry per output image, already in reading order."""
    axis, cut, width, height = sheet_geometry(doc[0])
    front, back = cover_halves(doc[0], half_boxes(axis, cut, width, height, gutter))
    side = ("left", "right") if axis == "x" else ("top", "bottom")

    plan = [{"sheet": 1, "half": front, "role": "front_cover", "printed_page": None}]
    for sheet in range(2, len(doc) + 1):
        for half in (0, 1):
            plan.append({"sheet": sheet, "half": half, "role": "interior",
                         "printed_page": 2 * sheet - 3 + half})
    plan.append({"sheet": 1, "half": back, "role": "back_cover", "printed_page": None})

    for number, entry in enumerate(plan, 1):
        page = doc[entry["sheet"] - 1]
        geometry = sheet_geometry(page)
        box = half_boxes(*geometry, gutter)[entry["half"]]
        entry.update({"number": number, "box": box, "side": side[entry["half"]],
                      "rotation": half_rotation(page, box),
                      "gutter_intrusions": gutter_intrusions(page, geometry[0], geometry[1],
                                                             gutter)})
        textpage = page.get_textpage()
        entry["empty"] = not box_text(textpage, box)
        entry["footer_matches"] = footer_check(textpage, box, entry)
    return plan


def relative_source(pdf_path: Path) -> str:
    path = pdf_path.resolve()
    if path.is_relative_to(ROOT):
        path = path.relative_to(ROOT)
    return str(path).replace("\\", "/")


def render_half(page, box: tuple, rotation: int, dpi: int):
    """Rasterise one half at the requested resolution, turned the right way up."""
    width, height = page.get_size()
    left, bottom, right, top = box
    insets = (left, bottom, width - right, height - top)
    image = page.render(scale=dpi / 72, crop=insets).to_pil()
    return image.rotate(180) if rotation == 180 else image


def text_words(page, box: tuple, rotation: int, image_size: tuple[int, int]) -> list[dict]:
    """Extract the PDF text layer as positioned words in the rendered half's pixel space."""
    left, bottom, right, top = box
    pixel_width, pixel_height = image_size
    sx, sy = pixel_width / (right - left), pixel_height / (top - bottom)
    textpage = page.get_textpage()
    words, chars, line = [], [], 0

    def flush() -> None:
        nonlocal chars
        if not chars:
            return
        text = ''.join(item[0] for item in chars)
        boxes = [item[1] for item in chars if item[1] is not None]
        chars = []
        if not text or not boxes:
            return
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        if rotation == 180:
            x, y = (right - x1) * sx, (y0 - bottom) * sy
        else:
            x, y = (x0 - left) * sx, (top - y1) * sy
        words.append({'text': text, 'line': line, 'x': round(x, 2), 'y': round(y, 2),
                      'width': round((x1 - x0) * sx, 2),
                      'height': round((y1 - y0) * sy, 2)})

    pending_break = False
    for index in range(textpage.count_chars()):
        char = textpage.get_text_range(index, 1)
        try:
            charbox = textpage.get_charbox(index)
        except Exception:
            charbox = None
        if charbox is not None:
            cx = (charbox[0] + charbox[2]) / 2
            cy = (charbox[1] + charbox[3]) / 2
            if not (left <= cx <= right and bottom <= cy <= top):
                continue
        if char.isspace():
            flush()
            if '\r' in char or '\n' in char:
                pending_break = True
            continue
        if pending_break:
            line += 1
            pending_break = False
        chars.append((char, charbox))
    flush()
    return words


def split_book(pdf_path: Path, output: Path, dpi: int, gutter: float, dry_run: bool) -> dict:
    doc = pdfium.PdfDocument(pdf_path)
    try:
        plan = plan_book(doc, gutter)
        digits = max(2, len(str(len(plan))))
        folder = output / pdf_path.stem
        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
        pages = []
        for entry in plan:
            name = f"{entry['number']:0{digits}d}.png"
            if not dry_run:
                source_page = doc[entry["sheet"] - 1]
                image = render_half(source_page, entry["box"], entry["rotation"], dpi)
                image.save(folder / name)
                positioned = {'schemaVersion': 'raz-pdf-text/1.0', 'width': image.width,
                              'height': image.height,
                              'words': text_words(source_page, entry['box'], entry['rotation'], image.size)}
                (folder / name.replace('.png', '.text.json')).write_text(
                    json.dumps(positioned, ensure_ascii=False, separators=(',', ':')) + '\n',
                    encoding='utf-8')
            pages.append({"file": name, "reading_order": entry["number"],
                          "text_file": name.replace('.png', '.text.json'),
                          "pdf_sheet": entry["sheet"], "sheet_half": entry["side"],
                          "rotated_180": entry["rotation"] == 180, "role": entry["role"],
                          "printed_page": entry["printed_page"],
                          "footer_matches_printed_page": entry["footer_matches"],
                          "no_text": entry["empty"]})
        axis, _, width, height = sheet_geometry(doc[0])
        warnings = [f"{p['file']}: footer says a different page number than {p['printed_page']}"
                    for p in pages if p["footer_matches_printed_page"] is False]
        warnings += [f"{p['file']}: no text in this half" for p in pages if p["no_text"]]
        warnings += [f"{entry['number']:0{digits}d}.png: {entry['gutter_intrusions']} text runs "
                     f"reach into the discarded seam" for entry in plan
                     if entry["gutter_intrusions"]]
        manifest = {
            "schema_version": 2,
            "book": pdf_path.stem,
            "pdf_source": relative_source(pdf_path),
            "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "sheet_size_pt": [round(width, 1), round(height, 1)],
            "layout": "side-by-side" if axis == "x" else "stacked",
            "sheet_count": len(doc),
            "page_count": len(plan),
            "dpi": dpi,
            "gutter_pt": gutter,
            "warnings": warnings,
            "pages": pages,
        }
        if not dry_run:
            (folder / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
    finally:
        doc.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=ROOT / "content/levels/P/books",
                        help="Book tree containing */_source/original.pdf")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis/book-pages")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--gutter", type=float, default=2.0,
                        help="Points dropped either side of the cut, to lose the printed cut "
                             "mark (default: 2)")
    parser.add_argument("--only", action="append", default=[],
                        help="Filename prefix to process; repeatable (default: every PDF)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the plan and the footer checks without writing images")
    args = parser.parse_args()

    pdfs = sorted(p for p in args.source.glob("*/_source/original.pdf")
                  if not args.only or any(p.parents[1].name.startswith(k) for k in args.only))
    if not pdfs:
        raise SystemExit(f"No matching PDF under {args.source}")

    index, warnings = [], []
    for pdf_path in pdfs:
        manifest = split_book(pdf_path, args.output, args.dpi, args.gutter, args.dry_run)
        warnings.extend(f"{manifest['book']}/{w}" for w in manifest["warnings"])
        index.append({key: manifest[key] for key in
                      ("book", "layout", "sheet_count", "page_count", "warnings")})
        print(f"{manifest['book']}: {manifest['sheet_count']} sheets -> "
              f"{manifest['page_count']} pages ({manifest['layout']})"
              f"{'  [' + str(len(manifest['warnings'])) + ' warnings]' if manifest['warnings'] else ''}",
              flush=True)

    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "index.json").write_text(
            json.dumps({"books": index, "dpi": args.dpi}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    print(f"\n{len(pdfs)} books, {sum(b['page_count'] for b in index)} pages, "
          f"{len(warnings)} warnings")
    for warning in warnings:
        print(f"  {warning}")


if __name__ == "__main__":
    main()
