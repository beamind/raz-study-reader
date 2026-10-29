"""Compile and validate the copy-ready reader content tree.

Every level and book owns its source material and reader assets under content/.
Files below `_source` are authoring inputs; everything else is safe to copy into
the web build without changing its relative path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import time

from check_lesson import HEADER, check_paths

ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = ROOT / 'content'
BLOCK = re.compile(
    r'^<!-- lesson-block: (.*?) -->\n<!-- lesson-kind: (.*?) -->\n'
    r'<!-- source-refs: (.*?) -->\n', re.M
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    for attempt in range(12):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(.05 * (attempt + 1))


def content_url(relative: str) -> str:
    return f'content/{relative}'


def parse_page(path: Path) -> dict:
    text = path.read_text(encoding='utf-8-sig')
    match = HEADER.match(text)
    if not match:
        raise ValueError(f'Missing lesson metadata: {path}')
    meta = json.loads(match[1])
    markers = list(BLOCK.finditer(text))
    blocks = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.end():end].strip()
        heading = re.search(r'^#{1,6}\s+(.+)$', body, re.M)
        title = heading[1] if heading else ''
        if heading:
            body = body[:heading.start()] + body[heading.end():]
        original = None
        if marker[2] == 'sentence':
            quote = re.search(r'^\*\*原文：\*\*[ \t]*(.*)$', body, re.M)
            original = quote[1]
            body = body[:quote.start()] + body[quote.end():]
        blocks.append({
            'id': marker[1], 'kind': marker[2], 'title': title,
            'sourceRefs': [] if marker[3] == 'none' else [item.strip() for item in marker[3].split(',')],
            'original': original, 'markdown': body.strip(),
        })
    titles = [unit['text'] for unit in meta['source_units'] if unit['kind'] == 'title']
    paper = meta['paper_page_label']
    label = f'第 {paper} 页' if paper and paper.isdigit() else paper
    if not label:
        label = '封面' if meta['pdf_page_number'] == 1 else ('扉页' if titles else '附页')
    return {
        'id': meta['page_id'], 'bookId': meta['book_id'], 'bookTitle': meta['book_title'],
        'level': meta['raz_level'], 'pdfPage': meta['pdf_page_number'],
        'paperLabel': paper, 'label': label, 'title': titles[0] if titles else label,
        'revision': meta['revision'], 'status': meta['generation_status'],
        'sourceUnits': meta['source_units'], 'blocks': blocks,
        'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
    }


def discover_books(content_root: Path) -> list[dict]:
    books = []
    for path in sorted(content_root.glob('levels/*/books/*/_source/book.json')):
        config = read_json(path)
        book_root = path.parents[1]
        level_root = path.parents[3]
        if config.get('schemaVersion') != 'raz-book-source/1.0':
            raise ValueError(f'Unsupported source book schema: {path}')
        if config.get('level') != level_root.name:
            raise ValueError(f'Book level does not match its directory: {path}')
        books.append({'config': config, 'root': book_root, 'source': path.parent,
                      'relativeRoot': book_root.relative_to(content_root).as_posix()})
    if not books:
        raise ValueError(f'No books found under {content_root / "levels"}')
    ids = [item['config']['id'] for item in books]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate book id in content tree')
    return sorted(books, key=lambda item: (item['config']['level'], item['config']['sourceNumber']))


def attach_original_pages(payload: dict, book_sources: list[dict], content_root: Path) -> None:
    """Attach verified page images and hotspots from each ready book."""
    from PIL import Image
    content_root = content_root.resolve()
    for source in book_sources:
        config, book_root = source['config'], source['root'].resolve()
        layout_path = source['source'] / 'page-layout.json'
        if not layout_path.is_file():
            raise ValueError(f'Original page layout missing: {config["id"]}')
        layout = read_json(layout_path)
        if layout.get('schemaVersion') != 'raz-page-layout/1.0' or layout.get('status') != 'verified-mapping':
            raise ValueError(f'Original page mappings still need review: {config["id"]}')
        book = next(item for item in payload['books'] if item['id'] == config['id'])
        book_page_ids = set(book['pageIds'])
        if set(layout['pages']) != book_page_ids:
            raise ValueError(f'Original page layout must contain every lesson page: {config["id"]}')
        cover = layout['cover']
        cover_path = (content_root / cover['image']).resolve()
        if not cover_path.is_relative_to(book_root) or '_source' in cover_path.parts or not cover_path.is_file():
            raise ValueError(f'Book cover must be a reader asset inside its book: {config["id"]}')
        if hashlib.sha256(cover_path.read_bytes()).hexdigest() != cover['imageSha256']:
            raise ValueError(f'Book cover hash mismatch: {config["id"]}')
        with Image.open(cover_path) as image:
            if image.size != (cover['width'], cover['height']):
                raise ValueError(f'Book cover dimensions mismatch: {config["id"]}')
        book['cover'] = content_url(cover['image'])

        for page_id in book['pageIds']:
            page, paper = payload['pages'][page_id], layout['pages'][page_id]
            if paper['sourceSha256'] != page['sha256']:
                raise ValueError(f'Stale original page mapping: {page_id}. Run npm run pages:build.')
            image_path = (content_root / paper['image']).resolve()
            if not image_path.is_relative_to(book_root) or '_source' in image_path.parts or not image_path.is_file():
                raise ValueError(f'Original page image must be inside its book: {page_id}')
            if hashlib.sha256(image_path.read_bytes()).hexdigest() != paper['imageSha256']:
                raise ValueError(f'Original page image hash mismatch: {page_id}')
            with Image.open(image_path) as image:
                if image.size != (paper['width'], paper['height']):
                    raise ValueError(f'Original page image dimensions mismatch: {page_id}')
            explained = {ref for block in page['blocks'] for ref in block['sourceRefs']}
            expected = {unit['id'] for unit in page['sourceUnits'] if unit['id'] in explained}
            mapped_units = [hotspot['unitId'] for hotspot in paper['hotspots']]
            if len(mapped_units) != len(set(mapped_units)) or set(mapped_units) != expected:
                raise ValueError(f'Incomplete or duplicated sentence hotspots: {page_id}')
            for hotspot in paper['hotspots']:
                if not hotspot['rects']:
                    raise ValueError(f'Empty sentence hotspot: {hotspot["unitId"]}')
                for rect in hotspot['rects']:
                    if set(rect) != {'x', 'y', 'width', 'height'} or not all(
                            type(value) in (int, float) and math.isfinite(value) for value in rect.values()):
                        raise ValueError(f'Invalid sentence rectangle: {hotspot["unitId"]}')
                    if not (0 <= rect['x'] < rect['x'] + rect['width'] <= paper['width'] and
                            0 <= rect['y'] < rect['y'] + rect['height'] <= paper['height']):
                        raise ValueError(f'Sentence rectangle outside page: {hotspot["unitId"]}')
            page['paper'] = {
                'image': content_url(paper['image']), 'width': paper['width'], 'height': paper['height'],
                'hotspots': [{'unitId': spot['unitId'], 'rects': spot['rects']} for spot in paper['hotspots']],
            }


def attach_page_audio(payload: dict, book_sources: list[dict], content_root: Path) -> None:
    """Attach verified read-aloud tracks; books and pages may intentionally be silent."""
    for page in payload['pages'].values():
        page['audio'] = []
    content_root = content_root.resolve()
    for source in book_sources:
        audio_path = source['source'] / 'audio-layout.json'
        if not audio_path.is_file():
            continue
        layout = read_json(audio_path)
        if layout.get('schemaVersion') != 'raz-page-audio/1.0' or layout.get('status') != 'verified-mapping':
            raise ValueError(f'Page audio mappings still need review: {source["config"]["id"]}')
        book_root = source['root'].resolve()
        for page_id, entry in layout['pages'].items():
            page = payload['pages'].get(page_id)
            if not page or page['bookId'] != source['config']['id']:
                raise ValueError(f'Page audio names an unknown page: {page_id}')
            if entry['sourceSha256'] != page['sha256']:
                raise ValueError(f'Stale page audio mapping: {page_id}. Run npm run audio:build.')
            units = {unit['id'] for unit in page['sourceUnits']}
            spoken = set()
            for track in entry['tracks']:
                audio_file = (content_root / track['audio']).resolve()
                if not audio_file.is_relative_to(book_root) or '_source' in audio_file.parts or not audio_file.is_file():
                    raise ValueError(f'Page audio must be a reader asset inside its book: {page_id}')
                if hashlib.sha256(audio_file.read_bytes()).hexdigest() != track['audioSha256']:
                    raise ValueError(f'Page audio hash mismatch: {page_id}')
                duration = track['durationMs']
                if type(duration) is not int or duration <= 0:
                    raise ValueError(f'Page audio needs a positive duration: {page_id}')
                for clip in track['clips']:
                    if clip['unitId'] not in units or clip['unitId'] in spoken:
                        raise ValueError(f'Unknown or repeated audio sentence: {clip["unitId"]}')
                    if not (type(clip['startMs']) is int and type(clip['endMs']) is int and
                            0 <= clip['startMs'] < clip['endMs'] <= duration):
                        raise ValueError(f'Audio range outside the recording: {clip["unitId"]}')
                    spoken.add(clip['unitId'])
                page['audio'].append({
                    'src': content_url(track['audio']), 'durationMs': duration,
                    'clips': [{'unitId': clip['unitId'], 'startMs': clip['startMs'], 'endMs': clip['endMs']}
                              for clip in sorted(track['clips'], key=lambda item: item['startMs'])],
                })


def check_pdf_sources(book_sources: list[dict]) -> None:
    for source in book_sources:
        pdf = source['source'] / 'original.pdf'
        layout = read_json(source['source'] / 'page-layout.json')
        if not pdf.is_file():
            raise ValueError(f'PDF source missing inside book: {source["config"]["id"]}')
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != layout.get('pdfSha256'):
            raise ValueError(f'PDF changed; rebuild original pages: {source["config"]["id"]}')


def build_lessons(content_root: Path = CONTENT_ROOT) -> tuple[dict, list[dict]]:
    all_sources = discover_books(content_root)
    ready = [item for item in all_sources if item['config']['status'] in {'draft', 'ready'}]
    checks = check_paths([item['source'] / 'lessons' for item in ready])
    errors = [f'{item.path}: {message}' for item in checks for message in item.errors]
    if errors:
        raise ValueError('\n'.join(errors))
    pages = [parse_page(item.path) for item in checks]
    by_book: dict[str, list] = {}
    for page in pages:
        by_book.setdefault(page['bookId'], []).append(page)
    if set(by_book) != {item['config']['id'] for item in ready}:
        raise ValueError('Ready book configs must name each lesson book exactly once')

    books = []
    for source in ready:
        config = source['config']
        book_pages = sorted(by_book[config['id']], key=lambda page: page['pdfPage'])
        if len({(page['bookTitle'], page['level']) for page in book_pages}) != 1:
            raise ValueError(f'Conflicting book metadata: {config["id"]}')
        if book_pages[0]['bookTitle'] != config['title'] or book_pages[0]['level'] != config['level']:
            raise ValueError(f'Lesson metadata does not match book config: {config["id"]}')
        expected = config.get('expectedPdfPages')
        numbers = [page['pdfPage'] for page in book_pages]
        if type(expected) is not int or expected < 1 or numbers != list(range(1, expected + 1)):
            raise ValueError(f'Missing or duplicated PDF pages: {config["id"]}: {numbers}')
        books.append({
            'id': config['id'], 'title': config['title'], 'level': config['level'],
            'subtitle': config.get('subtitle', ''), 'category': config.get('category', '逐页精读'),
            'cover': '', 'pageIds': [page['id'] for page in book_pages],
        })

    payload = {
        'schemaVersion': 'raz-reader-content/1.0', 'bundleId': 'raz-local-preview',
        'contentVersion': '', 'contentStatus': 'draft-preview',
        'levels': [], 'books': books, 'pages': {page['id']: page for page in pages},
    }
    level_names = list(dict.fromkeys(book['level'] for book in books))
    payload['levels'] = [
        {'id': level, 'label': f'{level} 级', 'bookIds': [book['id'] for book in books if book['level'] == level]}
        for level in level_names
    ]
    return payload, ready


def build(content_root: Path = CONTENT_ROOT) -> dict:
    payload, ready = build_lessons(content_root)
    attach_original_pages(payload, ready, content_root)
    attach_page_audio(payload, ready, content_root)
    check_pdf_sources(ready)
    from practice_content import compile_practice
    by_id = {source['config']['id']: source for source in ready}
    for book in payload['books']:
        practice = compile_practice(book['id'], by_id[book['id']]['source'] / 'practice', payload['pages'])
        if practice:
            book['practice'] = practice
    identity = json.dumps({key: value for key, value in payload.items() if key != 'contentVersion'},
                          sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    payload['contentVersion'] = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return payload


def write_runtime_indexes(payload: dict, content_root: Path, output: Path) -> None:
    sources = {item['config']['id']: item for item in discover_books(content_root)}
    books = {}
    for book in payload['books']:
        root = sources[book['id']]['root']
        books[book['id']] = {**book, 'contentPath': content_url(
            (root / 'book.json').relative_to(content_root).as_posix())}
        write_json(root / 'book.json', {
            'schemaVersion': 'raz-reader-book/1.0', 'contentVersion': payload['contentVersion'],
            **books[book['id']], 'pages': {page_id: payload['pages'][page_id] for page_id in book['pageIds']},
        })
    # The shelf needs metadata and practice packs, but no lesson text or hotspots.
    write_json(output, {
        **{key: value for key, value in payload.items() if key not in {'books', 'pages'}},
        'schemaVersion': 'raz-reader-content/2.0', 'books': list(books.values()),
        'pageCount': len(payload['pages']),
    })
    for level in payload['levels']:
        write_json(content_root / 'levels' / level['id'] / 'level.json', {
            'schemaVersion': 'raz-reader-level/1.0', 'id': level['id'], 'label': level['label'],
            'books': [books[book_id] for book_id in level['bookIds']],
        })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--content-root', type=Path, default=CONTENT_ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    content_root = args.content_root.resolve()
    output = args.output.resolve() if args.output else content_root / 'manifest.json'
    payload = build(content_root)
    write_runtime_indexes(payload, content_root, output)
    spoken = sum(len(track['clips']) for page in payload['pages'].values() for track in page['audio'])
    print(f'Built {len(payload["levels"])} level / {len(payload["books"])} ready books / '
          f'{len(payload["pages"])} pages / {spoken} read-aloud sentences from '
          f'{len(discover_books(content_root))} source books')


if __name__ == '__main__':
    main()
