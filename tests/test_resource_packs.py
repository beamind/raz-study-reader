from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_resource_packs import build_pack


class ResourcePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        self.book_root = self.content / 'levels/Q/books/001-fixture'
        self.book_root.mkdir(parents=True)
        self.base = 'content/levels/Q/books/001-fixture/'
        self.book = {'id': 'raz-q-001', 'title': 'Fixture', 'subtitle': '', 'category': '故事', 'level': 'Q',
                     'cover': self.base + 'cover.webp', 'contentPath': self.base + 'book.json', 'pageIds': ['raz-q-001-p001']}
        self.page = {'id': 'raz-q-001-p001', 'bookId': self.book['id'],
                     'paper': {'image': self.base + 'pages/p001/page.webp'},
                     'audio': [{'src': self.base + 'pages/p001/audio/book.mp3'}, {'src': self.base + 'pages/p001/audio/book.mp3'}]}
        for relative, data in [('cover.webp', b'webp'), ('pages/p001/page.webp', b'page'), ('pages/p001/audio/book.mp3', b'audio'),
                               ('pages/p001/audio/unused.mp3', b'unused'), ('_source/original.pdf', b'private')]:
            path = self.book_root / relative
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        self.write('global-a')

    def write(self, version):
        (self.content / 'manifest.json').write_text(json.dumps({'bundleId': 'raz-local-preview', 'contentStatus': 'draft-preview',
            'contentVersion': version, 'levels': [{'id': 'Q', 'label': 'Q 级', 'bookIds': [self.book['id']]}], 'books': [self.book]}), encoding='utf-8')
        (self.book_root / 'book.json').write_text(json.dumps({'schemaVersion': 'raz-reader-book/1.0', 'contentVersion': version,
            **self.book, 'pages': {self.page['id']: self.page}}), encoding='utf-8')

    def test_referenced_assets_only_integrity_and_stored_entries(self):
        result = build_pack(self.content, self.root / 'packs', 'Q')
        with zipfile.ZipFile(result['path']) as archive:
            manifest = json.loads(archive.read('pack.json'))
            self.assertEqual(len(archive.namelist()), 5)
            self.assertFalse(any('_source' in name or 'unused' in name for name in archive.namelist()))
            self.assertEqual(manifest['version'], manifest['library']['contentVersion'])
            self.assertEqual(result['version'], json.loads(archive.read(self.book['contentPath']))['contentVersion'])
            for file in manifest['files']:
                data = archive.read(file['path'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), file['sha256'])
                self.assertEqual(len(data), file['size'])
                self.assertEqual(archive.getinfo(file['path']).compress_type, zipfile.ZIP_STORED)

    def test_unrelated_global_version_does_not_change_pack_or_bytes(self):
        first = build_pack(self.content, self.root / 'one', 'Q')
        self.write('global-b')
        second = build_pack(self.content, self.root / 'two', 'Q')
        self.assertEqual(first['version'], second['version'])
        self.assertEqual(Path(first['path']).read_bytes(), Path(second['path']).read_bytes())

    def test_media_changes_change_package_version(self):
        first = build_pack(self.content, self.root / 'one', 'Q')
        (self.book_root / 'cover.webp').write_bytes(b'new-cover')
        second = build_pack(self.content, self.root / 'two', 'Q')
        self.assertNotEqual(first['version'], second['version'])

    def test_missing_media_and_traversal_rejected(self):
        (self.book_root / 'cover.webp').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing resource'):
            build_pack(self.content, self.root / 'one', 'Q')
        self.book['cover'] = self.base + '../private.webp'
        self.write('global-a')
        with self.assertRaisesRegex(ValueError, 'outside level'):
            build_pack(self.content, self.root / 'two', 'Q')

    def test_stale_book_and_unknown_or_empty_level_rejected(self):
        runtime = json.loads((self.book_root / 'book.json').read_text(encoding='utf-8'))
        runtime['contentVersion'] = 'old'
        (self.book_root / 'book.json').write_text(json.dumps(runtime), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Stale runtime'):
            build_pack(self.content, self.root / 'one', 'Q')
        with self.assertRaisesRegex(ValueError, 'Unknown level'):
            build_pack(self.content, self.root / 'one', 'Z')
        with self.assertRaisesRegex(ValueError, 'Empty resource'):
            build_pack(self.content, self.root / 'one', 'Q', {'missing'})
