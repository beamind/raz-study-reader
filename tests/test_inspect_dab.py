"""Small synthetic regressions; never open the user's original DAB file."""

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "inspect_dab.py"
SPEC = importlib.util.spec_from_file_location("inspect_dab_under_test", MODULE_PATH)
dab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dab)


def tiny_package(footer_rule="full", auxiliary=False):
    """Two guides and two content clips, with intentional holes in both tables."""
    content_slots = 5
    payload_start = dab.INDEX_START + content_slots * 8
    header = bytearray(dab.INDEX_START)
    header[:4] = b"deb3"
    header[0x154] = 2
    struct.pack_into("<I", header, 0x17B, 1000)
    struct.pack_into("<H", header, 0x1AF, 4)
    header[0x400:] = b"\xff" * 64
    table = bytearray(b"\xff" * (content_slots * 8))
    payload = bytearray()
    for kind, slots in (("guide", (0, 2)), ("content", (0, 3))):
        for slot in slots:
            target = header if kind == "guide" else table
            entry_offset = 0x400 + slot * 8 if kind == "guide" else slot * 8
            struct.pack_into("<II", target, entry_offset, payload_start + len(payload), 3)
            payload.extend(b"\xff\xfb\x90\xc4")
    if auxiliary:
        struct.pack_into("<II", header, 0x17F, payload_start + len(payload), 3)
        payload.extend(b"\xff\xfb\x90\xc4")
    footer = b""
    if footer_rule is not None:
        bitmap = bytearray(8192)
        codes = {
            "full": (1000, 1002, 1008, 1011),
            "upstream": (1000, 1002, 1003),
            "mismatch": (1000, 1002, 1008, 1012),
        }[footer_rule]
        for code in codes:
            bitmap[code // 8] |= 0x80 >> (code % 8)
        footer = b"BOID" + bitmap + b"EOID"
    return bytes(header + table + payload + footer)


class PackageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fixture.dab"

    def parse(self, package):
        self.path.write_bytes(package)
        return dab.parse_package(self.path)

    def test_sparse_slots_keep_original_positions_and_full_index_codes(self):
        metadata, rows = self.parse(tiny_package())
        content = [row for row in rows if row["kind"] == "content"]
        self.assertEqual([row["slot"] for row in content], [0, 1, 2, 3, 4])
        self.assertEqual([row["occupied"] for row in content], [True, False, False, True, False])
        self.assertEqual([(row["slot"], row["full_index_slot"], row["candidate_oid"])
                          for row in content if row["occupied"]], [(0, 8, 1008), (3, 11, 1011)])
        self.assertEqual(metadata["candidate_code_rule"], "code_with_guide_slots")
        self.assertTrue(metadata["footer_bitmap"]["full_index_formula_matches_exactly"])
        self.assertFalse(metadata["footer_bitmap"]["upstream_body_formula_matches_exactly"])
        self.assertEqual(metadata["footer_bitmap"]["set_codes"], [1000, 1002, 1008, 1011])
        self.assertEqual(metadata["payload_gaps"], [])
        self.assertEqual(metadata["payload_overlaps"], [])
        self.assertTrue(metadata["declared_count_matches_active"])

    def test_observed_labelled_header_keeps_the_version_two_index_and_title(self):
        package = bytearray(tiny_package())
        package[:200] = b"789-ha-2024Q" + b"\xff" * (200 - len(b"789-ha-2024Q"))
        metadata, rows = self.parse(package)
        self.assertEqual(metadata["title"], "789-ha-2024Q")
        self.assertEqual(metadata["format_version"], 2)
        self.assertTrue(metadata["declared_count_matches_active"])
        self.assertEqual([row["candidate_oid"] for row in rows if row["kind"] == "content" and row["occupied"]], [1008, 1011])

    def test_unknown_header_and_labelled_new_version_are_rejected(self):
        for label, version in [(b"789-unknown", 2), (b"789-ha-2024Q", 3), (b"789-ha-2024Qextra", 2)]:
            with self.subTest(label=label, version=version):
                package = bytearray(tiny_package())
                package[:200] = label + b"\xff" * (200 - len(label))
                package[0x154] = version
                with self.assertRaises(ValueError):
                    self.parse(package)

    def test_footer_evidence_can_select_upstream_formula(self):
        metadata, rows = self.parse(tiny_package("upstream"))
        self.assertEqual(metadata["candidate_code_rule"], "code_without_guide_slots")
        self.assertFalse(metadata["footer_bitmap"]["full_index_formula_matches_exactly"])
        self.assertTrue(metadata["footer_bitmap"]["upstream_body_formula_matches_exactly"])
        content = [row for row in rows if row["kind"] == "content" and row["occupied"]]
        self.assertEqual([row["candidate_oid"] for row in content], [1000, 1003])

    def test_missing_or_mismatched_footer_does_not_confirm_codes(self):
        for rule in (None, "mismatch"):
            with self.subTest(footer=rule):
                metadata, rows = self.parse(tiny_package(rule))
                self.assertIsNone(metadata["candidate_code_rule"])
                self.assertTrue(all(row["candidate_oid"] is None for row in rows))

    def test_auxiliary_audio_is_bounded_and_excluded_from_footer_index(self):
        metadata, rows = self.parse(tiny_package(auxiliary=True))
        auxiliary = [row for row in rows if row["kind"] == "auxiliary_candidate"]
        self.assertEqual(len(auxiliary), 1)
        self.assertEqual(auxiliary[0]["stored_length"], 4)
        self.assertIsNone(auxiliary[0]["candidate_oid"])
        self.assertEqual(metadata["trailer"]["length"], 8200)
        self.assertEqual(metadata["footer_bitmap"]["set_bit_count"], 4)
        self.assertTrue(metadata["footer_bitmap"]["full_index_formula_matches_exactly"])

    def test_half_empty_table_entries_are_rejected(self):
        for entry in ((dab.EMPTY, 3), (dab.INDEX_START + 40, dab.EMPTY)):
            with self.subTest(entry=entry):
                package = bytearray(tiny_package())
                struct.pack_into("<II", package, dab.INDEX_START + 8, *entry)
                with self.assertRaisesRegex(ValueError, "Partial empty sentinel: content/1"):
                    self.parse(package)

    def test_invalid_index_boundaries_are_rejected(self):
        valid = tiny_package()
        boundaries = (dab.INDEX_START - 8, dab.INDEX_START + 1,
                      len(valid) + 8, dab.INDEX_START + dab.MAX_INDEX_BYTES + 8)
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                package = bytearray(valid)
                struct.pack_into("<I", package, 0x400, boundary)
                with self.assertRaisesRegex(ValueError, "index boundary"):
                    self.parse(package)

    def test_audio_bounds_allow_exact_eof_and_reject_overrun(self):
        exact = dab.parse_entry(struct.pack("<II", 20, 9), 3, "content", 30, 20, 1000)
        self.assertEqual(exact["stored_length"], 10)
        self.assertEqual(exact["end_exclusive"], 30)
        for offset, length_minus_one in ((19, 0), (20, 10), (30, 0)):
            with self.subTest(offset=offset, length_minus_one=length_minus_one):
                with self.assertRaisesRegex(ValueError, "Out-of-bounds audio entry"):
                    dab.parse_entry(struct.pack("<II", offset, length_minus_one),
                                    3, "content", 30, 20, 1000)


class AudioTests(unittest.TestCase):
    def test_observed_prefix_known_answers(self):
        # Literal bytes recorded by index.json, independent of the synthetic fixture encoder.
        prefixes = (
            "ed12447d7bedbfedbfedbfedbfedbfed",
            "817e28111781d381d381d381d381d381",
            "5da2f4cdcb5d0f5d0f5d0f5d0f5d0f5d",
            "936c3a030593c193c193c193c193c193",
            "7887d1e8ee782a782a782a782a782a78",
        )
        # One key byte is removed from each recorded 16-byte prefix.
        expected = bytes.fromhex("fffb90c4") + b"\0" * 11
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                raw = bytes.fromhex(prefix)
                mode = dab.classify_audio(raw)
                self.assertEqual(mode, "alternating_xor_52_candidate")
                self.assertEqual(dab.unwrap_audio(raw, mode), expected)
                self.assertEqual(dab.unwrap_audio(raw[:-1], mode), expected[:-1])

    def test_raw_mp3_and_unknown_headers(self):
        for raw in (b"ID3\x04", bytes.fromhex("fffb90c4")):
            with self.subTest(raw=raw):
                self.assertEqual(dab.classify_audio(raw), "raw_mp3_candidate")
                self.assertEqual(dab.unwrap_audio(raw, "raw_mp3_candidate"), raw)
        for raw in (b"", b"\xff", b"\0\0\0\0", bytes.fromhex("ffeb90c4")):
            with self.subTest(raw=raw):
                self.assertEqual(dab.classify_audio(raw), "unknown")
        with self.assertRaises(ValueError):
            dab.unwrap_audio(b"\x52", "alternating_xor_52_candidate")


if __name__ == "__main__":
    unittest.main()
