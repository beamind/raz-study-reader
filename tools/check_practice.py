"""Check same-book restoration sources, blanks, feedback and shared word banks."""
import argparse
from pathlib import Path

from build_reader_content import build_lessons
from practice_content import compile_practice

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book', help='Stable book ID, e.g. raz-p-001')
    parser.add_argument('--level', help='Check a whole level, e.g. P')
    parser.add_argument('--require-all', action='store_true', help='Fail if any selected book has no ready practice')
    args = parser.parse_args()
    payload, books = build_lessons(ROOT / 'content')
    if args.level:
        books = [b for b in books if b['config']['level'] == args.level]
        if not books:
            parser.error(f'Unknown enabled level: {args.level}')
    if args.book:
        books = [b for b in books if b['config']['id'] == args.book]
        if not books:
            parser.error(f'Unknown enabled book: {args.book}')
    total, ready, missing = 0, 0, []
    for book in books:
        pack = compile_practice(book['config']['id'], book['source'] / 'practice', payload['pages'])
        if pack:
            ready += 1
            total += len(pack['questions'])
            print(f'{pack["bookId"]}: {len(pack["questions"])} sentences, {len(pack["options"])} shared options')
        else:
            missing.append(book['config']['id'])
    if args.require_all and missing:
        parser.error('Missing ready practice: ' + ', '.join(missing))
    print(f'{ready}/{len(books)} books, {total} questions ready. Meanings and feedback require content review before publication.')


if __name__ == '__main__':
    main()
