"""Build independent, deterministic per-level ZIP_STORED .razpack files.

Only assets actually referenced by validated runtime books are distributed.
The format deliberately excludes ZIP64 and compression: WebP/MP3 are already
compressed, and readers can copy bounded file slices without loading a ZIP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE = 64 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
MAX_TOTAL = 3 * 1024 * 1024 * 1024


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')


def asset_path(value: str, level: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_./-]+', value):
        raise ValueError(f'Invalid asset path: {value}')
    path = PurePosixPath(value)
    if '..' in path.parts or '.' in path.parts or not value.startswith(f'content/levels/{level}/books/') or '_source' in path.parts:
        raise ValueError(f'Asset outside level {level}: {value}')
    if path.suffix not in {'.json', '.webp', '.mp3'}:
        raise ValueError(f'Unsupported asset: {value}')
    return value


def build_pack(content: Path, destination: Path, level_id: str, book_ids: set[str] | None = None) -> dict:
    manifest = json.loads((content / 'manifest.json').read_text(encoding='utf-8'))
    level = next((item for item in manifest['levels'] if item['id'] == level_id), None)
    if not level:
        raise ValueError(f'Unknown level: {level_id}')
    books = [dict(book) for book in manifest['books'] if book['id'] in level['bookIds'] and
             (book_ids is None or book['id'] in book_ids)]
    if not books:
        raise ValueError('Empty resource pack')
    files: dict[str, Path | bytes] = {}
    for book in books:
        book_path = asset_path(book['contentPath'], level_id)
        source = content.parent / book_path
        data = json.loads(source.read_text(encoding='utf-8'))
        if data['id'] != book['id'] or data['contentVersion'] != manifest['contentVersion']:
            raise ValueError(f'Stale runtime book: {book["id"]}')
        data['contentVersion'] = ''
        book.pop('contentVersion', None)
        files[book_path] = encoded(data)
        paths = {book['cover']}
        for page in data['pages'].values():
            paths.add(page['paper']['image'])
            paths.update(track['src'] for track in page['audio'])
        for value in paths:
            path = asset_path(value, level_id)
            source = content.parent / path
            if not source.is_file():
                raise ValueError(f'Missing resource: {path}')
            files[path] = source

    def describe(path: str, source: Path | bytes) -> dict:
        if isinstance(source, bytes):
            size, digest = len(source), hashlib.sha256(source).hexdigest()
        else:
            size = source.stat().st_size
            with source.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if size <= 0 or size > (MAX_JSON if path.endswith('.json') else MAX_FILE):
            raise ValueError(f'Resource exceeds size limit: {path}')
        return {'path': path, 'size': size, 'sha256': digest}

    inventory = [describe(path, source) for path, source in sorted(files.items())]
    # Ignore the full-library version so a Q edit cannot change the P package.
    version = hashlib.sha256(encoded({'books': books, 'files': inventory})).hexdigest()[:16]
    for book in books:
        book['contentVersion'] = version
        path = book['contentPath']
        data = json.loads(files[path])
        data['contentVersion'] = version
        files[path] = encoded(data)
    inventory = [describe(path, source) for path, source in sorted(files.items())]
    level = {**level, 'bookIds': [book['id'] for book in books]}
    descriptor = {
        'schemaVersion': 'raz-reader-pack/1.0', 'readerVersion': 1,
        'level': level, 'version': version, 'files': inventory,
        'totalBytes': sum(item['size'] for item in inventory),
        'library': {'schemaVersion': 'raz-reader-content/2.0', 'bundleId': manifest['bundleId'],
                    'contentVersion': version, 'contentStatus': manifest['contentStatus'],
                    'levels': [level], 'books': books,
                    'pageCount': sum(len(book['pageIds']) for book in books)},
    }
    if descriptor['totalBytes'] > MAX_TOTAL or len(inventory) > 60000 or len(encoded(descriptor)) > MAX_JSON:
        raise ValueError('Resource pack exceeds format limits')
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f'RAZ-{level_id}-{version}.razpack'
    temporary = output.with_suffix('.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            for path, source in [('pack.json', encoded(descriptor)), *sorted(files.items())]:
                info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o100644 << 16
                with archive.open(info, 'w') as target:
                    if isinstance(source, bytes):
                        target.write(source)
                    else:
                        with source.open('rb') as stream:
                            while chunk := stream.read(1024 * 1024):
                                target.write(chunk)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {'level': level_id, 'version': version, 'books': len(books),
            'files': len(inventory), 'bytes': output.stat().st_size, 'path': str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--content-root', type=Path, default=ROOT / 'content')
    parser.add_argument('--destination', type=Path, default=ROOT / 'artifacts' / 'resource-packs')
    parser.add_argument('--level', action='append')
    parser.add_argument('--book', action='append', help='Subset for development/test fixtures')
    args = parser.parse_args()
    content = args.content_root.resolve()
    levels = args.level or [level['id'] for level in json.loads((content / 'manifest.json').read_text(encoding='utf-8'))['levels']]
    for level in levels:
        print(json.dumps(build_pack(content, args.destination, level, set(args.book) if args.book else None), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
