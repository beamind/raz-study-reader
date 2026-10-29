"""Restore MP3 reader assets from committed audio layouts and local originals.

No ASR or sentence alignment is rerun. Each decoded source and encoded output
must match the hashes in the layout before a reader asset is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from build_page_audio import transcode
from build_reader_content import CONTENT_ROOT, discover_books
from inspect_dab import classify_audio, digest, parse_package, unwrap_audio


def destination_path(content_root: Path, book_root: Path, relative: str) -> Path:
    destination = (content_root / relative).resolve()
    if not destination.is_relative_to(book_root.resolve()) or '_source' in destination.parts:
        raise ValueError(f'Audio destination must be inside its reader book: {relative}')
    if destination.suffix != '.mp3' or destination.parent.name != 'audio':
        raise ValueError(f'Unexpected reader audio destination: {relative}')
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level', required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--cache', type=Path, default=Path('.local/page-audio-restore'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    originals = {}
    restored = reused = 0
    with tempfile.TemporaryDirectory(prefix='raz-audio-restore-') as temporary:
        temp = Path(temporary)
        for book in discover_books(CONTENT_ROOT):
            if book['config']['level'].upper() != args.level.upper():
                continue
            layout_path = book['source'] / 'audio-layout.json'
            if not layout_path.is_file():
                continue
            layout = json.loads(layout_path.read_text(encoding='utf-8'))
            unique = {t['audio']: t for p in layout['pages'].values() for t in p['tracks']}
            for relative, track in unique.items():
                destination = destination_path(CONTENT_ROOT, book['root'], relative)
                if destination.is_file() and digest(destination) == track['audioSha256']:
                    reused += 1
                    continue
                if args.dry_run:
                    print(f'Restore: {relative}')
                    restored += 1
                    continue
                expected = track['sourceAudioSha256']
                source = temp / f'{expected}.mp3'
                if not source.is_file():
                    oid = track.get('candidateOid')
                    if oid is None:
                        narration = book['source'] / 'narration.mp3'
                        if not narration.is_file() or digest(narration) != expected:
                            raise ValueError(f'Original narration missing/changed: {narration}')
                        payload = narration.read_bytes()
                    else:
                        package = book['root'].parents[1] / '_source' / f"raz-{args.level.lower()}.dab"
                        if package not in originals:
                            expected_package = layout['source']['package']
                            if digest(package) != expected_package:
                                raise ValueError(f'Original DAB changed: {package}')
                            _, rows = parse_package(package)
                            originals[package] = {str(r['candidate_oid']): r for r in rows
                                                  if r.get('occupied') and r.get('candidate_oid') is not None}
                        row = originals[package][str(oid)]
                        with package.open('rb') as stream:
                            stream.seek(row['offset'])
                            raw = stream.read(row['stored_length'])
                        payload = unwrap_audio(raw, classify_audio(raw[:64]))
                    if hashlib.sha256(payload).hexdigest() != expected:
                        raise ValueError(f'Extracted source does not match layout: {relative}')
                    source.write_bytes(payload)
                encoded = transcode(source, args.cache, temp / 'encoded.mp3',
                                    layout['encoding']['bitrate'], layout['encoding']['sampleRate'], args.ffmpeg)
                if hashlib.sha256(encoded).hexdigest() != track['audioSha256']:
                    raise ValueError(f'Encoder output differs from layout; use the original FFmpeg version: {relative}')
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(encoded)
                restored += 1
    print(json.dumps({'level': args.level.upper(), 'reused': reused, 'restored': restored,
                      'dryRun': args.dry_run}))


if __name__ == '__main__':
    main()
