"""Render original pages and align offline word coordinates with lesson source units.

Requires Poppler, Pillow, and the optional RapidOCR environment. No OCR runs on children's devices.
Raw OCR is a positioning aid only: displayed English always comes from the source image/lesson.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import difflib
import hashlib
import json
import statistics
from pathlib import Path
import shutil
import subprocess
import unicodedata

from build_reader_content import CONTENT_ROOT, ROOT, build_lessons, read_json, write_json


def normalized(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text).casefold() if c.isalnum())


def ocr_fingerprint(ocr: dict) -> str:
    identity = {'width': ocr['width'], 'height': ocr['height'],
                'words': [{key: w[key] for key in ('text', 'line', 'x', 'y', 'width', 'height')} for w in ocr['words']]}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def spatial_locator(locator: dict) -> dict:
    """Reorder positioned PDF words by their visual rows when the font stream is scrambled."""
    words = [word for word in locator['words'] if normalized(word['text'])]
    heights = [word['height'] for word in words if word['height'] > 0]
    tolerance = max(8.0, (statistics.median(heights) if heights else 10) * .8)
    lines = []
    for word in sorted(words, key=lambda item: (item['y'] + item['height'] / 2, item['x'])):
        center = word['y'] + word['height'] / 2
        candidates = [line for line in lines if abs(line['center'] - center) <= tolerance]
        if candidates:
            line = min(candidates, key=lambda item: abs(item['center'] - center))
            line['words'].append(word)
            line['center'] = sum(w['y'] + w['height'] / 2 for w in line['words']) / len(line['words'])
        else:
            lines.append({'center': center, 'words': [word]})
    ordered = []
    for line_number, line in enumerate(sorted(lines, key=lambda item: item['center'])):
        ordered.extend({**word, 'line': line_number} for word in sorted(line['words'], key=lambda item: item['x']))
    return {**locator, 'engine': 'pdf-text-spatial', 'words': ordered}


def column_locator(locator: dict, column_count: int) -> dict:
    """Try column-major reading order while preserving each OCR-detected text row."""
    rows = defaultdict(list)
    for word in locator['words']:
        rows[word['line']].append(word)
    ordered_rows = []
    for row in rows.values():
        left = min(word['x'] for word in row)
        right = max(word['x'] + word['width'] for word in row)
        top = min(word['y'] for word in row)
        center = (left + right) / 2
        column = min(column_count - 1, int(center * column_count / locator['width']))
        ordered_rows.append((column, top, left, row))
    words = []
    for line_number, (_, _, _, row) in enumerate(
            sorted(ordered_rows, key=lambda item: item[:3])):
        words.extend({**word, 'line': line_number} for word in sorted(row, key=lambda item: item['x']))
    return {**locator, 'engine': f"{locator.get('engine', 'ocr')}-columns-{column_count}", 'words': words}


def find_candidates(text: str, words: list[dict]) -> list[dict]:
    target = normalized(text)
    tokens = [normalized(w['text']) for w in words]
    corpus = ''.join(tokens)
    starts, ends, offset = {}, {}, 0
    for i, token in enumerate(tokens):
        if token:
            starts[offset] = i
            offset += len(token)
            ends[offset] = i + 1
    found = []
    position = corpus.find(target) if target else -1
    while position >= 0:
        finish = position + len(target)
        if position in starts and finish in ends:
            found.append({'start': starts[position], 'end': ends[finish], 'score': 1.0})
        position = corpus.find(target, position + 1)
    if found:
        return found
    if len(target) < 6:
        return []
    allowance = max(3, round(len(target) * .12))
    for start, token in enumerate(tokens):
        if not token:
            continue
        candidate = ''
        for end in range(start, len(tokens)):
            candidate += tokens[end]
            if len(candidate) > len(target) + allowance:
                break
            if not tokens[end] or len(candidate) < len(target) - allowance:
                continue
            score = difflib.SequenceMatcher(None, target, candidate, autojunk=False).ratio()
            if score >= .75:
                found.append({'start': start, 'end': end + 1, 'score': round(score, 5)})
    found.sort(key=lambda item: (-item['score'], item['start']))
    # Windows of the same occurrence compete as one location, not as ambiguous alternatives.
    locations = []
    for item in found:
        if all(max(0, min(item['end'], other['end']) - max(item['start'], other['start'])) /
               max(1, min(item['end'] - item['start'], other['end'] - other['start'])) < .5 for other in locations):
            locations.append(item)
        if len(locations) == 3:
            break
    return locations


def rectangles(words: list[dict], width: int, height: int) -> list[dict]:
    lines = defaultdict(list)
    heights = [word['height'] for word in words if word['height'] > 0]
    median_height = statistics.median(heights) if heights else 0
    malformed = [word for word in words if median_height and word['height'] > median_height * 1.8]
    if malformed:
        # Some PDF text layers join a hyphenated word across two printed rows into one
        # oversized box. Do not let that box swallow adjacent sentences; regroup the
        # remaining words by their visual rows instead of trusting the PDF line number.
        tolerance = max(8.0, median_height * .8)
        visual_lines = []
        for word in sorted((word for word in words if word not in malformed),
                           key=lambda item: (item['y'] + item['height'] / 2, item['x'])):
            center = word['y'] + word['height'] / 2
            candidates = [line for line in visual_lines if abs(line['center'] - center) <= tolerance]
            if candidates:
                line = min(candidates, key=lambda item: abs(item['center'] - center))
                line['words'].append(word)
                line['center'] = sum(w['y'] + w['height'] / 2 for w in line['words']) / len(line['words'])
            else:
                visual_lines.append({'center': center, 'words': [word]})
        for index, line in enumerate(sorted(visual_lines, key=lambda item: item['center'])):
            lines[index].extend(line['words'])
    else:
        for word in words:
            lines[word['line']].append(word)
    result = []
    for line in lines.values():
        left = max(0, min(w['x'] for w in line) - 2)
        top = max(0, min(w['y'] for w in line) - 3)
        right = min(width, max(w['x'] + w['width'] for w in line) + 2)
        bottom = min(height, max(w['y'] + w['height'] for w in line) + 3)
        result.append({'x': round(left, 2), 'y': round(top, 2), 'width': round(right - left, 2), 'height': round(bottom - top, 2)})
    return result


def align_page(page: dict, ocr: dict, overrides: dict) -> tuple[list, list]:
    words = ocr['words']
    hotspots, review = [], []
    cursor = None
    explained = {ref for block in page['blocks'] for ref in block['sourceRefs']}
    for unit in page['sourceUnits']:
        if unit['id'] not in explained:
            continue  # Credits without a lesson are displayed in the image, without a dead control.
        candidates = find_candidates(unit['text'], words)
        best = candidates[0] if candidates else None
        ordered = [candidate for candidate in candidates
                   if candidate['score'] == 1.0 and cursor is not None and candidate['start'] >= cursor]
        order_resolved = len(candidates) > 1 and bool(ordered)
        if order_resolved:
            best = ordered[0]
        override = overrides.get(unit['id'])
        selected = None
        override_rects = None
        if override:
            if not override.get('reason'):
                raise ValueError(f"Override needs a review reason: {unit['id']}")
            if 'rects' in override:
                bound = (override.get('sourceText') == unit['text'] and
                         override.get('imageSha256') == ocr.get('imageSha256') and
                         override.get('layoutWidth') == ocr['width'] and
                         override.get('layoutHeight') == ocr['height'])
                rects = override['rects']
                valid_rects = (isinstance(rects, list) and bool(rects) and
                               all(isinstance(rect, dict) and set(rect) == {'x', 'y', 'width', 'height'} and
                                   all(type(rect[key]) in (int, float) for key in rect) and
                                   rect['x'] >= 0 and rect['y'] >= 0 and rect['width'] > 0 and rect['height'] > 0 and
                                   rect['x'] + rect['width'] <= ocr['width'] and
                                   rect['y'] + rect['height'] <= ocr['height'] for rect in rects))
                if bound and valid_rects:
                    override_rects = rects
                else:
                    override = None
            else:
                bound = override.get('ocrSha256') == ocr_fingerprint(ocr) and override.get('sourceText') == unit['text']
                # Bound overrides only apply to the locator they were reviewed against. A page can
                # try PDF-text and OCR locators with different word counts, so validating a stale
                # locator's indices would reject an otherwise valid reviewed mapping.
                if ('ocrSha256' in override or 'sourceText' in override) and not bound:
                    override = None
                else:
                    indices = override.get('word_indices')
                    if indices is None:
                        start, end = override.get('word_start'), override.get('word_end')
                        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(words):
                            raise ValueError(f"Invalid override word range: {unit['id']}")
                        indices = list(range(start, end))
                    if not indices or len(set(indices)) != len(indices) or not all(type(i) is int and 0 <= i < len(words) for i in indices):
                        raise ValueError(f"Invalid override word range: {unit['id']}")
                    if bound:
                        selected = [words[i] for i in indices]
                    else:
                        override = None  # Legacy unbound entries are validated but never trusted.
        repeated_equivalent = (unit.get('kind') in {'title', 'heading', 'label', 'caption', 'glossary'} and
                               len(candidates) > 1 and best and best['score'] >= .95 and
                               len({normalized(' '.join(word['text'] for word in words[c['start']:c['end']]))
                                    for c in candidates}) == 1)
        if repeated_equivalent and unit.get('kind') in {'title', 'heading'}:
            def heading_rank(candidate: dict) -> tuple[int, float]:
                selected_lines = {word['line'] for word in words[candidate['start']:candidate['end']]}
                outside_words = sum(word['line'] in selected_lines for word in words) - (
                    candidate['end'] - candidate['start'])
                area = sum(word['width'] * word['height']
                           for word in words[candidate['start']:candidate['end']])
                return -outside_words, area
            best = max(candidates, key=heading_rank)
        unique_fuzzy = len(candidates) == 1 and best and best['score'] >= .85
        separated_fuzzy = (best and best['score'] >= .95 and len(candidates) > 1 and
                           candidates[0]['score'] - candidates[1]['score'] >= .035)
        exact_confident = best and best['score'] == 1.0 and (
            len(candidates) == 1 or repeated_equivalent or order_resolved)
        confident = best and (exact_confident or unique_fuzzy or separated_fuzzy or order_resolved)
        if not (confident or override):
            review.append({'pageId': page['id'], 'unitId': unit['id'], 'text': unit['text'],
                           'candidates': [{**c, 'text': ' '.join(w['text'] for w in words[c['start']:c['end']])} for c in candidates]})
            continue
        if override_rects is not None:
            hotspots.append({'unitId': unit['id'], 'rects': override_rects,
                             'score': 1.0, 'method': 'reviewed'})
            continue
        selected = selected if selected is not None else words[best['start']:best['end']]
        hotspots.append({'unitId': unit['id'], 'rects': rectangles(selected, ocr['width'], ocr['height']),
                         'score': 1.0 if override else best['score'], 'method': 'reviewed' if override else 'ocr-text-match'})
        if selected:
            selected_indices = [words.index(word) for word in selected]
            cursor = max(selected_indices) + 1 if cursor is None else max(cursor, max(selected_indices) + 1)
    return hotspots, review


def locate_page(page: dict, image_path: Path, locator_paths: list[Path],
                overrides: dict) -> tuple[dict, list, list]:
    """Merge the best per-unit matches from every available reading-order strategy."""
    locator_results = []
    image_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
    for locator_path in locator_paths:
        locator = json.loads(locator_path.read_text(encoding='utf-8-sig'))
        locator['imageSha256'] = image_sha
        locators = [locator, spatial_locator(locator),
                    column_locator(locator, 2), column_locator(locator, 3)]
        for candidate_locator in locators:
            located, unresolved = align_page(page, candidate_locator, overrides)
            locator_results.append((candidate_locator, located, unresolved))
    ocr = locator_results[0][0]
    if any((candidate['width'], candidate['height']) != (ocr['width'], ocr['height'])
           for candidate, _, _ in locator_results):
        raise ValueError(f"Locator dimensions disagree: {page['id']}")
    hotspot_options = defaultdict(list)
    review_candidates = defaultdict(list)
    for _, located, unresolved in locator_results:
        for hotspot in located:
            signature = tuple(tuple(sorted(rect.items())) for rect in hotspot['rects'])
            if all(tuple(tuple(sorted(rect.items())) for rect in option['rects']) != signature
                   for option in hotspot_options[hotspot['unitId']]):
                hotspot_options[hotspot['unitId']].append(hotspot)
        for item in unresolved:
            review_candidates[item['unitId']].append(item)
    explained = {ref for block in page['blocks'] for ref in block['sourceRefs']}
    units = [unit for unit in page['sourceUnits'] if unit['id'] in explained]
    # Choose constrained, longer matches first. Short labels often repeat inside a
    # body sentence; selecting the sentence first lets the label use its distinct
    # diagram or table occurrence instead of overlapping the sentence control.
    ranked_units = sorted(units, key=lambda unit: (
        0 if any(option['method'] == 'reviewed' for option in hotspot_options[unit['id']]) else 1,
        -len(normalized(unit['text'])),
        len(hotspot_options[unit['id']]) or 10_000,
    ))
    selected = {}
    for unit in ranked_units:
        options = sorted(hotspot_options[unit['id']],
                         key=lambda item: (item['method'] == 'reviewed', item['score']),
                         reverse=True)
        for option in options:
            existing = list(selected.values())
            if not hotspot_conflicts([*existing, option]) and not hotspot_conflicts([option, *existing]):
                selected[unit['id']] = option
                break
    hotspots, missing = [], []
    for unit in units:
        if unit['id'] in selected:
            hotspots.append(selected[unit['id']])
            continue
        candidates = review_candidates[unit['id']]
        if candidates:
            missing.append(max(candidates, key=lambda item: (
                item['candidates'][0]['score'] if item['candidates'] else -1,
                -len(item['candidates']),
            )))
        else:
            missing.append({'pageId': page['id'], 'unitId': unit['id'],
                            'text': unit['text'], 'candidates': [],
                            'reason': 'all located candidates overlap another unit'})
    return ocr, hotspots, missing


def hotspot_conflicts(hotspots: list[dict]) -> list[tuple[str, str]]:
    """Mirror the reader's center-line hit test so overlapping controls cannot ship."""
    conflicts = set()
    for index, hotspot in enumerate(hotspots):
        for rect in hotspot['rects']:
            for fraction in (.2, .5, .8):
                x = rect['x'] + rect['width'] * fraction
                y = rect['y'] + rect['height'] / 2
                hit = None
                for candidate in hotspots[index + 1:]:
                    if any(other['x'] <= x <= other['x'] + other['width'] and
                           other['y'] <= y <= other['y'] + other['height']
                           for other in candidate['rects']):
                        hit = candidate['unitId']
                if hit:
                    conflicts.add((hotspot['unitId'], hit))
    return sorted(conflicts)


def render_print_imposed(pdf: Path, entry: dict, book: dict, book_cache: Path,
                         height: int, pdf_python: Path) -> dict[str, Path]:
    """Split a printable booklet and normalize every reading page to the target height."""
    from PIL import Image
    split_root = book_cache / 'split-source'
    split_folder = split_root / pdf.stem
    manifest_path = split_folder / 'manifest.json'
    pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    manifest = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    split_page_count = len(book['pageIds']) + (0 if entry.get('include_back_cover', True) else 1)
    valid = (manifest and manifest.get('schema_version') == 2 and
             manifest.get('pdf_sha256') == pdf_sha and
             manifest.get('page_count') == split_page_count and
             all((split_folder / page['file']).is_file() and
                 (split_folder / page.get('text_file', '')).is_file()
                 for page in manifest.get('pages', [])))
    if not valid:
        if not pdf_python.is_file():
            raise SystemExit('Install requirements-reader-pdf.txt in .local/reader-pdf, or pass --pdf-python.')
        subprocess.run([
            str(pdf_python), str(ROOT / 'tools/split_book_pages.py'),
            '--source', str(pdf.parents[2]), '--output', str(split_root),
            '--dpi', '360', '--only', pdf.parents[1].name,
        ], check=True)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('pdf_sha256') != pdf_sha or manifest.get('page_count') != split_page_count:
        raise ValueError(f"Printable PDF split does not match lessons: {book['id']}")
    source_pages = manifest['pages']
    if not entry.get('include_back_cover', True):
        if source_pages[-1].get('role') != 'back_cover':
            raise ValueError(f"Expected final split page to be the back cover: {book['id']}")
        source_pages = source_pages[:-1]
    count = len(source_pages)
    schemes = {
        'reading-order': list(range(1, count + 1)),
        'cover-back-body-title-copyright': [1, count, *range(4, count), 2, 3],
        'copyright-before-back': [1, 2, *range(4, count), 3, count],
        'copyright-last': [1, 2, *range(4, count + 1), 3],
    }
    scheme = entry.get('split_order', 'reading-order')
    if scheme not in schemes or sorted(schemes[scheme]) != list(range(1, count + 1)):
        raise ValueError(f"Invalid split_order for {book['id']}: {scheme!r}")
    source_pages = [source_pages[index - 1] for index in schemes[scheme]]

    inputs = {}
    for page_id, source_meta in zip(book['pageIds'], source_pages, strict=True):
        source = split_folder / source_meta['file']
        source_number = source_meta['reading_order']
        image = book_cache / f'source-{source_number:03}.png'
        # Migrate the first build's index-named cache without re-running OCR; the bytes are
        # identical because that build assigned split page N to lesson page N.
        legacy = book_cache / f"{book['pageIds'][source_number - 1]}.png"
        if not image.exists() and legacy.exists():
            shutil.copy2(legacy, image)
            legacy_ocr = legacy.with_suffix('.rapid-3.9.2.json')
            if legacy_ocr.exists():
                shutil.copy2(legacy_ocr, image.with_suffix('.rapid-3.9.2.json'))
            legacy_webp = legacy.with_suffix('.q92.webp')
            if legacy_webp.exists():
                shutil.copy2(legacy_webp, image.with_suffix('.q92.webp'))
        if not image.exists():
            with Image.open(source) as original:
                width = max(1, round(original.width * height / original.height))
                resized = original.convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
                resized.save(image, 'PNG', optimize=True)
        source_text = json.loads((split_folder / source_meta['text_file']).read_text(encoding='utf-8'))
        positioned = image.with_suffix('.pdf-text.json')
        if not positioned.exists():
            from PIL import Image
            with Image.open(image) as normalized_image:
                width, normalized_height = normalized_image.size
            sx, sy = width / source_text['width'], normalized_height / source_text['height']
            words = [{**word, 'x': round(word['x'] * sx, 2), 'y': round(word['y'] * sy, 2),
                      'width': round(word['width'] * sx, 2),
                      'height': round(word['height'] * sy, 2)} for word in source_text['words']]
            positioned.write_text(json.dumps({'engine': 'pdf-text', 'width': width,
                                              'height': normalized_height, 'words': words},
                                             ensure_ascii=False, separators=(',', ':')) + '\n',
                                  encoding='utf-8')
        inputs[page_id] = (image, positioned)
    return inputs


def find_binding_guide(image) -> tuple[int, int] | None:
    """Find the first thin printable guide inside an otherwise blank left strip."""
    width, height = image.size
    rgb = image.convert('RGB')
    sample_step = max(1, height // 240)
    sample_rows = range(0, height, sample_step)
    candidates = []
    for x in range(max(2, round(width * .02)), max(3, round(width * .15))):
        # Inspect every row so a dotted guide cannot alias with the sampling step.
        column = [rgb.getpixel((x, y)) for y in range(height)]
        line_ratio = sum(max(pixel) - min(pixel) <= 15 and sum(pixel) / 3 < 248
                         for pixel in column) / len(column)
        if line_ratio < .40:
            continue
        before = [rgb.getpixel((before_x, y))
                  for before_x in range(0, x, max(1, x // 24)) for y in sample_rows]
        blank_ratio = sum(min(pixel) > 242 and max(pixel) - min(pixel) < 15
                          for pixel in before) / len(before)
        if blank_ratio >= .96:
            candidates.append(x)
    if not candidates:
        return None
    # Only the first thin line after the blank margin is the printable guide.
    # A later run may be the cover artwork's own rectangular border.
    first_run = [candidates[0]]
    for x in candidates[1:]:
        if x > first_run[-1] + 2:
            break
        first_run.append(x)
    return min(first_run), max(first_run)


def cover_crop_box(image, setting=None) -> tuple[int, int, int, int]:
    """Crop only the printable strip on the left unless explicitly overridden."""
    width, height = image.size
    if setting is False:
        return 0, 0, width, height
    if setting is None:
        guide = find_binding_guide(image)
        return (guide[1] + 1, 0, width, height) if guide else (0, 0, width, height)
    if not isinstance(setting, dict):
        raise ValueError('coverCrop must be false or an object with a fractional left edge')
    if set(setting) != {'left'} or type(setting['left']) not in (int, float) or not 0 <= setting['left'] < 1:
        raise ValueError('coverCrop must contain only left, as a number from 0 up to 1')
    return round(width * setting['left']), 0, width, height


def cover_recipe(setting=None) -> str:
    value = json.dumps({'algorithm': 5, 'coverCrop': setting}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def resolved_cover_crop(config: dict):
    """Only print-imposed PDFs need automatic binding-guide removal by default."""
    if 'coverCrop' in config:
        return config['coverCrop']
    return None if config.get('pdfLayout') == 'print-imposed' else False


def make_cover(source: Path, cached: Path, destination: Path, relative_path: str,
               crop_setting=None) -> dict:
    """Create a light bookshelf thumbnail without printable binding guides."""
    from PIL import Image
    if not cached.exists():
        with Image.open(source) as original:
            cover = original.convert('RGB')
            cover.thumbnail((480, 720), Image.Resampling.LANCZOS)
            box = cover_crop_box(cover, crop_setting)
            if box != (0, 0, *cover.size):
                cover = cover.crop(box)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cover.save(cached, 'WEBP', quality=88, method=4)
    cover_bytes = cached.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != cover_bytes:
        destination.write_bytes(cover_bytes)
    with Image.open(cached) as cover:
        width, height = cover.size
    return {'image': relative_path, 'width': width, 'height': height,
            'imageSha256': hashlib.sha256(cover_bytes).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--height', type=int, default=2000)
    parser.add_argument('--ocr-python', type=Path, default=ROOT / '.local/reader-ocr/Scripts/python.exe')
    parser.add_argument('--pdf-python', type=Path, default=ROOT / '.local/reader-pdf/Scripts/python.exe')
    parser.add_argument('--allow-review', action='store_true', help='Write a draft layout while unresolved matches are reviewed.')
    parser.add_argument('--covers-only', action='store_true',
                        help='Rebuild bookshelf covers from existing first-page images without PDF/OCR.')
    parser.add_argument('--level', action='append',
                        help='Only build the named RAZ level; repeat to select multiple levels.')
    args = parser.parse_args()
    from PIL import Image
    library, sources = build_lessons(CONTENT_ROOT)
    if args.level:
        selected_levels = {level.upper() for level in args.level}
        sources = [source for source in sources if source['config']['level'].upper() in selected_levels]
        if not sources:
            raise SystemExit(f'No enabled books found for level(s): {sorted(selected_levels)}')
    cache = ROOT / '.local/original-pages'
    cache.mkdir(parents=True, exist_ok=True)
    if args.covers_only:
        for source in sources:
            book = next(item for item in library['books'] if item['id'] == source['config']['id'])
            page_name = book['pageIds'][0].rsplit('-', 1)[-1]
            page_image = source['root'] / 'pages' / page_name / 'page.webp'
            if not page_image.is_file():
                raise ValueError(f'Missing first-page image: {book["id"]}')
            digest = hashlib.sha256(page_image.read_bytes()).hexdigest()[:12]
            crop_setting = resolved_cover_crop(source['config'])
            cached = cache / 'covers-only' / f'{book["id"]}-{digest}' / (
                f'cover-{cover_recipe(crop_setting)}.w480.q88.webp')
            relative = f"{source['relativeRoot']}/cover.webp"
            cover = make_cover(page_image, cached, source['root'] / 'cover.webp', relative,
                               crop_setting)
            layout_path = source['source'] / 'page-layout.json'
            layout = read_json(layout_path)
            layout['cover'] = cover
            write_json(layout_path, layout)
            print(f'Rebuilt cover: {book["title"]}', flush=True)
        return
    renderer = shutil.which('pdftoppm')
    if not renderer:
        raise SystemExit('pdftoppm (Poppler) must be on PATH.')
    jobs, page_inputs, book_hashes, covers, page_sources = [], {}, {}, {}, {}
    for source in sources:
        config = source['config']
        book = next(item for item in library['books'] if item['id'] == config['id'])
        pdf = (source['source'] / 'original.pdf').resolve()
        if not pdf.is_relative_to(source['root'].resolve()) or not pdf.is_file():
            raise ValueError(f"PDF source must be inside its book: {book['id']}")
        pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        book_hashes[book['id']] = pdf_sha
        book_cache = cache / f"{book['id']}-{pdf_sha[:12]}-{args.height}"
        book_cache.mkdir(exist_ok=True)
        entry = {'split_order': config.get('splitOrder'),
                 'include_back_cover': config.get('includeBackCover', True)}
        pdf_layout = config.get('pdfLayout')
        if pdf_layout == 'print-imposed':
            rendered = render_print_imposed(pdf, entry, book, book_cache, args.height, args.pdf_python)
        elif pdf_layout == 'single-pages':
            rendered = {}
            for index, page_id in enumerate(book['pageIds'], 1):
                image = book_cache / f'{page_id}.png'
                if not image.exists():
                    subprocess.run([renderer, '-f', str(index), '-l', str(index), '-singlefile',
                                    '-scale-to', str(args.height), '-png', str(pdf),
                                    str(image.with_suffix(''))], check=True)
                rendered[page_id] = (image, None)
        else:
            raise ValueError(f"Unknown pdf_layout for {book['id']}: {pdf_layout!r}")
        for page_id in book['pageIds']:
            image, positioned = rendered[page_id]
            rapid = image.with_suffix('.rapid-3.9.2-en-v4.json')
            legacy_rapid = image.with_suffix('.rapid-3.9.2.json')
            # Printable PDFs already provide scaled word boxes through `positioned`.
            # Use those first and reserve image OCR for pages without a text layer or
            # for the targeted fallback pass below. Running full-page OCR eagerly on
            # every text-backed page makes a new level take hours without improving
            # exact PDF-text matches.
            needs_rapid = not rapid.exists() and positioned is None
            if needs_rapid:
                jobs.append({'id': page_id, 'image': str(image), 'output': str(rapid)})
            locator_paths = [path for path in (positioned,) if path and path.exists()]
            if rapid.exists() or needs_rapid:
                locator_paths.append(rapid)
            if legacy_rapid.exists():
                locator_paths.append(legacy_rapid)
            page_inputs[page_id] = (image, locator_paths)
            page_sources[page_id] = source
        cover_relative = f"{source['relativeRoot']}/cover.webp"
        crop_setting = resolved_cover_crop(config)
        covers[book['id']] = make_cover(
            rendered[book['pageIds'][0]][0],
            book_cache / f"cover-{cover_recipe(crop_setting)}.w480.q88.webp",
            source['root'] / 'cover.webp', cover_relative, crop_setting)
        print(f"Rendered {book['title']}: {len(book['pageIds'])} pages", flush=True)
    if jobs:
        if not args.ocr_python.is_file():
            raise SystemExit('Install requirements-reader-ocr.txt in .local/reader-ocr, or pass --ocr-python.')
        manifest = cache / 'ocr-jobs.json'
        manifest.write_text(json.dumps(jobs, ensure_ascii=False), encoding='utf-8')
        subprocess.run([str(args.ocr_python), str(ROOT / 'tools/ocr_page_images.py'), str(manifest)], check=True)
    overrides = {}
    for source in sources:
        override_file = source['source'] / 'page-layout-overrides.json'
        if override_file.exists():
            overrides.update(json.loads(override_file.read_text(encoding='utf-8')))
    page_matches = {}
    fallback_pages = []
    for page_id, (image_path, locator_paths) in page_inputs.items():
        match = locate_page(library['pages'][page_id], image_path,
                            locator_paths, overrides)
        page_matches[page_id] = match
        _, _, missing = match
        if missing:
            fallback_pages.append(page_id)
    # A normal full-page pass is much cheaper than the tiled low-threshold pass,
    # so try it first for the relatively small set the PDF text layer missed.
    fallback_jobs = []
    for page_id in fallback_pages:
        image_path, locator_paths = page_inputs[page_id]
        rapid = image_path.with_suffix('.rapid-3.9.2-en-v4.json')
        if not rapid.exists():
            fallback_jobs.append({'id': page_id, 'image': str(image_path),
                                  'output': str(rapid)})
        if rapid not in locator_paths:
            locator_paths.append(rapid)
    if fallback_jobs:
        manifest = cache / 'ocr-fallback-jobs.json'
        manifest.write_text(json.dumps(fallback_jobs, ensure_ascii=False), encoding='utf-8')
        subprocess.run([str(args.ocr_python), str(ROOT / 'tools/ocr_page_images.py'),
                        str(manifest)], check=True)
    tiled_pages = []
    for page_id in fallback_pages:
        image_path, locator_paths = page_inputs[page_id]
        match = locate_page(library['pages'][page_id], image_path,
                            locator_paths, overrides)
        page_matches[page_id] = match
        _, _, missing = match
        if missing:
            tiled_pages.append(page_id)
    tiled_jobs = []
    for page_id in tiled_pages:
        image_path, locator_paths = page_inputs[page_id]
        tiled_rapid = image_path.with_suffix('.rapid-3.9.2-en-v5-tiles2.json')
        if not tiled_rapid.exists():
            tiled_jobs.append({'id': page_id, 'image': str(image_path),
                               'output': str(tiled_rapid)})
        if tiled_rapid not in locator_paths:
            locator_paths.append(tiled_rapid)
    if tiled_jobs:
        manifest = cache / 'ocr-tiled-fallback-jobs.json'
        manifest.write_text(json.dumps(tiled_jobs, ensure_ascii=False), encoding='utf-8')
        subprocess.run([str(args.ocr_python), str(ROOT / 'tools/ocr_page_images.py'),
                        str(manifest), '--ocr-version', '5', '--horizontal-tiles', '2',
                        '--text-score', '.15', '--box-thresh', '.3'], check=True)
    for page_id in tiled_pages:
        image_path, locator_paths = page_inputs[page_id]
        page_matches[page_id] = locate_page(library['pages'][page_id], image_path,
                                            locator_paths, overrides)
    layout, review, conflict_review = {}, [], []
    for page_id, (image_path, locator_paths) in page_inputs.items():
        page = library['pages'][page_id]
        ocr, hotspots, missing = page_matches[page_id]
        conflicts = hotspot_conflicts(hotspots)
        if conflicts:
            conflict_review.append({'pageId': page_id, 'conflicts': conflicts})
        review.extend(missing)
        source = page_sources[page_id]
        page_name = page_id.rsplit('-', 1)[-1]
        webp = source['root'] / 'pages' / page_name / 'page.webp'
        webp.parent.mkdir(parents=True, exist_ok=True)
        cached_webp = image_path.with_suffix('.q92.webp')
        if not cached_webp.exists():
            with Image.open(image_path) as source_image:
                source_image.save(cached_webp, 'WEBP', quality=92, method=4)
        image_bytes = cached_webp.read_bytes()
        # The PDF hash is part of the cache directory; a changed source cannot reuse a stale public image.
        if not webp.exists() or webp.read_bytes() != image_bytes:
            webp.write_bytes(image_bytes)
        layout[page_id] = {'image': f"{source['relativeRoot']}/pages/{page_name}/page.webp",
                           'width': ocr['width'], 'height': ocr['height'],
                           'sourceSha256': library['pages'][page_id]['sha256'], 'imageSha256': hashlib.sha256(image_bytes).hexdigest(),
                           'hotspots': hotspots}
    report = cache / 'alignment-review.json'
    report.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    if conflict_review:
        conflict_report = cache / 'alignment-conflicts.json'
        conflict_report.write_text(json.dumps(conflict_review, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
        raise ValueError(f'Overlapping text locations on {len(conflict_review)} pages. '
                         f'See {conflict_report}')
    print(f"Aligned {sum(len(p['hotspots']) for p in layout.values())} text units; {len(review)} need review. See {report}")
    if review and not args.allow_review:
        raise SystemExit('Unresolved text locations: review before building the reader.')
    status = 'draft' if review else 'verified-mapping'
    for source in sources:
        book_id = source['config']['id']
        write_json(source['source'] / 'page-layout.json', {
            'schemaVersion': 'raz-page-layout/1.0', 'status': status,
            'pdfSha256': book_hashes[book_id], 'cover': covers[book_id],
            'pages': {page_id: page for page_id, page in layout.items()
                      if library['pages'][page_id]['bookId'] == book_id},
        })


if __name__ == '__main__':
    main()
