"""Small synthetic regressions; never open the user's original DAB file."""

import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest


def load(name):
    path = Path(__file__).resolve().parents[1] / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


split = load("split_dab_pages")
dab = load("inspect_dab")

FRAME = b"\xff\xfb\x90\xc4" + b"\0" * 413  # MPEG1 Layer3, 44100 Hz, 128 kbps
FRAME_SECONDS = 1152 / 44100


def mp3_clip(seconds):
    return FRAME * max(1, round(seconds / FRAME_SECONDS))


def wrap(payload, key=0x5D):
    """Inverse of the alternating-XOR wrapper; the transform is its own inverse."""
    body = bytearray(len(payload))
    body[0::2] = bytes(b ^ key for b in payload[0::2])
    body[1::2] = bytes(b ^ key ^ 0x52 for b in payload[1::2])
    return bytes([key]) + bytes(body)


def package(content_seconds, guide_seconds=(0.5,), content_start=1000):
    """One guide clip followed by content clips, wrapped the way real packages are."""
    slots = len(content_seconds)
    payload_start = dab.INDEX_START + slots * 8
    header = bytearray(dab.INDEX_START)
    header[:4] = b"deb3"
    header[0x154] = 2
    struct.pack_into("<I", header, 0x17B, content_start)
    struct.pack_into("<H", header, 0x1AF, slots + len(guide_seconds))
    header[0x400:] = b"\xff" * 64
    table = bytearray(b"\xff" * (slots * 8))
    payload = bytearray()

    def add(target, entry_offset, seconds):
        clip = wrap(mp3_clip(seconds))
        struct.pack_into("<II", target, entry_offset, payload_start + len(payload), len(clip) - 1)
        payload.extend(clip)

    # Guide slot 0 shares its offset field with payload_start, so it must come first.
    for slot, seconds in enumerate(guide_seconds):
        add(header, 0x400 + slot * 8, seconds)
    for slot, seconds in enumerate(content_seconds):
        add(table, slot * 8, seconds)
    bitmap = bytearray(8192)
    for code in ([content_start + slot for slot in range(len(guide_seconds))]
                 + [content_start + 8 + slot for slot in range(slots)]):
        bitmap[code // 8] |= 0x80 >> (code % 8)
    return bytes(header + table + payload + b"BOID" + bitmap + b"EOID")


# A leading page with no whole-book read, then two books; the second has a word list.
LAYOUT = (1.0, 6.0, 1.0, 2.0, 3.0, 7.0, 1.0, 2.0, 0.2, 0.2)
THRESHOLDS = {"book_threshold": 5.0, "word_threshold": 0.5, "tolerance": 8.0}


class NameTests(unittest.TestCase):
    def test_illegal_characters_and_reserved_names_fall_back(self):
        self.assertEqual(split.safe_name('A/B:C*D?', "fb"), "ABCD")
        self.assertEqual(split.safe_name("  spaced   out  ", "fb"), "spaced out")
        self.assertEqual(split.safe_name(" ..trailing.. ", "fb"), "trailing")
        for reserved in ("CON", "con", "LPT1", "NUL.mp3", "", "   ", "...", "///"):
            with self.subTest(name=reserved):
                self.assertEqual(split.safe_name(reserved, "fb"), "fb")

    def test_long_titles_are_truncated_without_trailing_separators(self):
        name = split.safe_name("x" * 200, "fb")
        self.assertEqual(len(name), 80)
        self.assertEqual(split.safe_name("y" * 79 + " . ", "fb"), "y" * 79)


class GroupingTests(unittest.TestCase):
    def entries(self, seconds):
        return [{"kind": "content", "slot": slot, "seconds": value, "candidate_oid": 1008 + slot}
                for slot, value in enumerate(seconds)]

    def test_long_reads_start_books_and_short_clips_become_words(self):
        books = split.group_books(self.entries(LAYOUT), 5.0, 0.5)
        self.assertEqual(len(books), 3)
        self.assertIsNone(books[0]["whole_book"])
        self.assertEqual([b["page_count"] for b in books], [1, 3, 2])
        self.assertEqual([b["word_count"] for b in books], [0, 0, 2])
        self.assertEqual([b["whole_book"]["slot"] if b["whole_book"] else None
                          for b in books], [None, 1, 5])
        self.assertEqual(books[1]["coverage_ratio"], 1.0)
        self.assertFalse(books[1]["coverage_warning"])
        self.assertTrue(books[2]["coverage_warning"])  # 3.4s of parts against a 7s read

    def test_entries_are_grouped_by_slot_order_not_file_order(self):
        shuffled = list(reversed(self.entries(LAYOUT)))
        self.assertEqual([b["page_count"] for b in split.group_books(shuffled, 5.0, 0.5)],
                         [1, 3, 2])

    def test_zero_length_entries_are_skipped(self):
        entries = self.entries(LAYOUT) + [{"kind": "content", "slot": 99, "seconds": None,
                                           "candidate_oid": None}]
        self.assertEqual(sum(b["page_count"] for b in split.group_books(entries, 5.0, 0.5)), 6)


class TitleTests(unittest.TestCase):
    def books(self):
        return split.group_books(
            [{"kind": "content", "slot": slot, "seconds": value, "candidate_oid": 1008 + slot}
             for slot, value in enumerate(LAYOUT)], 5.0, 0.5)

    def test_closest_reference_wins_and_is_used_only_once(self):
        books = self.books()
        split.match_titles(books, [{"name": "Alpha", "seconds": 6.1},
                                   {"name": "Beta", "seconds": 6.9}], 8.0)
        self.assertEqual([b.get("title") for b in books], [None, "Alpha", "Beta"])
        self.assertAlmostEqual(books[1]["title_delta_seconds"], 0.1, places=1)

    def test_tolerance_rejects_distant_matches(self):
        books = self.books()
        split.match_titles(books, [{"name": "Alpha", "seconds": 6.1}], 0.01)
        self.assertEqual([b.get("title") for b in books], [None, None, None])

    def test_books_without_a_whole_book_read_are_never_titled(self):
        books = self.books()
        split.match_titles(books, [{"name": "Alpha", "seconds": 1.0}], 8.0)
        self.assertIsNone(books[0].get("title"))


class SplitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source" / "fixture.dab"
        self.source.parent.mkdir()
        self.source.write_bytes(package(LAYOUT))
        self.output = self.root / "out"

    def run_split(self, reference=None, dry_run=False, **overrides):
        options = {**THRESHOLDS, **overrides}
        return split.split(self.source, self.output, reference, options["book_threshold"],
                           options["word_threshold"], options["tolerance"], dry_run)

    def test_end_to_end_layout_and_playable_clips(self):
        report = self.run_split()
        self.assertTrue(report["source_unchanged"])
        self.assertEqual((report["book_count"], report["page_files"], report["word_files"]),
                         (3, 6, 2))
        self.assertEqual((report["whole_book_files"], report["guide_files"]), (2, 1))
        folders = sorted(p.name for p in (self.output / "books").iterdir())
        self.assertEqual(folders, ["book-001-slot-0000-no-whole-book-read",
                                   "book-002-slot-0001", "book-003-slot-0005"])
        book = self.output / "books" / "book-003-slot-0005"
        self.assertEqual(sorted(p.name for p in book.glob("*.mp3")),
                         ["00-whole-book-oid-1013.mp3", "page-01-oid-1014.mp3",
                          "page-02-oid-1015.mp3", "word-01-oid-1016.mp3",
                          "word-02-oid-1017.mp3"])
        self.assertTrue((self.output / "guide" / "guide-00-oid-1000.mp3").exists())
        # Files are decoded MP3 frames, not the wrapped bytes stored in the package.
        self.assertTrue((book / "page-01-oid-1014.mp3").read_bytes().startswith(FRAME))
        self.assertEqual((book / "page-02-oid-1015.mp3").stat().st_size, len(mp3_clip(2.0)))

    def test_manifest_records_slots_codes_and_empty_paper_pages(self):
        self.run_split()
        manifest = json.loads((self.output / "books" / "book-003-slot-0005"
                               / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["page_count"], manifest["word_count"]), (2, 2))
        self.assertTrue(manifest["coverage_warning"])
        self.assertTrue(manifest["sequence_is_not_paper_page"])
        self.assertEqual([(f["role"], f["sequence"], f["slot"], f["candidate_oid"])
                          for f in manifest["files"]],
                         [("whole_book", None, 5, 1013), ("page", 1, 6, 1014),
                          ("page", 2, 7, 1015), ("word", 1, 8, 1016), ("word", 2, 9, 1017)])
        self.assertTrue(all(f["paper_page"] is None for f in manifest["files"]))

    def test_titles_rename_folders_and_are_recorded_as_derived(self):
        reference = self.root / "ref"
        reference.mkdir()
        (reference / "Beta's  Big Day.mp3").write_bytes(mp3_clip(7.0))
        report = self.run_split(reference=reference)
        self.assertEqual(report["titled_books"], 1)
        folder = self.output / "books" / "003 Beta's Big Day"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["title"], "Beta's  Big Day")
        self.assertEqual(manifest["title_source"], "reference_audio_duration_match")
        self.assertEqual(manifest["title_delta_seconds"], 0.0)

    def test_dry_run_reports_without_writing(self):
        report = self.run_split(dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["page_files"], 6)
        self.assertFalse(self.output.exists())

    def test_missing_whole_book_reads_are_an_error_not_one_giant_book(self):
        with self.assertRaisesRegex(ValueError, "book-threshold"):
            self.run_split(book_threshold=1000.0)

    def test_output_inside_the_source_tree_is_rejected(self):
        for output in (self.source.parent, self.source.parent / "nested", self.root):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "separate from the source"):
                    split.split(self.source, output, None, 5.0, 0.5, 8.0, False)

    def test_truncated_source_is_reported_instead_of_writing_short_files(self):
        self.source.write_bytes(package(LAYOUT)[:-9000])
        with self.assertRaises(ValueError):
            self.run_split()


if __name__ == "__main__":
    unittest.main()
