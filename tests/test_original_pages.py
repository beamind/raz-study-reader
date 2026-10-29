"""Original-page alignment and shipping gates, independent of the OCR runtime."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_original_pages import (align_page, column_locator, cover_crop_box,
                                  find_binding_guide, find_candidates, rectangles,
                                  hotspot_conflicts, locate_page, resolved_cover_crop)
from build_reader_content import attach_original_pages


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.words = [dict(text=text, line=i // 3, x=(i % 3) * 80 + 10, y=(i // 3) * 40 + 10, width=60, height=20)
                      for i, text in enumerate(['We', 'can', 'move', 'nine', 'boxes', 'before', 'lunch!'])]

    def test_sentence_crossing_lines_keeps_separate_click_regions(self):
        page = {'id': 'page', 'sourceUnits': [{'id': 'u1', 'text': 'We can move nine boxes before lunch!'}],
                'blocks': [{'sourceRefs': ['u1']}]}
        hotspots, review = align_page(page, {'words': self.words, 'width': 300, 'height': 160}, {})
        self.assertEqual(review, [])
        self.assertEqual(len(hotspots[0]['rects']), 3)
        self.assertTrue(all(rect['height'] == 26 for rect in hotspots[0]['rects']))
        self.assertEqual(hotspots[0]['rects'][-1]['width'], 64)

    def test_duplicate_occurrences_require_review_instead_of_first_match(self):
        page = {'id': 'page', 'sourceUnits': [{'id': 'u1', 'text': 'We can move'}], 'blocks': [{'sourceRefs': ['u1']}]}
        hotspots, review = align_page(page, {'words': self.words + self.words, 'width': 300, 'height': 160}, {})
        self.assertEqual(hotspots, [])
        self.assertEqual(len(review[0]['candidates']), 2)

    def test_short_wrong_label_is_not_guessed_and_credits_have_no_dead_button(self):
        page = {'id': 'page', 'sourceUnits': [{'id': 'u1', 'text': 'Kia'}, {'id': 'credit', 'text': 'Written by someone'}],
                'blocks': [{'sourceRefs': ['u1']}]}
        hotspots, review = align_page(page, {'words': self.words, 'width': 300, 'height': 160}, {})
        self.assertEqual(hotspots, [])
        self.assertEqual([r['unitId'] for r in review], ['u1'])

    def test_reviewed_override_requires_a_reason_and_valid_word_range(self):
        page = {'id': 'page', 'sourceUnits': [{'id': 'u1', 'text': 'Misread title'}], 'blocks': [{'sourceRefs': ['u1']}]}
        ocr = {'words': self.words, 'width': 300, 'height': 160}
        with self.assertRaisesRegex(ValueError, 'reason'):
            align_page(page, ocr, {'u1': {'word_start': 0, 'word_end': 2}})
        with self.assertRaisesRegex(ValueError, 'range'):
            align_page(page, ocr, {'u1': {'word_start': 0, 'word_end': 100, 'reason': 'Checked source'}})

    def test_punctuation_and_typographic_quotes_do_not_break_matching(self):
        self.assertEqual(find_candidates('lunch.', self.words)[0]['start'], 6)
        self.assertEqual(find_candidates('Mom’s', [{'text': "Mom's"}])[0]['score'], 1)

    def test_malformed_cross_line_pdf_word_does_not_swallow_adjacent_text(self):
        words = [
            {'text': 'She', 'line': 1, 'x': 300, 'y': 10, 'width': 40, 'height': 20},
            {'text': 'started', 'line': 1, 'x': 350, 'y': 10, 'width': 80, 'height': 20},
            {'text': 'baby-size', 'line': 1, 'x': 10, 'y': 10, 'width': 480, 'height': 80},
            {'text': 'wooden', 'line': 1, 'x': 10, 'y': 60, 'width': 70, 'height': 20},
            {'text': 'ones', 'line': 1, 'x': 90, 'y': 60, 'width': 50, 'height': 20},
        ]
        rects = rectangles(words, 500, 120)
        self.assertEqual(len(rects), 2)
        self.assertGreater(rects[0]['x'], 250)
        self.assertGreater(rects[1]['y'], rects[0]['y'] + rects[0]['height'])

    def test_column_sort_does_not_compare_rows_when_coordinates_tie(self):
        locator = {
            'engine': 'pdf-text', 'width': 200, 'height': 100,
            'words': [
                {'text': 'first', 'line': 1, 'x': 10, 'y': 20, 'width': 30, 'height': 10},
                {'text': 'second', 'line': 2, 'x': 10, 'y': 20, 'width': 35, 'height': 10},
            ],
        }
        ordered = column_locator(locator, 2)
        self.assertEqual([word['text'] for word in ordered['words']], ['first', 'second'])

    def test_page_merge_keeps_repeated_label_away_from_long_sentence(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'page.png'
            Image.new('RGB', (200, 120), 'white').save(image)
            body = [
                {'text': 'Alpha', 'line': 0, 'x': 10, 'y': 10, 'width': 35, 'height': 12},
                {'text': 'trunk', 'line': 0, 'x': 50, 'y': 10, 'width': 35, 'height': 12},
                {'text': 'omega.', 'line': 0, 'x': 90, 'y': 10, 'width': 45, 'height': 12},
            ]
            label = {'text': 'trunk', 'line': 1, 'x': 20, 'y': 80, 'width': 35, 'height': 12}
            paths = []
            for index, words in enumerate((body + [label], [label] + body)):
                path = root / f'locator-{index}.json'
                path.write_text(json.dumps({'engine': f'test-{index}', 'width': 200,
                                            'height': 120, 'words': words}), encoding='utf-8')
                paths.append(path)
            page = {
                'id': 'page',
                'sourceUnits': [
                    {'id': 'label', 'kind': 'label', 'text': 'trunk'},
                    {'id': 'body', 'kind': 'sentence', 'text': 'Alpha trunk omega.'},
                ],
                'blocks': [{'sourceRefs': ['label']}, {'sourceRefs': ['body']}],
            }
            _, hotspots, missing = locate_page(page, image, paths, {})
            self.assertEqual(missing, [])
            self.assertEqual(hotspot_conflicts(hotspots), [])
            selected = {hotspot['unitId']: hotspot for hotspot in hotspots}
            self.assertGreater(selected['label']['rects'][0]['y'], 50)

    def test_printable_binding_guide_crops_only_the_left_edge(self):
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (500, 700), 'white')
        draw = ImageDraw.Draw(image)
        for y in range(0, 700, 8):
            draw.line((45, y, 45, min(y + 4, 699)), fill=(175, 175, 175), width=2)
        draw.rectangle((90, 30, 460, 670), fill=(80, 145, 110))
        self.assertEqual(find_binding_guide(image), (45, 46))
        self.assertEqual(cover_crop_box(image), (47, 0, 500, 700))

    def test_full_bleed_cover_is_not_automatically_cropped(self):
        from PIL import Image
        image = Image.new('RGB', (500, 700), (80, 145, 110))
        self.assertIsNone(find_binding_guide(image))
        self.assertEqual(cover_crop_box(image), (0, 0, 500, 700))

    def test_manual_cover_crop_can_override_or_disable_left_crop(self):
        from PIL import Image
        image = Image.new('RGB', (500, 700), 'white')
        self.assertEqual(cover_crop_box(image, {'left': .1}), (50, 0, 500, 700))
        self.assertEqual(cover_crop_box(image, False), (0, 0, 500, 700))

    def test_cover_crop_default_follows_pdf_layout(self):
        self.assertIsNone(resolved_cover_crop({'pdfLayout': 'print-imposed'}))
        self.assertFalse(resolved_cover_crop({'pdfLayout': 'single-pages'}))
        self.assertEqual(resolved_cover_crop({
            'pdfLayout': 'single-pages', 'coverCrop': {'left': .08},
        }), {'left': .08})


class OriginalPageBundleTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.book = self.root / 'levels/P/books/001-demo'
        self.source = self.book / '_source'
        (self.book / 'pages/p001').mkdir(parents=True)
        self.source.mkdir()
        image = self.book / 'pages/p001/page.webp'
        Image.new('RGB', (100, 200), 'white').save(image)
        cover = self.book / 'cover.webp'
        Image.new('RGB', (80, 120), 'green').save(cover)
        self.payload = {'books': [{'id': 'demo', 'pageIds': ['page']}],
            'pages': {'page': {'bookId': 'demo', 'sha256': 'source-sha',
            'sourceUnits': [{'id': 'u1'}, {'id': 'credit'}], 'blocks': [{'sourceRefs': ['u1']}]}}}
        self.paper = {'image': 'levels/P/books/001-demo/pages/p001/page.webp', 'width': 100, 'height': 200,
            'sourceSha256': 'source-sha', 'imageSha256': hashlib.sha256(image.read_bytes()).hexdigest(),
            'hotspots': [{'unitId': 'u1', 'rects': [{'x': 5, 'y': 10, 'width': 60, 'height': 20}]}]}
        self.layout = {'schemaVersion': 'raz-page-layout/1.0', 'status': 'verified-mapping',
            'cover': {'image': 'levels/P/books/001-demo/cover.webp', 'width': 80, 'height': 120,
                'imageSha256': hashlib.sha256(cover.read_bytes()).hexdigest()},
            'pages': {'page': self.paper}}
        self.path = self.source / 'page-layout.json'
        self.book_source = {'config': {'id': 'demo'}, 'root': self.book, 'source': self.source}

    def attach(self):
        self.path.write_text(json.dumps(self.layout), encoding='utf-8')
        attach_original_pages(self.payload, [self.book_source], self.root)

    def test_verified_complete_mapping_includes_book_local_assets(self):
        self.attach()
        self.assertEqual(self.payload['pages']['page']['paper']['image'],
                         'content/levels/P/books/001-demo/pages/p001/page.webp')
        self.assertEqual(self.payload['books'][0]['cover'],
                         'content/levels/P/books/001-demo/cover.webp')

    def test_draft_or_stale_mapping_is_rejected(self):
        self.layout['status'] = 'draft'
        with self.assertRaisesRegex(ValueError, 'need review'):
            self.attach()
        self.layout['status'] = 'verified-mapping'
        self.paper['sourceSha256'] = 'old-source'
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.attach()

    def test_missing_and_duplicate_sentence_hotspots_are_rejected(self):
        original = copy.deepcopy(self.paper['hotspots'])
        for hotspots in ([], original + original):
            self.paper['hotspots'] = hotspots
            with self.assertRaisesRegex(ValueError, 'Incomplete or duplicated'):
                self.attach()

    def test_outside_rectangle_and_changed_image_are_rejected(self):
        self.paper['hotspots'][0]['rects'][0]['width'] = 200
        with self.assertRaisesRegex(ValueError, 'outside page'):
            self.attach()
        self.paper['hotspots'][0]['rects'][0]['width'] = 60
        self.paper['imageSha256'] = 'not-the-image'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.attach()

    def test_untrusted_asset_path_is_rejected(self):
        self.paper['image'] = '../outside.webp'
        with self.assertRaisesRegex(ValueError, 'inside its book'):
            self.attach()

    def test_missing_or_changed_cover_is_rejected(self):
        self.layout['cover'] = {'image': '../outside.webp', 'width': 80, 'height': 120,
                                'imageSha256': 'bad'}
        with self.assertRaisesRegex(ValueError, 'inside its book'):
            self.attach()


if __name__ == '__main__':
    unittest.main()
