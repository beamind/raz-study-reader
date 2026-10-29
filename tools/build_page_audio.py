"""Cut per-sentence play ranges out of the point-reader page audio.

The DAB package stores one recording per printed page, not per sentence, so a
sentence range is derived here: the page transcript produced by
`transcribe_pages.py` keeps a start/end time for every ASR segment, and those
segments are word-aligned against the proofread `source_units` of the lesson.
Each source unit that is covered well enough gets a start/end time inside the
page recording; weaker matches are reported and shipped as nothing at all.

Playback uses the whole page file with a time range, so no audio is re-cut and
one seek serves every sentence on the page. Recordings are transcoded once to
a speech-sized mono MP3 beside the page's runtime files; timings are unaffected
because the timeline does not change.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from align_asr_to_lessons import similarity, words
from build_reader_content import CONTENT_ROOT, ROOT, build_lessons, write_json

SCHEMA_VERSION = 'raz-page-audio/1.0'
# Enough of a sentence must be heard in the transcript before its range is trusted.
MIN_UNIT_COVERAGE = 0.6
MIN_TRACK_SCORE = 0.8
LEAD_MS = 120
TAIL_MS = 200


def segments_of(sidecar: Path, duration_ms: int) -> list[dict]:
    """Read the transcript segments of one page, clamped to the real recording."""
    record = json.loads(sidecar.read_text(encoding='utf-8'))
    if record.get('status') != 'ok':
        raise ValueError(f'Transcript is not usable: {sidecar}')
    segments = []
    for segment in record['segments']:
        start, end = segment.get('start_ms'), segment.get('end_ms')
        if type(start) is not int or type(end) is not int:
            raise ValueError(f'Transcript segment has no timing: {sidecar}')
        start = min(max(start, 0), duration_ms)
        end = min(max(end, start), duration_ms)
        if end > start and words(segment['text']):
            segments.append({'text': segment['text'], 'start_ms': start, 'end_ms': end})
    return segments


def token_times(segments: list[dict]) -> tuple[list[str], list[tuple[int, int]]]:
    """Flatten the transcript into tokens, each with the time slice it occupies.

    whisper.cpp times whole segments, so a token inside a segment is placed by
    its position.  Segment edges stay exact, which is what sentence starts and
    ends usually land on.
    """
    tokens: list[str] = []
    times: list[tuple[int, int]] = []
    for segment in segments:
        spoken = words(segment['text'])
        span = (segment['end_ms'] - segment['start_ms']) / len(spoken)
        for position, token in enumerate(spoken):
            tokens.append(token)
            times.append((round(segment['start_ms'] + span * position),
                          round(segment['start_ms'] + span * (position + 1))))
    return tokens, times


def align_units(units: list[dict], segments: list[dict], duration_ms: int) -> tuple[list[dict], list[dict]]:
    """Give every source unit the time range where the page recording reads it."""
    tokens, times = token_times(segments)
    spoken: list[str] = []
    owners: list[int] = []
    for index, unit in enumerate(units):
        for token in words(unit['text']):
            spoken.append(token)
            owners.append(index)
    matched: dict[int, list[int]] = defaultdict(list)
    for block in SequenceMatcher(None, tokens, spoken, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            matched[owners[block.b + offset]].append(block.a + offset)

    raw, review = [], []
    for index, unit in enumerate(units):
        total = sum(owner == index for owner in owners)
        hits = matched.get(index, [])
        coverage = len(hits) / total if total else 0.0
        if not hits or coverage < MIN_UNIT_COVERAGE:
            review.append({'unitId': unit['id'], 'reason': 'unit_not_heard_in_transcript',
                           'coverage': round(coverage, 4)})
            continue
        # Matching blocks advance through both texts, so ranges stay in reading order.
        raw.append({'unitId': unit['id'], 'coverage': round(coverage, 4),
                    'startMs': times[min(hits)][0], 'endMs': times[max(hits)][1]})

    clips = []
    for position, item in enumerate(raw):
        before = raw[position - 1]['endMs'] if position else 0
        after = raw[position + 1]['startMs'] if position + 1 < len(raw) else duration_ms
        start = item['startMs'] - min(LEAD_MS, max(0, item['startMs'] - before) // 2)
        end = item['endMs'] + min(TAIL_MS, max(0, after - item['endMs']) // 2)
        start, end = max(0, start), min(duration_ms, end)
        if end - start < 200:
            review.append({'unitId': item['unitId'], 'reason': 'range_too_short',
                           'coverage': item['coverage']})
            continue
        clips.append({'unitId': item['unitId'], 'startMs': start, 'endMs': end,
                      'coverage': item['coverage']})
    return clips, review


def probe_duration_ms(audio: Path) -> int:
    if not shutil.which('ffprobe'):
        # The bundled Windows FFmpeg runtime may ship only ffmpeg.exe. Count
        # MP3 frames rather than estimating the duration from file size/bitrate.
        from dab_segment_stats import mp3_duration
        seconds, frames, _ = mp3_duration(audio.read_bytes())
        if not frames or not seconds:
            raise ValueError(f'Cannot measure MP3 duration: {audio}')
        return round(seconds * 1000)
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'default=noprint_wrappers=1:nokey=1', str(audio)],
                            capture_output=True, text=True, check=True)
    return round(float(result.stdout.strip()) * 1000)


def transcode(source: Path, cache: Path, destination: Path, bitrate: str, rate: int,
              ffmpeg: str = 'ffmpeg') -> bytes:
    """Shrink one page recording to speech size once, then reuse it."""
    cached = cache / f'{hashlib.sha256(source.read_bytes()).hexdigest()[:16]}-{rate}-{bitrate}.mp3'
    if not cached.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_name(cached.name + '.tmp.mp3')
        subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                        '-i', str(source), '-ac', '1', '-ar', str(rate), '-b:a', bitrate,
                        '-map_metadata', '-1', str(temporary)], check=True)
        temporary.replace(cached)
    payload = cached.read_bytes()
    if not destination.exists() or destination.read_bytes() != payload:
        destination.write_bytes(payload)
    return payload


def build_tracks(library: dict, bindings: list[dict], split_root: Path, asr_root: Path,
                 cache: Path, page_destinations: dict[str, tuple[Path, str]],
                 bitrate: str, rate: int, ffmpeg: str = 'ffmpeg') -> tuple[dict, list[dict]]:
    pages: dict[str, list[dict]] = defaultdict(list)
    review: list[dict] = []
    for binding in sorted(bindings, key=lambda item: item['source_audio']):
        target = binding.get('suggested_target') or {}
        page = library['pages'].get(target.get('page_id'))
        if binding['reason'] == 'no_lessons_for_book' or not page:
            continue
        note = {'pageId': target.get('page_id'), 'sourceAudio': binding['source_audio'],
                'score': (target.get('metrics') or {}).get('score')}
        if binding['alignment_status'] != 'candidate':
            review.append({**note, 'reason': 'audio_page_binding_needs_review'})
            continue
        by_id = {unit['id']: unit for unit in page['sourceUnits']}
        # The recording can read a heading before body units that were added
        # earlier to the lesson. Retain the reviewed binding's spoken order.
        units = [by_id[unit_id] for unit_id in target['source_unit_ids']]
        source = split_root / binding['source_audio']
        sidecar = (asr_root / binding['source_audio']).with_suffix('.asr.json')
        if not source.is_file() or not sidecar.is_file():
            raise ValueError(f"Missing page audio or transcript: {binding['source_audio']}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != binding['source_audio_sha256']:
            raise ValueError(f"Page audio changed since alignment: {binding['source_audio']}")
        heard = json.loads(sidecar.read_text(encoding='utf-8')).get('text', '')
        score = similarity(heard, ' '.join(unit['text'] for unit in units))['score']
        if score < MIN_TRACK_SCORE:
            review.append({**note, 'reason': 'transcript_unlike_source_text', 'score': round(score, 4)})
            continue
        pages[page['id']].append({'binding': binding, 'units': units, 'source': source,
                                  'sidecar': sidecar, 'score': round(score, 4)})

    layout: dict[str, dict] = {}
    for page_id, items in sorted(pages.items()):
        items.sort(key=lambda item: item['binding']['sequence'])
        tracks, taken = [], set()
        for number, item in enumerate(items, 1):
            page_directory, relative_page = page_destinations[page_id]
            destination = page_directory / 'audio' / f'{number:02}.mp3'
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = transcode(item['source'], cache, destination, bitrate, rate, ffmpeg)
            duration_ms = probe_duration_ms(destination)
            clips, unheard = align_units(item['units'], segments_of(item['sidecar'], duration_ms), duration_ms)
            review.extend({'pageId': page_id, 'sourceAudio': item['binding']['source_audio'], **entry}
                          for entry in unheard)
            kept = []
            for clip in clips:
                if clip['unitId'] in taken:
                    review.append({'pageId': page_id, 'unitId': clip['unitId'],
                                   'sourceAudio': item['binding']['source_audio'],
                                   'reason': 'sentence_already_read_by_another_track'})
                    continue
                taken.add(clip['unitId'])
                kept.append(clip)
            tracks.append({
                'audio': f'{relative_page}/audio/{destination.name}',
                'audioSha256': hashlib.sha256(payload).hexdigest(),
                'durationMs': duration_ms,
                'sourceAudio': item['binding']['source_audio'],
                'sourceAudioSha256': item['binding']['source_audio_sha256'],
                'candidateOid': item['binding']['candidate_oid'],
                'transcriptScore': item['score'],
                'clips': kept,
            })
        layout[page_id] = {'sourceSha256': library['pages'][page_id]['sha256'], 'tracks': tracks}
    return layout, review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split-root', type=Path, default=ROOT / 'analysis/raz-p-071-split',
                        help='Output directory of split_dab_pages.py')
    parser.add_argument('--asr-root', type=Path, default=ROOT / 'analysis/raz-p-071-asr',
                        help='Output directory of transcribe_pages.py')
    parser.add_argument('--bindings', type=Path,
                        default=ROOT / 'analysis/raz-p-071-alignment/binding-candidates.json',
                        help='Output of align_asr_to_lessons.py')
    parser.add_argument('--cache', type=Path, default=ROOT / '.local/page-audio')
    parser.add_argument('--bitrate', default='40k', help='Shipped mono MP3 bitrate (default: 40k)')
    parser.add_argument('--sample-rate', type=int, default=22050)
    parser.add_argument('--ffmpeg', default='ffmpeg', help='FFmpeg executable (default: PATH)')
    parser.add_argument('--level', action='append',
                        help='Only write audio layouts for the named RAZ level; repeatable.')
    args = parser.parse_args()
    if not shutil.which(args.ffmpeg) and not Path(args.ffmpeg).is_file():
        raise SystemExit(f'FFmpeg executable does not exist: {args.ffmpeg}')

    library, sources = build_lessons(CONTENT_ROOT)
    sources_by_book = {source['config']['id']: source for source in sources}
    page_destinations = {}
    for page_id, page in library['pages'].items():
        source = sources_by_book[page['bookId']]
        page_name = page_id.rsplit('-', 1)[-1]
        page_destinations[page_id] = (
            source['root'] / 'pages' / page_name,
            f"{source['relativeRoot']}/pages/{page_name}",
        )
    report = json.loads(args.bindings.read_text(encoding='utf-8'))
    if args.level:
        selected_levels = {level.upper() for level in args.level}
        sources = [source for source in sources
                   if source['config']['level'].upper() in selected_levels]
        if not sources:
            raise SystemExit(f'No enabled books found for level(s): {sorted(selected_levels)}')
    else:
        target_pages = {
            target['page_id']
            for binding in report['bindings']
            if (target := binding.get('suggested_target')) and target.get('page_id') in library['pages']
        }
        target_books = {library['pages'][page_id]['bookId'] for page_id in target_pages}
        sources = [source for source in sources if source['config']['id'] in target_books]
        if not sources:
            raise SystemExit('Bindings do not target any enabled books; pass --level to write an empty layout intentionally.')
    layout, review = build_tracks(library, report['bindings'], args.split_root.resolve(),
                                  args.asr_root.resolve(), args.cache,
                                  page_destinations,
                                  args.bitrate, args.sample_rate, args.ffmpeg)

    clips = sum(len(track['clips']) for page in layout.values() for track in page['tracks'])
    common = {
        'schemaVersion': SCHEMA_VERSION,
        'status': 'verified-mapping',
        'source': {'algorithm': report['algorithm'], 'package': report['bindings'][0]['package_id']
                   if report['bindings'] else None},
        'encoding': {'codec': 'mp3', 'channels': 1, 'sampleRate': args.sample_rate,
                     'bitrate': args.bitrate},
        'thresholds': {'unitCoverage': MIN_UNIT_COVERAGE, 'trackScore': MIN_TRACK_SCORE,
                       'leadMs': LEAD_MS, 'tailMs': TAIL_MS},
    }
    for source in sources:
        book_id = source['config']['id']
        write_json(source['source'] / 'audio-layout.json', {
            **common,
            'pages': {page_id: page for page_id, page in layout.items()
                      if library['pages'][page_id]['bookId'] == book_id},
        })
    args.cache.mkdir(parents=True, exist_ok=True)
    (args.cache / 'audio-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2),
                                                  encoding='utf-8')
    selected_books = {source['config']['id'] for source in sources}
    selected_pages = [page for page in library['pages'].values() if page['bookId'] in selected_books]
    heard = sum(len(page['sourceUnits']) for page in selected_pages)
    print(f'Timed {clips} sentences of {heard} on {len(layout)} of {len(selected_pages)} pages; '
          f'{len(review)} left silent. See {args.cache / "audio-review.json"}')


if __name__ == '__main__':
    main()
