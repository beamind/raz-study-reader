"""Original synthetic packages and damaged/update variants for integration checks."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import zipfile
from build_resource_packs import build_pack, encoded, ROOT
from synthetic_content import write_fixture_content


def main() -> None:
    destination = ROOT / 'artifacts' / 'pack-fixtures'
    content = ROOT / 'artifacts' / 'fixture-content' / 'content'
    write_fixture_content(content)
    paths = {}
    for level, book in [('Q', 'raz-q-003'), ('P', 'raz-p-001')]:
        result = build_pack(content, destination, level, {book})
        paths[level] = result['path']
    with zipfile.ZipFile(paths['Q']) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    for kind in ['update', 'damaged', 'incompatible']:
        files = dict(original)
        manifest = json.loads(files['pack.json'])
        if kind == 'update':
            manifest['version'] = '0123456789abcdef'
            manifest['library']['contentVersion'] = manifest['version']
            for book in manifest['library']['books']:
                book['contentVersion'] = manifest['version']
                book['subtitle'] += '（更新测试）'
                content = json.loads(files[book['contentPath']])
                content.update(book)
                files[book['contentPath']] = encoded(content)
            for file in manifest['files']:
                file['size'] = len(files[file['path']])
                file['sha256'] = hashlib.sha256(files[file['path']]).hexdigest()
            manifest['totalBytes'] = sum(file['size'] for file in manifest['files'])
        elif kind == 'incompatible':
            manifest['readerVersion'] = 999
        else:
            target = manifest['files'][-1]['path']
            value = bytearray(files[target]); value[0] ^= 1
            files[target] = bytes(value)
        files['pack.json'] = encoded(manifest)
        path = destination / f'Q-{kind}.razpack'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, data in files.items(): archive.writestr(name, data)
        paths[kind] = str(path)
    (destination / 'fixtures.json').write_text(json.dumps(paths), encoding='utf-8')
    print(json.dumps(paths))


if __name__ == '__main__': main()
