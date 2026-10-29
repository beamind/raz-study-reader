"""Prepare an empty development catalogue without importing any book materials."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    target = ROOT / 'content' / 'manifest.json'
    if target.exists():
        print('Keeping the existing local development catalogue.')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        'schemaVersion': 'raz-reader-content/2.0', 'bundleId': 'raz-local-preview',
        'contentVersion': 'shell-v1', 'contentStatus': 'draft-preview',
        'levels': [], 'books': [], 'pageCount': 0,
    }), encoding='utf-8')
    print('Prepared an empty development catalogue; import your own authorized resource packs.')


if __name__ == '__main__':
    main()
