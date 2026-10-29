"""Match original page recordings to current lesson sentences, with word timings.

Unlike page-span matching, this also handles captions/headings stored out of
spoken order and recordings that cross a PDF page boundary. Uncertain or partial
sentences are reported, never assigned proportional book/page timings.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import subprocess

from align_asr_to_lessons import words
from build_page_audio import probe_duration_ms, transcode
from build_reader_content import CONTENT_ROOT, build_lessons, write_json

ALGORITHM = 'word-timed-sentence-match/1.0'


def tokens(text: str) -> list[str]:
    # PDF pronunciation guides are printed learning aids, not spoken words.
    text = re.sub(r'\([^()]*[a-zA-Z]-[a-zA-Z][^()]*\)', '', text)
    text = re.sub(r'(?<=\d),(?=\d)', '', text)
    # Dropping Unicode dashes during ASCII folding would merge neighbours,
    # e.g. "Lakes—Lake Michigan" would become the non-word "lakeslake".
    text = re.sub(r'[\u2010-\u2015\u2212]', ' ', text)
    return words(text)


def timed_words(record: dict) -> tuple[list[str], list[tuple[int, int]]]:
    if not record.get('asr', {}).get('word_timestamps'):
        raise ValueError('Sentence alignment requires --word-timestamps ASR')
    spoken, times = [], []
    previous = 0
    for segment in record['segments']:
        chunk = tokens(segment['text'])
        start, end = segment['start_ms'], segment['end_ms']
        if not chunk or end < start:
            continue
        if start < previous:
            raise ValueError('ASR timestamps are not monotonic')
        # A split hyphenated word/numeral may contain multiple lexical tokens.
        if len(chunk) > 4 and len(segment['text'].split()) > 4:
            raise ValueError('ASR segment is too long for word-level alignment')
        for index, word in enumerate(chunk):
            spoken.append(word)
            times.append((round(start + (end-start)*index/len(chunk)),
                          round(start + (end-start)*(index+1)/len(chunk))))
        previous = end
    return spoken, times


def locate(needle: list[str], haystack: list[str]) -> dict | None:
    """Find a complete sentence; reject distant common-word coincidences."""
    n, m = len(needle), len(haystack)
    if not n or n > m + 2:
        return None
    exact = [i for i in range(m-n+1) if haystack[i:i+n] == needle]
    if exact:
        return {'start': exact[0], 'stop': exact[0]+n, 'coverage': 1.0,
                'score': 1.0, 'occurrences': len(exact)}
    if n < 5:
        return None
    positions = defaultdict(list)
    for j, word in enumerate(haystack):
        positions[word].append(j)
    starts = set()
    # Rare words provide stable anchors without quadratic substring enumeration.
    anchors = sorted(enumerate(needle), key=lambda x: len(positions[x[1]]) or m+1)[:5]
    for i, word in anchors:
        for j in positions[word]:
            starts.update(range(max(0, j-i-1), min(m, j-i+2)))
    best = None
    for start in sorted(starts):
        for size in range(max(1, n-2), n+3):
            stop = start+size
            if stop > m:
                continue
            window = haystack[start:stop]
            matcher = SequenceMatcher(None, needle, window, autojunk=False)
            matches = sum(b.size for b in matcher.get_matching_blocks())
            coverage = matches/n
            # Short sentences need exact matches; long ones may have a small
            # ASR spelling error, but neither endpoint may be missing.
            if size != n or coverage < 0.8:
                continue
            # Spelling errors such as Daron/Darren are common in tiny.en.
            # Missing/added words indicate a partial recording or another
            # edition; they must not be disguised as an ASR spelling error.
            if any(a != b and (a in {'not', 'no', 'never', 'without'} or
                               b in {'not', 'no', 'never', 'without'} or
                               SequenceMatcher(None, a, b).ratio() < 0.67)
                   for a, b in zip(needle, window)):
                continue
            character = SequenceMatcher(None, ' '.join(needle), ' '.join(window), autojunk=False).ratio()
            if character < 0.93:
                continue
            score = .6*matcher.ratio() + .4*character
            if best is None or score > best['score']:
                best = {'start': start, 'stop': stop, 'coverage': round(coverage, 4),
                        'score': round(score, 4), 'occurrences': 1}
    return best


def candidate_pages(spoken: list[str], pages: list[dict]) -> list[dict]:
    counts = Counter(tuple(spoken[i:i+3]) for i in range(len(spoken)-2))
    ranked = []
    for page in pages:
        if any(u['text'].strip().casefold() in {'table of contents', 'contents'}
               for u in page['sourceUnits'][:2]):
            continue
        score = 0
        for unit in page['sourceUnits']:
            part = tokens(unit['text'])
            score += sum(counts[tuple(part[i:i+3])] for i in range(len(part)-2))
        ranked.append((score, page))
    ranked.sort(key=lambda item: -item[0])
    limit = len(ranked) if len(spoken) > 250 else 3
    return [p for score, p in ranked[:limit] if score > 0]


def match_record(record: dict, pages: list[dict]) -> tuple[list[dict], list[dict]]:
    spoken, times = timed_words(record)
    candidates = []
    for page in candidate_pages(spoken, pages):
        hits = []
        for unit in page['sourceUnits']:
            located = locate(tokens(unit['text']), spoken)
            if located:
                hits.append({'pageId': page['id'], 'unitId': unit['id'], 'text': unit['text'],
                             'kind': unit['kind'], **located})
        # A page must be identified by substantive text, not just a repeated
        # heading or one common word that occurs elsewhere in the recording.
        substantive = [h for h in hits if len(tokens(h['text'])) >= 5]
        if sum(h['stop']-h['start'] for h in substantive) < 5:
            continue
        for hit in hits:
            if hit['occurrences'] > 1:
                continue
            start, stop = hit['start'], hit['stop']
            if stop-start < 4 and not any(
                    0 <= start-other['stop'] <= 3 or 0 <= other['start']-stop <= 3
                    for other in substantive):
                continue
            hit.update(startMs=times[start][0], endMs=times[stop-1][1],
                       heard=' '.join(spoken[start:stop]))
            candidates.append(hit)
    # The title/credits recording has no body sentence with which to identify
    # the page. Only an exact title at its beginning can bind a cover/title page.
    for page in pages[:2]:
        for unit in page['sourceUnits']:
            part = tokens(unit['text'])
            if unit['kind'] != 'title' or part != tokens(page['bookTitle']):
                continue
            found = locate(part, spoken)
            if not found or found['score'] != 1 or found['start'] > 1:
                continue
            if not any(word in spoken[found['stop']:found['stop']+4]
                       for word in ('written', 'by', 'illustrated', 'adapted')):
                continue
            candidates.append({'pageId': page['id'], 'unitId': unit['id'],
                               'text': unit['text'], 'kind': 'title', **found,
                               'startMs': times[found['start']][0],
                               'endMs': times[found['stop']-1][1],
                               'heard': ' '.join(spoken[found['start']:found['stop']]),
                               'sharedTitle': True})
    # Give stronger/longer matches ownership before a common label/title can
    # claim words inside a complete sentence. Duplicate page text is ambiguous.
    candidates.sort(key=lambda h: (-h['score'], -(h['stop']-h['start']), h['pageId']))
    accepted, rejected, occupied, assigned = [], [], set(), set()
    for hit in candidates:
        if hit['unitId'] in assigned:
            continue
        span = set(range(hit['start'], hit['stop']))
        shared_title = hit.get('sharedTitle', False)
        ambiguous = any(not shared_title and other['pageId'] != hit['pageId'] and
                        other['start'] == hit['start'] and other['stop'] == hit['stop']
                        for other in candidates)
        if ambiguous or (span & occupied and not shared_title):
            rejected.append({**hit, 'reason': 'ambiguous_or_overlapping_text'})
            continue
        if not (250 <= hit['endMs']-hit['startMs'] <= max(5000, len(span)*1250)):
            rejected.append({**hit, 'reason': 'implausible_word_timing'})
            continue
        accepted.append(hit)
        assigned.add(hit['unitId'])
        occupied.update(span)
    accepted.sort(key=lambda h: h['start'])
    # Word edges include pauses assigned by ASR. Add padding only in gaps;
    # never include the adjoining spoken word (including unmatched text).
    for hit in accepted:
        a, b = hit['start'], hit['stop']
        before = times[a-1][1] if a else 0
        after = times[b][0] if b < len(times) else hit['endMs']+400
        hit['startMs'] -= min(80, max(0, hit['startMs']-before)//2)
        hit['endMs'] += min(120, max(0, after-hit['endMs'])//2)
    return accepted, rejected


def silence_intervals(audio: Path, cache: Path, ffmpeg: str) -> list[tuple[int, int]]:
    """Measure real pauses, including pauses hidden by zero-duration ASR words."""
    key = hashlib.sha256(audio.read_bytes()).hexdigest()
    cached = cache / f'{key}-silence-36db.json'
    if cached.is_file():
        return [tuple(pair) for pair in json.loads(cached.read_text(encoding='utf-8'))]
    run = subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-i', str(audio),
                          '-af', 'silencedetect=noise=-36dB:d=0.10', '-f', 'null', '-'],
                         capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
    intervals, start = [], None
    for line in run.stderr.splitlines():
        found = re.search(r'silence_(start|end):\s*([0-9.]+)', line)
        if not found:
            continue
        value = round(float(found[2])*1000)
        if found[1] == 'start':
            start = value
        elif start is not None:
            intervals.append((start, value))
            start = None
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(intervals), encoding='utf-8')
    return intervals


def pause_before(point: int, intervals: list[tuple[int, int]]) -> int:
    choices = [(max(a, point-800), min(b, point+100)) for a, b in intervals
               if b >= point-800 and a <= point+100]
    choices = [(a, b) for a, b in choices if b-a >= 140]
    if not choices:
        return point
    a, b = max(choices, key=lambda pair: (min(500, pair[1]-pair[0]), pair[1]))
    # Moving to the preceding pause preserves the first syllable. Never move
    # a start later than its word timestamp.
    return min(point, (a+b)//2)


def refine_boundaries(hits: list[dict], intervals: list[tuple[int, int]]) -> None:
    for hit in hits:
        hit['startMs'] = pause_before(hit['startMs'], intervals)
    starts = sorted({hit['startMs'] for hit in hits})
    following = dict(zip(starts, starts[1:]))
    for hit in hits:
        # Covers and title pages deliberately share the same title range.
        # They are alternative views, not adjacent spoken sentences.
        next_start = following.get(hit['startMs'])
        if next_start is not None and hit['endMs'] > next_start:
            hit['endMs'] = next_start


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--title-map', type=Path, required=True)
    parser.add_argument('--split-root', type=Path, required=True)
    parser.add_argument('--asr-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=Path('.local/page-audio-q'))
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    mapping = json.loads(args.title_map.read_text(encoding='utf-8'))
    library, sources = build_lessons(CONTENT_ROOT)
    source_by_id = {s['config']['id']: s for s in sources}
    selected = {row['book_id'] for row in mapping['books']}
    pages_by_book = {bid: [p for p in library['pages'].values() if p['bookId'] == bid]
                     for bid in selected}
    evidence, rejected, layouts, summaries, unmapped = [], [], {}, [], []
    grouped = defaultdict(list)
    for row in mapping['books']:
        grouped[row['book_id']].append(row)
    for bid, rows in grouped.items():
        row = rows[0]
        pages = pages_by_book[bid]
        records = sorted(path for r in rows for path in
                         (args.asr_root / 'books' / r['source_folder']).glob('*.asr.json'))
        if args.write:
            for r in rows:
                manifest = json.loads((args.split_root / 'books' / r['source_folder'] /
                                       'manifest.json').read_text(encoding='utf-8'))
                for entry in manifest['files']:
                    if entry['role'] == 'page':
                        sidecar = (args.asr_root / 'books' / r['source_folder'] /
                                   entry['file']).with_suffix('.asr.json')
                        if not sidecar.is_file():
                            raise ValueError(f'Incomplete transcription: {sidecar}')
        page_tracks = defaultdict(list)
        for path in records:
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('status') != 'ok':
                if args.write:
                    raise ValueError(f'Failed transcription: {path}')
                continue
            record['_sidecarSha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            audio = args.split_root / record['source_audio']
            if hashlib.sha256(audio.read_bytes()).hexdigest() != record['source_audio_sha256']:
                raise ValueError(f'Changed source audio: {audio}')
            hits, review = match_record(record, pages)
            if args.write and hits:
                refine_boundaries(hits, silence_intervals(audio, args.cache / 'boundaries', args.ffmpeg))
            for hit in hits:
                evidence.append({**hit, 'sourceAudio': record['source_audio'],
                                 'sourceAudioSha256': record['source_audio_sha256']})
            rejected.extend({**h, 'sourceAudio': record['source_audio']} for h in review)
            for page_id in dict.fromkeys(h['pageId'] for h in hits):
                page_tracks[page_id].append((record, [h for h in hits if h['pageId'] == page_id]))
        # Keep each sentence's strongest available recording.
        winners = {}
        for tracks in page_tracks.values():
            for record, hits in tracks:
                for h in hits:
                    key = (h['score'], h['coverage'], -len(record['segments']),
                           -record['location']['sequence'])
                    if h['unitId'] not in winners or key > winners[h['unitId']][0]:
                        winners[h['unitId']] = (key, record['source_audio'])
        layout_pages = {}
        audio_destinations = {}
        for page in pages:
            tracks = []
            for record, hits in page_tracks[page['id']]:
                hits = [h for h in hits if winners[h['unitId']][1] == record['source_audio']]
                if not hits:
                    continue
                name = page['id'].rsplit('-', 1)[-1]
                source = source_by_id[bid]
                # A whole-book fallback is stored once and shared by its pages.
                relative = audio_destinations.setdefault(record['source_audio'],
                    f"{source['relativeRoot']}/pages/{name}/audio/{record['source_audio_sha256'][:12]}.mp3")
                clips = [{k: h[k] for k in ('unitId', 'startMs', 'endMs', 'coverage')} for h in hits]
                if args.write:
                    dest = CONTENT_ROOT / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    payload = transcode(args.split_root / record['source_audio'], args.cache,
                                        dest, '40k', 22050, args.ffmpeg)
                    duration = probe_duration_ms(dest)
                    for clip in clips:
                        clip['endMs'] = min(clip['endMs'], duration)
                    tracks.append({'audio': relative, 'audioSha256': hashlib.sha256(payload).hexdigest(),
                                   'durationMs': duration, 'sourceAudio': record['source_audio'],
                                   'sourceAudioSha256': record['source_audio_sha256'],
                                   'asrSha256': record['_sidecarSha256'],
                                   'candidateOid': record['location']['candidate_oid'],
                                   'transcriptScore': min(h['score'] for h in hits), 'clips': clips})
                else:
                    tracks.append({'sourceAudio': record['source_audio'], 'clips': clips})
            if tracks:
                layout_pages[page['id']] = {'sourceSha256': page['sha256'], 'tracks': tracks}
        layouts[bid] = {'schemaVersion': 'raz-page-audio/1.0', 'status': 'verified-mapping',
                        'source': {'algorithm': ALGORITHM, 'package': mapping['source_package_sha256'],
                                   'splitRoot': args.split_root.as_posix(),
                                   'asrRoot': args.asr_root.as_posix()},
                        'encoding': {'codec': 'mp3', 'channels': 1, 'sampleRate': 22050, 'bitrate': '40k'},
                        'thresholds': {'wordCoverage': .8, 'characterSimilarity': .93,
                                       'wordSpellingSimilarity': .67, 'wordCount': 'exact',
                                       'shortSentences': 'exact_only'},
                        'boundaryRefinement': {'method': 'measured_silence_before_word',
                                               'silenceDb': -36, 'searchBackMs': 800,
                                               'minimumPauseMs': 140},
                        'pages': layout_pages}
        unmapped.extend({'pageId': p['id'], 'unitId': u['id'], 'kind': u['kind'],
                         'text': u['text'], 'reason': 'no_complete_unambiguous_recording'}
                        for p in pages for u in p['sourceUnits'] if u['id'] not in winners)
        summaries.append({'bookId': bid, 'title': row['canonical_title'], 'recordings': len(records),
                          'pages': len(layout_pages), 'clips': len(winners),
                          'sentenceUnits': sum(u['kind'] == 'sentence' for p in pages for u in p['sourceUnits']),
                          'sentenceClips': sum(u['kind'] == 'sentence' and u['id'] in winners
                                               for p in pages for u in p['sourceUnits'])})
        print(f"{bid}: {len(winners)} clips / {len(layout_pages)} pages", flush=True)
    report = {'algorithm': ALGORITHM, 'books': summaries, 'matches': evidence, 'rejected': rejected,
              'unmapped': unmapped,
              'layouts': layouts}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if args.write:
        for bid, layout in layouts.items():
            if layout['pages']:
                write_json(source_by_id[bid]['source'] / 'audio-layout.json', layout)


if __name__ == '__main__':
    main()
