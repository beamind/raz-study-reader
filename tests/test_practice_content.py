"""Check same-book evidence and shared banks before publishing offline practice."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from synthetic_content import synthetic_book
from practice_content import compile_practice

BOOK, PAGES, AUTHORING = synthetic_book('P', 1)


class PracticeContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = PAGES

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / 'practice'
        self.folder.mkdir()
        self.reset_fixture()

    def reset_fixture(self):
        (self.folder / 'exercises.json').write_text(json.dumps(AUTHORING), encoding='utf-8')

    def edit(self, fn):
        path = self.folder / 'exercises.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        fn(data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def compile(self, pages=None):
        return compile_practice('raz-p-001', self.folder, pages or self.pages)

    def test_five_sentences_share_seven_options_from_this_book(self):
        pack = self.compile()
        self.assertEqual(len(pack['questions']), 5)
        self.assertEqual(len(pack['options']), 7)
        self.assertEqual(pack['options'][:5], [{'id': q['answerId'], 'text': q['blank']['text']} for q in pack['questions']])
        self.assertEqual([o['text'] for o in pack['options'][5:]], ['miniature', 'conceive'])
        self.assertTrue(set(o['id'] for o in pack['options'][5:]).isdisjoint(q['answerId'] for q in pack['questions']))
        for q in pack['questions']:
            source = q['source']
            self.assertEqual(self.pages[source['pageId']]['bookId'], 'raz-p-001')
            self.assertIn(source['text'], next(u['text'] for u in self.pages[source['pageId']]['sourceUnits'] if u['id'] == source['unitId']))
            self.assertNotIn('options', q)
        self.assertNotIn('sourcePath', json.dumps(pack))

    def test_draft_is_validated_but_not_published(self):
        self.edit(lambda d: d.update(status='draft'))
        self.assertIsNone(self.compile())
        self.edit(lambda d: d['questions'][0]['blank'].update(text='wrong'))
        with self.assertRaisesRegex(ValueError, 'Blank does not restore'):
            self.compile()

    def test_source_cannot_be_rewritten(self):
        self.edit(lambda d: d['questions'][0]['source'].update(text='Invented sentence.'))
        with self.assertRaisesRegex(ValueError, 'Source quote mismatch'):
            self.compile()

    def test_source_cannot_come_from_another_book(self):
        pages = copy.deepcopy(self.pages)
        pages['raz-p-001-p005']['bookId'] = 'another-book'
        with self.assertRaisesRegex(ValueError, 'belong to this book'):
            self.compile(pages)

    def test_blank_must_use_the_exact_original_word_form(self):
        self.edit(lambda d: d['questions'][2]['blank'].update(text='measure'))
        with self.assertRaisesRegex(ValueError, 'Blank does not restore'):
            self.compile()

    def test_blank_cannot_split_a_word(self):
        def change(d):
            blank = d['questions'][2]['blank']
            blank.update(end=blank['end'] - 1, text='measure')
        self.edit(change)
        with self.assertRaisesRegex(ValueError, 'splits a word'):
            self.compile()

    def test_duplicate_answers_are_rejected(self):
        def change(d):
            duplicate = copy.deepcopy(d['questions'][0])
            duplicate['id'] = 'another-id'
            d['questions'][1] = duplicate
        self.edit(change)
        with self.assertRaisesRegex(ValueError, 'Duplicate bank word'):
            self.compile()

    def test_changed_lesson_invalidates_source(self):
        pages = copy.deepcopy(self.pages)
        pages['raz-p-001-p005']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Lesson changed'):
            self.compile(pages)

    def test_mismatched_unit_block_is_rejected(self):
        self.edit(lambda d: d['questions'][0]['source'].update(blockId='raz-p-001-p005-s03'))
        with self.assertRaisesRegex(ValueError, 'Invalid source unit/block'):
            self.compile()

    def test_exactly_five_questions_are_required(self):
        for count in (0, 1, 3, 4, 6):
            with self.subTest(count=count):
                self.reset_fixture()
                self.edit(lambda d: d.update(questions=(d['questions'] * 2)[:count]))
                with self.assertRaisesRegex(ValueError, 'exactly five'):
                    self.compile()

    def test_exactly_two_distractors_are_required(self):
        for count in (0, 1, 3):
            with self.subTest(count=count):
                self.reset_fixture()
                self.edit(lambda d: d.update(distractors=(d['distractors'] * 2)[:count]))
                with self.assertRaisesRegex(ValueError, 'exactly two'):
                    self.compile()

    def test_distractor_evidence_and_uniqueness_are_checked(self):
        def duplicate_answer(d):
            q = d['questions'][0]
            d['distractors'][0].update(source=q['source'], expression=q['blank'])
        cases = [
            (lambda d: d['distractors'][0]['source'].update(pageId='another-book'), 'belong to this book'),
            (lambda d: d['distractors'][0]['source'].update(lessonSha256='old'), 'Lesson changed'),
            (lambda d: d['distractors'][0]['source'].update(blockId='missing'), 'Invalid source unit/block'),
            (lambda d: d['distractors'][0]['source'].update(text='Invented sentence.'), 'Source quote mismatch'),
            (lambda d: d['distractors'][0]['expression'].update(text='miniatures'), 'Blank does not restore'),
            (lambda d: d['distractors'][0]['expression'].update(end=d['distractors'][0]['expression']['end'] - 1, text='miniatur'), 'splits a word'),
            (lambda d: d['distractors'][0].update(id=d['questions'][0]['id']), 'Duplicate option ID'),
            (lambda d: d['distractors'][1].update(id=d['distractors'][0]['id']), 'Duplicate option ID'),
            (lambda d: d['distractors'][1].update(source=d['distractors'][0]['source'], expression=d['distractors'][0]['expression']), 'Duplicate bank word'),
            (duplicate_answer, 'Duplicate bank word'),
            (lambda d: d['distractors'][0].update(meaning=''), 'Missing meaning'),
        ]
        for change, error in cases:
            with self.subTest(error=error):
                self.reset_fixture()
                self.edit(change)
                with self.assertRaisesRegex(ValueError, error):
                    self.compile()

    def test_missing_feedback_is_rejected(self):
        self.edit(lambda d: d['questions'][0].update(meaning=''))
        with self.assertRaisesRegex(ValueError, 'Missing meaning'):
            self.compile()

    def test_feedback_edit_changes_content_version(self):
        before = self.compile()['version']
        self.edit(lambda d: d['questions'][0].update(explanation='Updated explanation.'))
        self.assertNotEqual(before, self.compile()['version'])

    def test_source_offsets_are_unicode_codepoints(self):
        pages = copy.deepcopy(self.pages)
        unit = next(u for u in pages['raz-p-001-p005']['sourceUnits'] if u['id'] == 'raz-p-001-p005-u02')
        unit['text'] = '🌳 ' + unit['text']
        def change(d):
            q = d['questions'][0]
            q['source']['text'] = '🌳 ' + q['source']['text']
            q['source']['end'] += 2
            q['blank']['start'] += 2
            q['blank']['end'] += 2
        self.edit(change)
        self.assertTrue(self.compile(pages)['questions'][0]['source']['text'].startswith('🌳'))



if __name__ == '__main__':
    unittest.main()
