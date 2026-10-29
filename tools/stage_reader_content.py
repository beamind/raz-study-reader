"""Copy the validated reader portion of content/ into dist/content unchanged."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--content-root', type=Path, default=ROOT / 'content')
    parser.add_argument('--destination', type=Path, default=ROOT / 'dist' / 'content')
    args = parser.parse_args()
    content_root = args.content_root.resolve()
    destination = args.destination.resolve()
    expected = (ROOT / 'dist' / 'content').resolve()
    if destination.exists():
        if destination != expected:
            raise ValueError(f'Refusing to replace unexpected destination: {destination}')
        shutil.rmtree(destination)
    manifest_path = content_root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    copy_file(manifest_path, destination / 'manifest.json')
    copied = 1
    for level in manifest['levels']:
        level_path = content_root / 'levels' / level['id'] / 'level.json'
        copy_file(level_path, destination / level_path.relative_to(content_root))
        copied += 1
    runtime_books = {}
    for path in content_root.glob('levels/*/books/*/book.json'):
        book = json.loads(path.read_text(encoding='utf-8'))
        runtime_books[book['id']] = path.parent
    for book in manifest['books']:
        book_root = runtime_books.get(book['id'])
        if not book_root:
            raise ValueError(f'Missing runtime book index: {book["id"]}')
        for path in book_root.rglob('*'):
            if path.is_file() and '_source' not in path.parts:
                copy_file(path, destination / path.relative_to(content_root))
                copied += 1
    print(f'Staged {copied} reader files at {destination} with unchanged content-relative paths')


if __name__ == '__main__':
    main()
