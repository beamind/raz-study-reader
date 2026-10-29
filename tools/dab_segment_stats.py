"""Measure per-entry audio durations in a deb3 DAB package to infer split granularity.

Walks MP3 frame headers instead of shelling out to ffprobe once per entry, so all
entries can be measured rather than a sample. Read-only on the source package.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_dab import parse_package, classify_audio, unwrap_audio  # noqa: E402

MPEG1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
MPEG2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
MPEG1_L2 = [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384]
MPEG1_L1 = [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448]
MPEG2_L1 = [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256]
SAMPLE_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def skip_id3v2(data: bytes) -> int:
    if not data.startswith(b"ID3") or len(data) < 10:
        return 0
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    return 10 + size + (10 if data[5] & 0x10 else 0)


def mp3_duration(data: bytes) -> tuple[float, int, set[int]]:
    """Return (seconds, frame_count, sample_rates_seen) by walking frame headers."""
    pos = skip_id3v2(data)
    end = len(data)
    if data[-128:end][:3] == b"TAG":
        end -= 128
    seconds = 0.0
    frames = 0
    rates: set[int] = set()
    resyncs = 0
    while pos + 4 <= end:
        if data[pos] != 0xFF or data[pos + 1] & 0xE0 != 0xE0:
            pos += 1
            resyncs += 1
            if resyncs > 1 << 20:
                break
            continue
        version = (data[pos + 1] >> 3) & 3
        layer = 4 - ((data[pos + 1] >> 1) & 3)
        bitrate_index = data[pos + 2] >> 4
        rate_index = (data[pos + 2] >> 2) & 3
        if version == 1 or layer == 4 or bitrate_index in (0, 15) or rate_index == 3:
            pos += 1
            continue
        rate = SAMPLE_RATES[version][rate_index]
        if layer == 1:
            table = MPEG1_L1 if version == 3 else MPEG2_L1
        elif layer == 2:
            table = MPEG1_L2 if version == 3 else MPEG2_L3
        else:
            table = MPEG1_L3 if version == 3 else MPEG2_L3
        bitrate = table[bitrate_index] * 1000
        padding = (data[pos + 2] >> 1) & 1
        if layer == 1:
            samples = 384
            size = (12 * bitrate // rate + padding) * 4
        elif layer == 2:
            samples = 1152
            size = 144 * bitrate // rate + padding
        else:
            samples = 1152 if version == 3 else 576
            size = (144 if version == 3 else 72) * bitrate // rate + padding
        if size <= 4:
            pos += 1
            continue
        seconds += samples / rate
        frames += 1
        rates.add(rate)
        pos += size
    return seconds, frames, rates


def measure_package(source: Path) -> tuple[dict, list[dict]]:
    metadata, rows = parse_package(source)
    active = [r for r in rows if r["occupied"]]
    results = []
    with source.open("rb") as stream:
        for row in active:
            stream.seek(row["offset"])
            raw = stream.read(row["stored_length"])
            mode = classify_audio(raw[:64])
            if mode == "unknown":
                results.append({**{k: row[k] for k in ("kind", "slot", "candidate_oid",
                                                       "offset", "stored_length")},
                                "seconds": None, "frames": 0, "sample_rates": []})
                continue
            decoded = unwrap_audio(raw, mode)
            seconds, frames, rates = mp3_duration(decoded)
            results.append({**{k: row[k] for k in ("kind", "slot", "candidate_oid",
                                                   "offset", "stored_length")},
                            "seconds": round(seconds, 3), "frames": frames,
                            "sample_rates": sorted(rates)})
    return metadata, results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata, results = measure_package(args.source.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"title": metadata["title"], "entries": results},
                                      ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    durations = [r["seconds"] for r in results if r["kind"] == "content" and r["seconds"]]
    durations.sort()
    print(json.dumps({
        "content_entries": len(durations),
        "total_hours": round(sum(durations) / 3600, 2),
        "min": durations[0], "max": durations[-1],
        "median": durations[len(durations) // 2],
        "percentiles": {p: durations[int(len(durations) * p / 100)] for p in (1, 5, 10, 25, 75, 90, 95, 99)},
    }, indent=2))


if __name__ == "__main__":
    main()
