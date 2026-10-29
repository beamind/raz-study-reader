"""Tests for manifest-driven, one-to-one ASR output."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


def load():
    path = Path(__file__).resolve().parents[1] / "tools" / "transcribe_pages.py"
    spec = importlib.util.spec_from_file_location("transcribe_pages_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


asr = load()


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "split"
        self.output = self.root / "asr"
        self.book = self.source / "books" / "002 About Trees"
        self.book.mkdir(parents=True)
        manifest = {
            "book_index": 2, "title": "About Trees", "sequence_is_not_paper_page": True,
            "files": [
                {"file": "00-whole-book-oid-8136.mp3", "role": "whole_book",
                 "sequence": None, "slot": 8, "candidate_oid": 8136,
                 "seconds": 300.0, "paper_page": None},
                {"file": "page-01-oid-8137.mp3", "role": "page", "sequence": 1,
                 "slot": 9, "candidate_oid": 8137, "seconds": 3.37,
                 "paper_page": None},
                {"file": "page-02-oid-8140.mp3", "role": "page", "sequence": 2,
                 "slot": 12, "candidate_oid": 8140, "seconds": 32.2,
                 "paper_page": None},
            ],
        }
        (self.book / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for entry in manifest["files"]:
            (self.book / entry["file"]).write_bytes(b"audio-" + entry["file"].encode())
        self.model = self.root / "model.bin"
        self.model.write_bytes(b"model")
        self.whisper = self.root / "whisper-cli.exe"
        self.whisper.write_bytes(b"exe")
        self.config = asr.configuration(self.model, asr.sha256(self.model), "test-version",
                                        "en", None, 4)

    def test_discovery_uses_manifest_and_defaults_to_page_role(self):
        jobs = asr.discover_jobs(self.source, self.output, {"page"}, [])
        self.assertEqual([job["candidate_oid"] for job in jobs], [8137, 8140])
        self.assertEqual([job["sequence"] for job in jobs], [1, 2])
        self.assertEqual(jobs[0]["source_audio"],
                         "books/002 About Trees/page-01-oid-8137.mp3")
        self.assertEqual(jobs[0]["text_path"].name, "page-01-oid-8137.txt")
        self.assertEqual(jobs[0]["sidecar_path"].name, "page-01-oid-8137.asr.json")

    def test_book_glob_and_role_filters_are_explicit(self):
        none = asr.discover_jobs(self.source, self.output, {"page"}, ["Missing*"])
        whole = asr.discover_jobs(self.source, self.output, {"whole_book"}, ["about*"])
        self.assertEqual(none, [])
        self.assertEqual(len(whole), 1)
        self.assertEqual(whole[0]["role"], "whole_book")

    def fake_run(self, command, **kwargs):
        if "ffmpeg" in str(command[0]).lower():
            Path(command[-1]).write_bytes(b"RIFF" + b"0" * 100)
        else:
            wavs = [Path(value) for value in command if str(value).endswith(".wav")]
            for number, wav in enumerate(wavs, 1):
                Path(str(wav) + ".txt").write_text(f"  A tree {number}. \n", encoding="utf-8")
                raw = {"transcription": [{
                    "timestamps": {"from": "00:00:00,000", "to": "00:00:01,000"},
                    "offsets": {"from": 0, "to": 1000}, "text": f" A tree {number}.",
                    "tokens": [{"p": 0.9}, {"p": 0.7}],
                }]}
                Path(str(wav) + ".json").write_text(json.dumps(raw), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_batch_writes_same_stem_text_and_traceable_sidecars(self):
        jobs = asr.discover_jobs(self.source, self.output, {"page"}, [])
        jobs_with_hashes = [(job, asr.sha256(job["audio"])) for job in jobs]
        with mock.patch.object(asr.subprocess, "run", side_effect=self.fake_run):
            records = asr.process_batch(jobs_with_hashes, self.whisper, self.model, "ffmpeg",
                                        self.config, 2)
        self.assertEqual([record["status"] for record in records], ["ok", "ok"])
        sidecar = json.loads(jobs[0]["sidecar_path"].read_text(encoding="utf-8"))
        self.assertEqual(jobs[0]["text_path"].read_text(encoding="utf-8"), "A tree 1.\n")
        self.assertEqual(sidecar["location"]["candidate_oid"], 8137)
        self.assertEqual(sidecar["location"]["slot"], 9)
        self.assertIsNone(sidecar["location"]["paper_page"])
        self.assertEqual(sidecar["segments"][0]["mean_token_probability"], 0.8)
        self.assertEqual(sidecar["source_audio_sha256"], asr.sha256(jobs[0]["audio"]))
        self.assertEqual(sidecar["text_sha256"], asr.sha256(jobs[0]["text_path"]))

    def test_resume_requires_audio_and_model_configuration_to_match(self):
        job = asr.discover_jobs(self.source, self.output, {"page"}, ["002*"])[0]
        audio_hash = asr.sha256(job["audio"])
        record = asr.result_record(job, audio_hash, self.config, "A tree.", {})
        asr.save_text(job["text_path"], "A tree.")
        asr.save_json(job["sidecar_path"], record)
        self.assertIsNotNone(asr.current_sidecar(job, audio_hash, self.config))
        changed = {**self.config, "model_sha256": "different"}
        self.assertIsNone(asr.current_sidecar(job, audio_hash, changed))
        self.assertIsNone(asr.current_sidecar(job, "different-audio", self.config))
        asr.save_text(job["text_path"], "manually changed")
        self.assertIsNone(asr.current_sidecar(job, audio_hash, self.config))

    def test_word_timing_is_explicit_and_does_not_reuse_coarse_transcripts(self):
        job = asr.discover_jobs(self.source, self.output, {"page"}, ["002*"])[0]
        audio_hash = asr.sha256(job["audio"])
        asr.save_text(job["text_path"], "A tree.")
        asr.save_json(job["sidecar_path"], asr.result_record(job, audio_hash, self.config, "A tree.", {}))
        precise = asr.configuration(self.model, asr.sha256(self.model), "test-version", "en", None, 4,
                                    word_timestamps=True)
        self.assertIsNone(asr.current_sidecar(job, audio_hash, precise))
        with mock.patch.object(asr.subprocess, "run", side_effect=self.fake_run) as run:
            records = asr.process_batch([(job, audio_hash)], self.whisper, self.model, "ffmpeg", precise, 1)
        command = next(call.args[0] for call in run.call_args_list if call.args[0][0] == str(self.whisper))
        self.assertEqual(command[command.index("-ml") + 1], "1")
        self.assertIn("-sow", command)
        self.assertTrue(records[0]["asr"]["word_timestamps"])
        self.assertIsNotNone(asr.current_sidecar(job, audio_hash, precise))

    def test_missing_whisper_output_is_an_error_even_with_zero_exit_code(self):
        job = asr.discover_jobs(self.source, self.output, {"page"}, ["002*"])[0]

        def missing_output(command, **kwargs):
            if "ffmpeg" in str(command[0]).lower():
                Path(command[-1]).write_bytes(b"RIFF" + b"0" * 100)
            return subprocess.CompletedProcess(command, 0, "", "decoder failed")

        with mock.patch.object(asr.subprocess, "run", side_effect=missing_output):
            records = asr.process_batch([(job, asr.sha256(job["audio"]))], self.whisper,
                                        self.model, "ffmpeg", self.config, 1)
        self.assertEqual(records[0]["status"], "error")
        self.assertIn("no complete output", records[0]["error"])


if __name__ == "__main__":
    unittest.main()
