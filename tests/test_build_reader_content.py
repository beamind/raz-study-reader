"""Bundling regressions for the self-contained content tree."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
SPEC = importlib.util.spec_from_file_location('reader_builder', ROOT / 'tools/build_reader_content.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ReaderBundleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.content = Path(directory.name) / 'content'
        self.book = self.content / 'levels/P/books/001-demo-red-bird'
        self.source = self.book / '_source'
        self.lessons = self.source / 'lessons'
        self.lessons.mkdir(parents=True)
        self.config = {
            'schemaVersion': 'raz-book-source/1.0', 'id': 'demo-red-bird', 'level': 'P',
            'sourceNumber': 1, 'title': 'A Bird in the Garden', 'status': 'draft',
            'expectedPdfPages': 1, 'pdfLayout': 'single-pages',
        }
        builder.write_json(self.source / 'book.json', self.config)
        self.demo = (ROOT / 'examples/demo-red-bird/p003.md').read_text(encoding='utf-8')

    def page(self, number=1, change=None):
        text = self.demo.replace('p003', f'p{number:03}')
        match = builder.HEADER.match(text)
        meta = json.loads(match[1])
        meta.update(pdf_page_number=number, raz_level='P')
        if change:
            change(meta)
        path = self.lessons / f'p{number:03}.md'
        path.write_text('<!-- raz-lesson\n' + json.dumps(meta, ensure_ascii=False) +
                        '\n-->\n' + text[match.end():], encoding='utf-8')
        return path, meta

    def lessons_only(self):
        return builder.build_lessons(self.content)[0]

    def add_runtime_assets(self):
        from PIL import Image
        pages = [builder.parse_page(path) for path in sorted(self.lessons.glob('*.md'))]
        cover = self.book / 'cover.webp'
        cover.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (80, 120), 'green').save(cover)
        layout_pages = {}
        for page in pages:
            name = page['id'].rsplit('-', 1)[-1]
            image = self.book / 'pages' / name / 'page.webp'
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (100, 200), 'white').save(image)
            explained = {ref for block in page['blocks'] for ref in block['sourceRefs']}
            layout_pages[page['id']] = {
                'image': image.relative_to(self.content).as_posix(), 'width': 100, 'height': 200,
                'sourceSha256': page['sha256'],
                'imageSha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                'hotspots': [{'unitId': unit['id'],
                              'rects': [{'x': 5, 'y': 5, 'width': 20, 'height': 10}]}
                             for unit in page['sourceUnits'] if unit['id'] in explained],
            }
        pdf = self.source / 'original.pdf'
        pdf.write_bytes(b'fake pdf')
        builder.write_json(self.source / 'page-layout.json', {
            'schemaVersion': 'raz-page-layout/1.0', 'status': 'verified-mapping',
            'pdfSha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
            'cover': {'image': cover.relative_to(self.content).as_posix(), 'width': 80, 'height': 120,
                      'imageSha256': hashlib.sha256(cover.read_bytes()).hexdigest()},
            'pages': layout_pages,
        })

    def build(self):
        return builder.build(self.content)

    def test_preview_keeps_draft_status_refs_original_and_source_unchanged(self):
        path, meta = self.page()
        before = path.read_bytes(), path.stat().st_mtime_ns
        bundle = self.lessons_only()
        page = bundle['pages']['demo-red-bird-p001']
        self.assertEqual(page['sourceUnits'], meta['source_units'])
        self.assertEqual(page['status'], 'draft')
        original = next(block for block in page['blocks'] if block['kind'] == 'sentence')
        source = next(unit for unit in meta['source_units'] if unit['id'] == original['sourceRefs'][0])
        self.assertEqual(original['original'], source['text'])
        self.assertNotIn('**原文：**', original['markdown'])
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_missing_middle_page_is_rejected(self):
        self.config['expectedPdfPages'] = 3
        builder.write_json(self.source / 'book.json', self.config)
        self.page(1)
        self.page(3)
        with self.assertRaisesRegex(ValueError, 'Missing or duplicated PDF pages'):
            self.lessons_only()

    def test_missing_last_page_is_rejected(self):
        self.config['expectedPdfPages'] = 2
        builder.write_json(self.source / 'book.json', self.config)
        self.page(1)
        with self.assertRaisesRegex(ValueError, 'Missing or duplicated PDF pages'):
            self.lessons_only()

    def test_conflicting_title_is_rejected(self):
        self.config['expectedPdfPages'] = 2
        builder.write_json(self.source / 'book.json', self.config)
        self.page(1)
        self.page(2, lambda meta: meta.update(book_title='Another title'))
        with self.assertRaisesRegex(ValueError, 'Conflicting book metadata'):
            self.lessons_only()

    def test_blocked_lesson_cannot_be_bundled(self):
        self.page(change=lambda meta: meta.update(generation_status='blocked', issues=['Review required']))
        with self.assertRaisesRegex(ValueError, 'blocked draft cannot be enabled'):
            self.lessons_only()

    def test_invalid_reference_cannot_be_bundled(self):
        path, _ = self.page()
        path.write_text(path.read_text(encoding='utf-8').replace(
            'source-refs: demo-red-bird-p001-u01', 'source-refs: missing-u01', 1), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unknown source reference'):
            self.lessons_only()

    def test_ready_book_must_have_lessons(self):
        self.page()
        extra = self.content / 'levels/P/books/002-empty/_source'
        (extra / 'lessons').mkdir(parents=True)
        builder.write_json(extra / 'book.json', {**self.config, 'id': 'empty', 'sourceNumber': 2})
        with self.assertRaisesRegex(ValueError, 'directory has no Markdown files'):
            self.lessons_only()

    def test_content_version_is_deterministic_and_tracks_content_change(self):
        path, _ = self.page()
        self.add_runtime_assets()
        before = self.build()['contentVersion']
        self.assertEqual(before, self.build()['contentVersion'])
        path.write_text(path.read_text(encoding='utf-8') + '\n补充观察。\n', encoding='utf-8')
        self.add_runtime_assets()
        self.assertNotEqual(before, self.build()['contentVersion'])

    def test_runtime_manifest_is_small_and_each_book_is_self_contained(self):
        self.page()
        self.add_runtime_assets()
        bundle = self.build()
        output = self.content / 'manifest.json'
        builder.write_runtime_indexes(bundle, self.content, output)
        manifest = builder.read_json(output)
        self.assertEqual(manifest['schemaVersion'], 'raz-reader-content/2.0')
        self.assertNotIn('pages', manifest)
        self.assertEqual(manifest['pageCount'], len(bundle['pages']))
        for book in manifest['books']:
            runtime = builder.read_json(self.content / book['contentPath'].removeprefix('content/'))
            self.assertEqual(runtime['contentVersion'], manifest['contentVersion'])
            self.assertEqual(runtime['pages'], {key: bundle['pages'][key] for key in book['pageIds']})
            self.assertEqual(runtime['id'], book['id'])



if __name__ == '__main__':
    unittest.main()
