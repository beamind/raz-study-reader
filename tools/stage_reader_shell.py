"""Stage a content-free reader shell and generate its offline worker."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def stage_shell(destination: Path) -> None:
    content = destination / 'content'
    if content.exists():
        if content.resolve() != (ROOT / 'dist' / 'content').resolve():
            raise ValueError('Refusing to replace unexpected content directory')
        shutil.rmtree(content)
    content.mkdir(parents=True)
    (content / 'manifest.json').write_text(json.dumps({
        'schemaVersion': 'raz-reader-content/2.0', 'bundleId': 'raz-local-preview',
        'contentVersion': 'shell-v1', 'contentStatus': 'draft-preview',
        'levels': [], 'books': [], 'pageCount': 0,
    }), encoding='utf-8')
    assets = ['./', './index.html', './icon.svg', './content/manifest.json'] + [
        './' + path.relative_to(destination).as_posix()
        for directory in ('assets', 'legal')
        for path in sorted((destination / directory).rglob('*')) if path.is_file()]
    worker = (ROOT / 'tools' / 'reader-sw.template.js').read_text(encoding='utf-8')
    version = hashlib.sha256(worker.encode() + b''.join((destination / path[2:]).read_bytes() for path in assets if path != './')).hexdigest()[:16]
    (destination / 'reader-sw.js').write_text(worker.replace('__CACHE__', f'raz-reader-shell-{version}').replace('__ASSETS__', json.dumps(assets)), encoding='utf-8')
    print(f'Staged content-free reader shell: {sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())} bytes')


if __name__ == '__main__':
    stage_shell(ROOT / 'dist')
