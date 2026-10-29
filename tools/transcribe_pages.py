"""Transcribe split RAZ page audio with a small local whisper.cpp model.

The input is the directory produced by split_dab_pages.py.  Jobs are discovered
from each book's manifest.json, so filename ordering is never used to infer a
page, slot, or OID.  Each source MP3 gets a same-stem .txt file and a canonical
.asr.json sidecar in a mirrored output tree.

The command is resumable: an existing successful sidecar is reused only when
the audio hash, model hash, language, prompt, and engine version still match.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT_ROOT / ".local" / "asr" / "models" / "ggml-tiny.en-q5_1.bin"
DEFAULT_RUNTIME = PROJECT_ROOT / ".local" / "asr" / "runtime"
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text.rstrip() + "\n")
    temporary.replace(path)


def find_whisper_cli(explicit: Path | None) -> Path:
    if explicit:
        return explicit.resolve()
    names = ("whisper-cli.exe", "whisper-cli") if os.name == "nt" else ("whisper-cli",)
    for name in names:
        matches = sorted(DEFAULT_RUNTIME.rglob(name)) if DEFAULT_RUNTIME.exists() else []
        if matches:
            return matches[0].resolve()
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    raise FileNotFoundError(
        "whisper-cli was not found. Run: pwsh -File tools/setup_local_asr.ps1")


def engine_version(executable: Path) -> str:
    result = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=False)
    line = (result.stdout or result.stderr).strip().splitlines()
    return line[0].strip() if line else "unknown"


def discover_jobs(input_root: Path, output_root: Path, roles: set[str],
                  book_patterns: list[str]) -> list[dict]:
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    manifests = sorted((input_root / "books").glob("*/manifest.json"))
    if not manifests:
        raise ValueError(f"No book manifests found under {input_root / 'books'}")

    jobs: list[dict] = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        folder = manifest_path.parent.name
        title = manifest.get("title") or ""
        if book_patterns and not any(
                fnmatch.fnmatchcase(folder.casefold(), pattern.casefold())
                or fnmatch.fnmatchcase(title.casefold(), pattern.casefold())
                for pattern in book_patterns):
            continue
        for entry in manifest.get("files", []):
            if entry.get("role") not in roles:
                continue
            audio = manifest_path.parent / entry["file"]
            if not audio.is_file():
                raise FileNotFoundError(f"Manifest audio is missing: {audio}")
            relative_audio = audio.relative_to(input_root)
            destination = output_root / relative_audio.parent
            stem = audio.stem
            jobs.append({
                "audio": audio,
                "source_audio": relative_audio.as_posix(),
                "text_path": destination / f"{stem}.txt",
                "sidecar_path": destination / f"{stem}.asr.json",
                "book_folder": folder,
                "book_index": manifest.get("book_index"),
                "title": manifest.get("title"),
                "role": entry.get("role"),
                "sequence": entry.get("sequence"),
                "slot": entry.get("slot"),
                "candidate_oid": entry.get("candidate_oid"),
                "paper_page": entry.get("paper_page"),
                "seconds": entry.get("seconds"),
            })
    return jobs


def configuration(model: Path, model_hash: str, version: str, language: str,
                  prompt: str | None, threads: int, word_timestamps: bool = False) -> dict:
    config = {
        "engine": "whisper.cpp",
        "engine_version": version,
        "model": model.name,
        "model_sha256": model_hash,
        "language": language,
        "prompt": prompt,
        "threads": threads,
    }
    if word_timestamps:
        config["word_timestamps"] = True
    return config


def current_sidecar(job: dict, audio_hash: str, config: dict) -> dict | None:
    path = job["sidecar_path"]
    if not path.is_file() or not job["text_path"].is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (payload.get("schema_version") == SCHEMA_VERSION
            and payload.get("status") == "ok"
            and payload.get("source_audio") == job["source_audio"]
            and payload.get("source_audio_sha256") == audio_hash
            and payload.get("text_sha256") == sha256(job["text_path"])
            and payload.get("asr") == config):
        return payload
    return None


def clean_text(text: str) -> str:
    return " ".join(text.replace("\ufeff", "").split()).strip()


def text_sha256(text: str) -> str:
    """Hash the exact normalized bytes written by save_text."""
    return hashlib.sha256((text.rstrip() + "\n").encode("utf-8")).hexdigest()


def compact_segments(raw: dict) -> list[dict]:
    compact: list[dict] = []
    for segment in raw.get("transcription", []):
        item = {"text": clean_text(str(segment.get("text", "")))}
        offsets = segment.get("offsets")
        timestamps = segment.get("timestamps")
        if isinstance(offsets, dict):
            item["start_ms"] = offsets.get("from")
            item["end_ms"] = offsets.get("to")
        if isinstance(timestamps, dict):
            item["start"] = timestamps.get("from")
            item["end"] = timestamps.get("to")
        probabilities = [token.get("p") for token in segment.get("tokens", [])
                         if isinstance(token, dict) and isinstance(token.get("p"), (int, float))]
        if probabilities:
            item["mean_token_probability"] = round(sum(probabilities) / len(probabilities), 4)
        compact.append(item)
    return compact


def result_record(job: dict, audio_hash: str, config: dict, text: str,
                  raw_json: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "source_audio": job["source_audio"],
        "source_audio_sha256": audio_hash,
        "text_file": job["text_path"].name,
        "text_sha256": text_sha256(text),
        "book": {
            "folder": job["book_folder"],
            "index": job["book_index"],
            "title": job["title"],
        },
        "location": {
            "role": job["role"],
            "sequence": job["sequence"],
            "slot": job["slot"],
            "candidate_oid": job["candidate_oid"],
            "paper_page": job["paper_page"],
            "audio_seconds": job["seconds"],
        },
        "asr": config,
        "text": text,
        "segments": compact_segments(raw_json),
        "transcribed_at": utc_now(),
    }


def error_record(job: dict, audio_hash: str | None, config: dict, message: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "source_audio": job["source_audio"],
        "source_audio_sha256": audio_hash,
        "book": {"folder": job["book_folder"], "index": job["book_index"],
                 "title": job["title"]},
        "location": {
            "role": job["role"],
            "sequence": job["sequence"],
            "slot": job["slot"],
            "candidate_oid": job["candidate_oid"],
            "paper_page": job["paper_page"],
            "audio_seconds": job["seconds"],
        },
        "asr": config,
        "error": message,
        "transcribed_at": utc_now(),
    }


def convert_one(ffmpeg: str, job: dict, wav: Path) -> tuple[bool, str]:
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(job["audio"]), "-ar", "16000", "-ac", "1", "-c:a",
               "pcm_s16le", str(wav)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", check=False)
    if result.returncode or not wav.is_file() or wav.stat().st_size <= 44:
        detail = (result.stderr or result.stdout).strip()
        return False, f"ffmpeg conversion failed ({result.returncode}): {detail[-500:]}"
    return True, ""


def process_batch(jobs_with_hashes: list[tuple[dict, str]], whisper: Path, model: Path,
                  ffmpeg: str, config: dict, conversion_workers: int) -> list[dict]:
    records: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="smart-raz-asr-") as directory:
        temporary = Path(directory)
        prepared: list[tuple[dict, str, Path]] = []
        futures = {}
        with ThreadPoolExecutor(max_workers=conversion_workers) as pool:
            for number, (job, audio_hash) in enumerate(jobs_with_hashes):
                wav = temporary / f"audio-{number:04d}.wav"
                futures[pool.submit(convert_one, ffmpeg, job, wav)] = (job, audio_hash, wav)
            for future in as_completed(futures):
                job, audio_hash, wav = futures[future]
                try:
                    ok, message = future.result()
                except Exception as exc:  # keep the rest of a long batch resumable
                    ok, message = False, f"ffmpeg invocation failed: {exc}"
                if ok:
                    prepared.append((job, audio_hash, wav))
                else:
                    record = error_record(job, audio_hash, config, message)
                    save_json(job["sidecar_path"], record)
                    records.append(record)

        prepared.sort(key=lambda item: item[2].name)
        if not prepared:
            return records
        command = [str(whisper), "-m", str(model), "-l", config["language"],
                   "-t", str(config["threads"]), "-otxt", "-ojf", "-np"]
        if config.get("word_timestamps"):
            command += ["-ml", "1", "-sow"]
        if config["prompt"]:
            command += ["--prompt", config["prompt"]]
        command += [str(item[2]) for item in prepared]
        run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", check=False)

        for job, audio_hash, wav in prepared:
            raw_text_path = Path(str(wav) + ".txt")
            raw_json_path = Path(str(wav) + ".json")
            if not raw_text_path.is_file() or not raw_json_path.is_file():
                detail = (run.stderr or run.stdout).strip()
                message = (f"whisper-cli produced no complete output (exit {run.returncode}): "
                           f"{detail[-800:]}")
                record = error_record(job, audio_hash, config, message)
            else:
                try:
                    raw = json.loads(raw_json_path.read_text(encoding="utf-8-sig"))
                    text = clean_text(raw_text_path.read_text(encoding="utf-8-sig"))
                    save_text(job["text_path"], text)
                    record = result_record(job, audio_hash, config, text, raw)
                except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                    record = error_record(job, audio_hash, config,
                                          f"Cannot read whisper output: {exc}")
            save_json(job["sidecar_path"], record)
            records.append(record)
    return records


def write_index(output_root: Path, input_root: Path, config: dict | None,
                jobs: list[dict], records: dict[str, dict], planned: int) -> None:
    ordered = [records[job["source_audio"]] for job in jobs
               if job["source_audio"] in records]
    summary = {
        "total": len(jobs),
        "planned_this_run": planned,
        "ok": sum(r.get("status") == "ok" for r in ordered),
        "error": sum(r.get("status") == "error" for r in ordered),
        "remaining": len(jobs) - sum(r.get("status") == "ok" for r in ordered),
    }
    save_json(output_root / "index.json", {
        "schema_version": SCHEMA_VERSION,
        "source_root": str(input_root.resolve()),
        "generated_at": utc_now(),
        "asr": config,
        "summary": summary,
        "items": [{
            "source_audio": record["source_audio"],
            "status": record["status"],
            "text": record.get("text"),
            "candidate_oid": record.get("location", {}).get("candidate_oid"),
            "sequence": record.get("location", {}).get("sequence"),
            "sidecar": str(job["sidecar_path"].relative_to(output_root).as_posix()),
        } for job in jobs if (record := records.get(job["source_audio"]))],
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Output directory from split_dab_pages.py")
    parser.add_argument("--output", type=Path, required=True,
                        help="Mirrored transcript output directory")
    parser.add_argument("--whisper-cli", type=Path, help="Path to whisper-cli executable")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL,
                        help=f"GGML model (default: {DEFAULT_MODEL})")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable (default: PATH)")
    parser.add_argument("--roles", nargs="+", default=["page"],
                        choices=["page", "word", "whole_book"],
                        help="Manifest roles to transcribe (default: page)")
    parser.add_argument("--book", action="append", default=[], metavar="GLOB",
                        help="Only matching book folder/title; repeatable")
    parser.add_argument("--language", default="en", help="Whisper language (default: en)")
    parser.add_argument("--prompt", help="Optional initial Whisper prompt")
    parser.add_argument("--word-timestamps", action="store_true",
                        help="Keep word-level segments for more precise sentence playback boundaries")
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 4),
                        help="Whisper CPU threads (default: min(4, CPU count))")
    parser.add_argument("--batch-size", type=int, default=24,
                        help="Pages per model load (default: 24)")
    parser.add_argument("--conversion-workers", type=int, default=2,
                        help="Concurrent ffmpeg conversions (default: 2)")
    parser.add_argument("--limit", type=int, help="Process at most N pending files")
    parser.add_argument("--force", action="store_true", help="Reprocess matching sidecars")
    parser.add_argument("--dry-run", action="store_true", help="List work without running ASR")
    args = parser.parse_args()

    for name in ("threads", "batch_size", "conversion_workers"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    input_root, output_root = args.input.resolve(), args.output.resolve()
    jobs = discover_jobs(input_root, output_root, set(args.roles), args.book)
    if not jobs:
        parser.error("No manifest entries match the selected roles/books")

    if args.dry_run:
        preview = jobs[:args.limit] if args.limit else jobs
        print(json.dumps({"matched": len(jobs), "planned": len(preview),
                          "items": [job["source_audio"] for job in preview]},
                         ensure_ascii=False, indent=2))
        return 0

    model = args.model.resolve()
    if not model.is_file():
        parser.error(f"Model does not exist: {model}; run tools/setup_local_asr.ps1")
    whisper = find_whisper_cli(args.whisper_cli)
    if not whisper.is_file():
        parser.error(f"whisper-cli does not exist: {whisper}")
    if not shutil.which(args.ffmpeg) and not Path(args.ffmpeg).is_file():
        parser.error(f"ffmpeg does not exist: {args.ffmpeg}")

    model_hash = sha256(model)
    config = configuration(model, model_hash, engine_version(whisper), args.language,
                           args.prompt, args.threads, args.word_timestamps)
    records: dict[str, dict] = {}
    pending: list[tuple[dict, str]] = []
    print(f"Checking {len(jobs)} audio files...", file=sys.stderr)
    for job in jobs:
        audio_hash = sha256(job["audio"])
        existing = None if args.force else current_sidecar(job, audio_hash, config)
        if existing:
            records[job["source_audio"]] = existing
        else:
            pending.append((job, audio_hash))
    if args.limit:
        pending = pending[:args.limit]
    write_index(output_root, input_root, config, jobs, records, len(pending))

    total_batches = (len(pending) + args.batch_size - 1) // args.batch_size
    for start in range(0, len(pending), args.batch_size):
        batch = pending[start:start + args.batch_size]
        number = start // args.batch_size + 1
        print(f"ASR batch {number}/{total_batches}: {len(batch)} files", file=sys.stderr)
        for record in process_batch(batch, whisper, model, args.ffmpeg, config,
                                    args.conversion_workers):
            records[record["source_audio"]] = record
        write_index(output_root, input_root, config, jobs, records, len(pending))

    ok = sum(record.get("status") == "ok" for record in records.values())
    errors = [record for record in records.values() if record.get("status") == "error"]
    print(json.dumps({"matched": len(jobs), "processed_or_reused": len(records),
                      "ok": ok, "errors": len(errors),
                      "output": str(output_root)}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
