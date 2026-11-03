import hashlib
import unittest
from unittest import mock

import duplicate_detector
from duplicate_detector import find_duplicates
from file_ops import hash_file
from scanner import scan_folder
from tests.helpers import TempFolderTestCase


class DuplicateTests(TempFolderTestCase):
    def test_same_content_different_names(self):
        self.make_file("a.txt", b"hello world")
        self.make_file("copy_of_a.dat", b"hello world")
        self.make_file("other.txt", b"something else")
        files = self.scan().files
        report = find_duplicates(files)
        self.assertEqual(len(report.groups), 1)
        self.assertEqual({f.name for f in report.groups[0].files}, {"a.txt", "copy_of_a.dat"})
        status = {f.name: f.duplicate_status for f in files}
        self.assertEqual(status["other.txt"], "Unique")
        self.assertTrue(status["a.txt"].endswith("(Group 1)"))

    def test_same_name_different_content_is_not_duplicate(self):
        folder_a, folder_b = self.root / "a", self.root / "b"
        self.make_file("same.txt", b"AAAA", folder=folder_a)
        self.make_file("same.txt", b"BBBB", folder=folder_b)
        files = scan_folder(folder_a).files + scan_folder(folder_b).files
        self.assertEqual(find_duplicates(files).groups, [])

    def test_same_size_different_content_not_duplicate(self):
        self.make_file("x.bin", b"1234")
        self.make_file("y.bin", b"5678")
        report = find_duplicates(self.scan().files)
        self.assertEqual(report.groups, [])
        self.assertEqual(report.files_hashed, 2)

    def test_unique_sizes_are_never_hashed(self):
        self.make_file("a", b"1")
        self.make_file("b", b"22")
        self.make_file("c", b"333")
        with mock.patch.object(duplicate_detector, "hash_file") as hasher:
            find_duplicates(self.scan().files)
        hasher.assert_not_called()

    def test_empty_files_not_grouped(self):
        self.make_file("e1.txt", b"")
        self.make_file("e2.txt", b"")
        files = self.scan().files
        self.assertEqual(find_duplicates(files).groups, [])
        self.assertEqual(files[0].duplicate_status, "Empty file")

    def test_large_files_hashed_in_chunks(self):
        data = (b"0123456789abcdef" * 1024) * 512      # 8 MB
        a = self.make_file("big1.bin", data)
        self.make_file("big2.bin", data)
        self.assertEqual(hash_file(a, chunk_size=64 * 1024), hashlib.sha256(data).hexdigest())
        report = find_duplicates(self.scan().files, chunk_size=64 * 1024)
        self.assertEqual(len(report.groups), 1)
        self.assertEqual(report.groups[0].wasted_bytes, len(data))

    def test_unreadable_file_reported(self):
        self.make_file("a.txt", b"same")
        self.make_file("b.txt", b"same")
        real = duplicate_detector.hash_file

        def fake(path, chunk_size=0):
            if str(path).endswith("b.txt"):
                raise PermissionError(13, "Permission denied")
            return real(path)

        files = self.scan().files
        with mock.patch.object(duplicate_detector, "hash_file", side_effect=fake):
            report = find_duplicates(files)
        self.assertEqual(report.groups, [])
        self.assertEqual(report.errors[0][0], "b.txt")
        self.assertEqual({f.name: f.duplicate_status for f in files}["b.txt"], "Unreadable")

    def test_files_are_never_deleted(self):
        self.make_file("a.txt", b"same")
        self.make_file("b.txt", b"same")
        before = self.listing()
        find_duplicates(self.scan().files)
        self.assertEqual(before, self.listing())


if __name__ == "__main__":
    unittest.main()
