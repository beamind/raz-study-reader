"""Regression cases for mapping recordings to the revised Q lessons."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from align_sentence_audio import locate, match_record, timed_words, tokens, pause_before, refine_boundaries


def record(text):
    parts = text.split()
    return {'asr': {'word_timestamps': True}, 'segments': [
        {'text': word, 'start_ms': i*400, 'end_ms': (i+1)*400}
        for i, word in enumerate(parts)]}


def page(number, units):
    return {'id': f'p{number}', 'bookTitle': 'A Different Book', 'sourceUnits': [
        {'id': f'p{number}-u{i}', 'kind': kind, 'text': text}
        for i, (kind, text) in enumerate(units)]}


class SentenceAudioAlignmentTests(unittest.TestCase):
    def test_zero_length_asr_words_are_retained(self):
        data = record('He and his robot rolled past the fountain.')
        data['segments'][0]['end_ms'] = 0
        data['segments'][1]['start_ms'] = 0
        spoken, times = timed_words(data)
        self.assertEqual(spoken[0], 'he')
        self.assertEqual(times[0], (0, 0))
        hits, _ = match_record(data, [page(1, [('sentence', 'He and his robot rolled past the fountain.')])])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['startMs'], 0)

    def test_page_order_and_late_added_heading_do_not_change_spoken_order(self):
        data = record('Workshop Plans. Seven tiny robots roll quietly beside the workshop.')
        hits, _ = match_record(data, [page(1, [
            ('sentence', 'Seven tiny robots roll quietly beside the workshop.'), ('title', 'Workshop Plans.')])])
        self.assertEqual([h['unitId'] for h in hits], ['p1-u1', 'p1-u0'])

    def test_one_recording_can_cross_two_pdf_pages(self):
        data = record('Seven tiny robots roll quietly beside the workshop. Each silver wheel leaves a zigzag mark on sand.')
        hits, _ = match_record(data, [
            page(1, [('sentence', 'Seven tiny robots roll quietly beside the workshop.')]),
            page(2, [('sentence', 'Each silver wheel leaves a zigzag mark on sand.')])])
        self.assertEqual([h['pageId'] for h in hits], ['p1', 'p2'])
        self.assertLessEqual(hits[0]['endMs'], hits[1]['startMs'])

    def test_unread_caption_and_glossary_definition_do_not_claim_body_words(self):
        data = record('Seven tiny robots roll quietly beside the workshop.')
        hits, _ = match_record(data, [page(1, [
            ('sentence', 'Seven tiny robots roll quietly beside the workshop.'),
            ('caption', 'Animals'), ('vocab', 'ocean: a very large body of salt water')])])
        self.assertEqual([h['unitId'] for h in hits], ['p1-u0'])

    def test_similar_but_different_edition_and_missing_negation_are_rejected(self):
        self.assertIsNone(locate(tokens('They do not like living in cages.'), tokens('They do like living in cages.')))
        self.assertIsNone(locate(tokens('After the war, the army formed new regiments.'),
                                 tokens('Before the war, people formed their own governments.')))
        self.assertIsNone(locate(tokens('During the rainy season, many bright umbrellas were opened across town.'),
                                 tokens('During the rainy season, many umbrellas were opened across town.')))
        self.assertIsNone(locate(tokens('What powers this garden robot, and how can it carry a heavy basket?'),
                                 tokens('What powers this garden robot XYZ, and how can it carry a heavy basket?')))

    def test_repeated_text_on_two_pages_is_ambiguous(self):
        text = 'Seven tiny robots roll quietly beside the workshop.'
        hits, rejected = match_record(record(text), [page(1, [('sentence', text)]), page(2, [('sentence', text)])])
        self.assertEqual(hits, [])
        self.assertEqual(len(rejected), 2)

    def test_pronunciation_guides_and_thousands_separators(self):
        self.assertEqual(tokens('Inside the Gazebo (guh-ZEE-boh), 2,700 helpers waved.'),
                         tokens('Inside the Gazebo, 2700 helpers waved.'))
        self.assertEqual(tokens('Our shelter is a cabin—a small, wooden house—that was painted by the team.'),
                         tokens('Our shelter is a cabin, a small wooden house that was painted by the team.'))

    def test_coarse_timestamps_cannot_be_used_as_word_alignment(self):
        with self.assertRaisesRegex(ValueError, 'word-timestamps'):
            timed_words({'asr': {}, 'segments': []})

    def test_zero_word_boundary_moves_to_measured_pause_and_keeps_clips_disjoint(self):
        intervals = [(47000, 47500), (48020, 48500)]
        self.assertEqual(pause_before(47820, intervals), 47260)
        hits = [{'startMs': 45000, 'endMs': 47820}, {'startMs': 47820, 'endMs': 50910}]
        refine_boundaries(hits, intervals)
        self.assertEqual(hits[0]['endMs'], hits[1]['startMs'])
        self.assertEqual(hits[1]['startMs'], 47260)
        self.assertEqual(pause_before(1000, []), 1000)

    def test_shared_cover_title_ranges_keep_their_full_duration(self):
        hits = [{'startMs': 30, 'endMs': 2000}, {'startMs': 30, 'endMs': 2000},
                {'startMs': 2500, 'endMs': 6000}]
        refine_boundaries(hits, [])
        self.assertEqual(hits[0]['endMs'], 2000)
        self.assertEqual(hits[1]['endMs'], 2000)


if __name__ == '__main__':
    unittest.main()
