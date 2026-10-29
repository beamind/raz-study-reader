"""Sentence timing from page recordings, and the gates that keep silence honest."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_page_audio import align_units, segments_of, build_tracks, probe_duration_ms
from build_reader_content import attach_page_audio


def unit(number, text):
    return {'id': f'page-u{number:02}', 'kind': 'sentence', 'text': text}


class SentenceTimingTests(unittest.TestCase):
    def test_reviewed_binding_order_places_a_later_added_heading_before_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / 'page.mp3'
            audio.write_bytes(b'ID3 source')
            (root / 'page.asr.json').write_text(json.dumps({'status': 'ok',
                'text': 'In Class. Earth has layers.', 'segments': [
                    {'text': 'In Class.', 'start_ms': 0, 'end_ms': 1000},
                    {'text': 'Earth has layers.', 'start_ms': 1000, 'end_ms': 3000},
                ]}), encoding='utf-8')
            library = {'pages': {'page': {'id': 'page', 'sha256': 'lesson',
                'sourceUnits': [unit(1, 'Earth has layers.'), unit(2, 'In Class.')]}}}
            binding = {'source_audio': 'page.mp3', 'source_audio_sha256':
                hashlib.sha256(audio.read_bytes()).hexdigest(), 'reason': 'reviewed_order',
                'alignment_status': 'candidate', 'sequence': 1, 'candidate_oid': '1',
                'suggested_target': {'page_id': 'page', 'source_unit_ids': ['page-u02', 'page-u01']}}
            with patch('build_page_audio.transcode', return_value=b'encoded'), \
                 patch('build_page_audio.probe_duration_ms', return_value=3000):
                layout, review = build_tracks(library, [binding], root, root, root / 'cache',
                                              {'page': (root / 'page', 'pages/page')}, '40k', 22050)
            self.assertEqual(review, [])
            self.assertEqual([c['unitId'] for c in layout['page']['tracks'][0]['clips']],
                             ['page-u02', 'page-u01'])

    def test_duration_without_ffprobe_counts_mp3_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'page.mp3'
            audio.write_bytes(b'encoded')
            with patch('build_page_audio.shutil.which', return_value=None), \
                 patch('dab_segment_stats.mp3_duration', return_value=(2.612, 100, {22050})):
                self.assertEqual(probe_duration_ms(audio), 2612)
            with patch('build_page_audio.shutil.which', return_value=None), \
                 patch('dab_segment_stats.mp3_duration', return_value=(0, 0, set())):
                with self.assertRaisesRegex(ValueError, 'Cannot measure'):
                    probe_duration_ms(audio)

    def test_each_sentence_gets_the_time_its_own_segment_is_read(self):
        units = [unit(1, 'Some small robots are still not following the map.'),
                 unit(2, 'Our notebook described an imaginary purple lighthouse beside the orchard.')]
        segments = [{'text': 'Some small robots are still not following the map.', 'start_ms': 0, 'end_ms': 3360},
                    {'text': 'Our notebook described an imaginary purple lighthouse beside the orchard.', 'start_ms': 3360, 'end_ms': 9920}]
        clips, review = align_units(units, segments, 10000)
        self.assertEqual(review, [])
        self.assertEqual([clip['unitId'] for clip in clips], ['page-u01', 'page-u02'])
        self.assertEqual(clips[0]['startMs'], 0)
        self.assertEqual(clips[0]['endMs'], 3360)
        self.assertEqual(clips[1]['startMs'], 3360)
        self.assertEqual(clips[1]['endMs'], 9920 + 40)

    def test_two_sentences_inside_one_segment_are_split_without_overlap(self):
        units = [unit(1, 'The nest is empty.'), unit(2, 'The birds flew away.')]
        segments = [{'text': 'The nest is empty. The birds flew away.', 'start_ms': 0, 'end_ms': 8000}]
        clips, review = align_units(units, segments, 8000)
        self.assertEqual(review, [])
        self.assertEqual(clips[0]['startMs'], 0)
        self.assertLessEqual(clips[0]['endMs'], clips[1]['startMs'])
        self.assertEqual(clips[1]['endMs'], 8000)

    def test_a_sentence_the_recording_never_reads_stays_silent(self):
        units = [unit(1, 'Written and illustrated by someone else.'),
                 unit(2, 'The nest is empty.')]
        segments = [{'text': 'The nest is empty.', 'start_ms': 500, 'end_ms': 2500}]
        clips, review = align_units(units, segments, 3000)
        self.assertEqual([clip['unitId'] for clip in clips], ['page-u02'])
        self.assertEqual([(item['unitId'], item['reason']) for item in review],
                         [('page-u01', 'unit_not_heard_in_transcript')])

    def test_a_recording_that_reads_the_page_in_another_order_keeps_only_what_it_can_place(self):
        units = [unit(1, 'The birds flew away.'), unit(2, 'The nest is empty.')]
        segments = [{'text': 'The nest is empty.', 'start_ms': 0, 'end_ms': 2000},
                    {'text': 'The birds flew away.', 'start_ms': 2000, 'end_ms': 4000}]
        clips, review = align_units(units, segments, 4000)
        self.assertEqual([(clip['unitId'], clip['startMs']) for clip in clips], [('page-u02', 0)])
        self.assertEqual([(item['unitId'], item['reason']) for item in review],
                         [('page-u01', 'unit_not_heard_in_transcript')])

    def test_padding_never_reaches_into_the_neighbouring_sentence(self):
        units = [unit(1, 'The nest is empty.'), unit(2, 'The birds flew away.')]
        segments = [{'text': 'The nest is empty.', 'start_ms': 1000, 'end_ms': 2000},
                    {'text': 'The birds flew away.', 'start_ms': 2100, 'end_ms': 3000}]
        clips, _ = align_units(units, segments, 3000)
        self.assertEqual(clips[0]['endMs'], 2050)
        self.assertEqual(clips[1]['startMs'], 2050)

    def test_transcript_times_are_clamped_to_the_real_recording(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        sidecar = Path(directory.name) / 'page.asr.json'
        sidecar.write_text(json.dumps({'status': 'ok', 'segments': [
            {'text': 'The nest is empty.', 'start_ms': 0, 'end_ms': 2000},
            {'text': 'and...', 'start_ms': 2000, 'end_ms': 4200},
        ]}), encoding='utf-8')
        self.assertEqual(segments_of(sidecar, 3000)[-1]['end_ms'], 3000)


class PageAudioBundleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.book = self.root / 'levels/P/books/001-demo'
        self.source = self.book / '_source'
        (self.book / 'pages/p001/audio').mkdir(parents=True)
        self.source.mkdir()
        self.audio_file = self.book / 'pages/p001/audio/01.mp3'
        self.audio_file.write_bytes(b'ID3 fake recording')
        self.layout_path = self.source / 'audio-layout.json'
        self.payload = {'pages': {'page': {
            'bookId': 'demo', 'sha256': 'lesson-hash',
            'sourceUnits': [unit(1, 'The nest is empty.'), unit(2, 'The birds flew away.')],
        }}}
        self.layout = {'schemaVersion': 'raz-page-audio/1.0', 'status': 'verified-mapping', 'pages': {
            'page': {'sourceSha256': 'lesson-hash', 'tracks': [{
                'audio': 'levels/P/books/001-demo/pages/p001/audio/01.mp3',
                'audioSha256': hashlib.sha256(self.audio_file.read_bytes()).hexdigest(),
                'durationMs': 4000,
                'clips': [{'unitId': 'page-u02', 'startMs': 2000, 'endMs': 4000},
                          {'unitId': 'page-u01', 'startMs': 0, 'endMs': 2000}],
            }]},
        }}
        self.book_source = {'config': {'id': 'demo'}, 'root': self.book, 'source': self.source}

    def attach(self, **changes):
        self.layout_path.write_text(json.dumps({**self.layout, **changes}), encoding='utf-8')
        attach_page_audio(self.payload, [self.book_source], self.root)
        return self.payload['pages']['page']

    def test_clips_are_attached_in_playing_order(self):
        page = self.attach()
        self.assertEqual([clip['unitId'] for clip in page['audio'][0]['clips']], ['page-u01', 'page-u02'])
        self.assertEqual(page['audio'][0]['src'],
                         'content/levels/P/books/001-demo/pages/p001/audio/01.mp3')

    def test_a_page_without_audio_is_silent_instead_of_broken(self):
        self.layout_path.unlink(missing_ok=True)
        attach_page_audio(self.payload, [self.book_source], self.root)
        self.assertEqual(self.payload['pages']['page']['audio'], [])

    def test_draft_mapping_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'need review'):
            self.attach(status='draft')

    def test_changed_lesson_invalidates_the_recorded_ranges(self):
        pages = {'page': {**self.layout['pages']['page'], 'sourceSha256': 'other-hash'}}
        with self.assertRaisesRegex(ValueError, 'Stale page audio'):
            self.attach(pages=pages)

    def test_changed_recording_is_refused(self):
        self.audio_file.write_bytes(b'ID3 different recording')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.attach()

    def test_range_outside_the_recording_is_refused(self):
        track = {**self.layout['pages']['page']['tracks'][0],
                 'clips': [{'unitId': 'page-u01', 'startMs': 0, 'endMs': 9000}]}
        with self.assertRaisesRegex(ValueError, 'outside the recording'):
            self.attach(pages={'page': {'sourceSha256': 'lesson-hash', 'tracks': [track]}})

    def test_one_sentence_cannot_be_claimed_by_two_recordings(self):
        track = self.layout['pages']['page']['tracks'][0]
        with self.assertRaisesRegex(ValueError, 'repeated audio sentence'):
            self.attach(pages={'page': {'sourceSha256': 'lesson-hash', 'tracks': [track, track]}})

    def test_audio_outside_the_book_folder_is_refused(self):
        track = {**self.layout['pages']['page']['tracks'][0], 'audio': '../secret.mp3'}
        with self.assertRaisesRegex(ValueError, 'inside its book'):
            self.attach(pages={'page': {'sourceSha256': 'lesson-hash', 'tracks': [track]}})


if __name__ == '__main__':
    unittest.main()
