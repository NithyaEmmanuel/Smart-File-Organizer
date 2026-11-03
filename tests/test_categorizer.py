import unittest

from categorizer import categorize_extension, categorize_file, get_extension


class CategorizerTests(unittest.TestCase):
    def test_known_extensions(self):
        self.assertEqual(categorize_file("a.pdf"), "Documents")
        self.assertEqual(categorize_file("a.webp"), "Images")
        self.assertEqual(categorize_file("a.mkv"), "Videos")
        self.assertEqual(categorize_file("a.flac"), "Audio")
        self.assertEqual(categorize_file("a.7z"), "Archives")
        self.assertEqual(categorize_file("a.msi"), "Applications")
        self.assertEqual(categorize_file("a.json"), "Code")

    def test_uppercase_and_mixed_case(self):
        self.assertEqual(categorize_file("PHOTO.JPG"), "Images")
        self.assertEqual(categorize_file("Report.PdF"), "Documents")
        self.assertEqual(get_extension("X.TXT"), "txt")

    def test_no_extension_and_dotfiles(self):
        self.assertEqual(categorize_file("README"), "Others")
        self.assertEqual(categorize_file(".gitignore"), "Others")

    def test_unknown_and_multi_dot(self):
        self.assertEqual(categorize_file("data.xyz"), "Others")
        self.assertEqual(categorize_file("archive.tar.gz"), "Others")
        self.assertEqual(categorize_file("my.report.final.docx"), "Documents")

    def test_extension_with_dot(self):
        self.assertEqual(categorize_extension(".PNG"), "Images")
