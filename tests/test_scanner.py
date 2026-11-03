import threading
import unittest
from unittest import mock

import scanner
from scanner import FolderError, format_size, scan_folder, validate_folder
from tests.helpers import TempFolderTestCase, can_symlink
import os


class _FakeEntry:
    name = "locked.txt"
    path = "locked.txt"

    def is_symlink(self):
        return False

    def is_file(self, follow_symlinks=True):
        return True

    def stat(self, follow_symlinks=True):
        raise PermissionError(13, "Permission denied")


class _FakeScandir:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *args):
        return False


class ScannerTests(TempFolderTestCase):
    def test_empty_folder(self):
        result = scan_folder(self.root)
        self.assertEqual(result.total_files, 0)
        self.assertEqual(result.total_size, 0)

    def test_files_without_extension_and_uppercase(self):
        self.make_file("README", b"abc")
        self.make_file("Photo.JPG", b"abcd")
        result = scan_folder(self.root)
        by_name = {f.name: f for f in result.files}
        self.assertEqual(by_name["README"].category, "Others")
        self.assertEqual(by_name["README"].extension, "")
        self.assertEqual(by_name["Photo.JPG"].category, "Images")
        self.assertEqual(result.total_size, 7)

    def test_subfolders_are_not_scanned(self):
        self.make_file("top.txt")
        self.make_file("inner.txt", folder=self.root / "sub")
        self.assertEqual([f.name for f in scan_folder(self.root).files], ["top.txt"])

    def test_missing_folder_and_file_as_folder(self):
        with self.assertRaises(FolderError):
            validate_folder(self.root / "nope")
        f = self.make_file("a.txt")
        with self.assertRaises(FolderError):
            validate_folder(f)
        with self.assertRaises(FolderError):
            validate_folder("   ")

    def test_symlink_is_skipped(self):
        if not can_symlink(self.base):
            self.skipTest("symlinks not available")
        target = self.make_file("real.txt")
        os.symlink(target, self.root / "link.txt")
        result = scan_folder(self.root)
        self.assertEqual([f.name for f in result.files], ["real.txt"])
        self.assertEqual(result.skipped[0][0], "link.txt")

    def test_unreadable_file_is_reported_not_fatal(self):
        with mock.patch.object(scanner.os, "scandir", return_value=_FakeScandir([_FakeEntry()])):
            result = scan_folder(self.root)
        self.assertEqual(result.total_files, 0)
        self.assertEqual(result.skipped[0][0], "locked.txt")

    def test_unreadable_folder_raises_folder_error(self):
        with mock.patch.object(scanner.os, "scandir", side_effect=PermissionError("denied")):
            with self.assertRaises(FolderError):
                scan_folder(self.root)

    def test_progress_and_cancel(self):
        for i in range(5):
            self.make_file(f"f{i}.txt")
        calls = []
        scan_folder(self.root, progress_callback=lambda d, t: calls.append((d, t)))
        self.assertEqual(calls[-1], (5, 5))
        event = threading.Event()
        event.set()
        self.assertTrue(scan_folder(self.root, cancel_event=event).cancelled)

    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(2048), "2.0 KB")
        self.assertEqual(format_size(5 * 1024 ** 2), "5.0 MB")


if __name__ == "__main__":
    unittest.main()
