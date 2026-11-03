import os
import unittest

from history import HistoryDB, undo_last_operation
from organizer import execute_plan
from tests.helpers import TempFolderTestCase


class UndoTests(TempFolderTestCase):
    def organize(self, db):
        return execute_plan(self.plan(), db)

    def test_nothing_to_undo(self):
        self.assertTrue(undo_last_operation(self.make_db()).nothing_to_undo)

    def test_undo_restores_everything(self):
        self.make_file("a.pdf", b"1")
        self.make_file("b.mp3", b"2")
        db = self.make_db()
        self.organize(db)
        result = undo_last_operation(db)
        self.assertEqual(result.restored, 2)
        self.assertEqual(result.failed, [])
        self.assertEqual(self.listing(), ["a.pdf", "b.mp3"])
        self.assertEqual([p.name for p in self.root.iterdir() if p.is_dir()], [])  # empty folders removed
        self.assertTrue(undo_last_operation(db).nothing_to_undo)

    def test_undo_when_original_path_is_occupied(self):
        self.make_file("a.txt", b"moved")
        self.make_file("b.txt", b"also moved")
        db = self.make_db()
        self.organize(db)
        squatter = self.make_file("a.txt", b"new file in the way")
        result = undo_last_operation(db)
        self.assertEqual(result.restored, 1)
        self.assertEqual([n for n, _ in result.failed], ["a.txt"])
        self.assertIn("occupied", result.failed[0][1])
        self.assertEqual(squatter.read_bytes(), b"new file in the way")      # not overwritten
        self.assertEqual((self.root / "Documents" / "a.txt").read_bytes(), b"moved")

    def test_failed_undo_can_be_retried(self):
        self.make_file("a.txt", b"x")
        db = self.make_db()
        self.organize(db)
        squatter = self.make_file("a.txt", b"blocker")
        self.assertEqual(undo_last_operation(db).restored, 0)
        squatter.unlink()
        self.assertEqual(undo_last_operation(db).restored, 1)
        self.assertEqual(self.listing(), ["a.txt"])

    def test_undo_when_destination_content_changed(self):
        original = self.make_file("a.txt", b"AAAA")
        st = original.stat()
        db = self.make_db()
        self.organize(db)
        moved = self.root / "Documents" / "a.txt"
        moved.write_bytes(b"BBBB")                       # same size, different content
        os.utime(moved, ns=(st.st_atime_ns, st.st_mtime_ns))   # even with same mtime
        result = undo_last_operation(db)
        self.assertEqual(result.restored, 0)
        self.assertIn("content differs", result.failed[0][1])
        self.assertEqual(moved.read_bytes(), b"BBBB")
        self.assertFalse((self.root / "a.txt").exists())

    def test_undo_when_destination_size_changed(self):
        self.make_file("a.txt", b"AAAA")
        db = self.make_db()
        self.organize(db)
        (self.root / "Documents" / "a.txt").write_bytes(b"much longer content")
        result = undo_last_operation(db)
        self.assertEqual(result.restored, 0)
        self.assertIn("modified", result.failed[0][1])

    def test_undo_when_destination_file_is_gone(self):
        self.make_file("a.txt", b"A")
        db = self.make_db()
        self.organize(db)
        (self.root / "Documents" / "a.txt").unlink()
        result = undo_last_operation(db)
        self.assertIn("no longer at its destination", result.failed[0][1])

    def test_only_last_operation_is_undone(self):
        self.make_file("first.txt", b"1")
        db = self.make_db()
        self.organize(db)
        self.make_file("second.pdf", b"2")
        self.organize(db)
        undo_last_operation(db)
        self.assertEqual(self.listing(), ["Documents/first.txt", "second.pdf"])

    def test_undo_restores_renamed_collision_file(self):
        self.make_file("a.txt", b"incoming")
        self.make_file("a.txt", b"existing", folder=self.root / "Documents")
        db = self.make_db()
        self.organize(db)
        undo_last_operation(db)
        self.assertEqual((self.root / "a.txt").read_bytes(), b"incoming")
        self.assertEqual((self.root / "Documents" / "a.txt").read_bytes(), b"existing")

    def test_history_persists_between_sessions(self):
        self.make_file("a.txt")
        db = HistoryDB(self.db_path)
        self.organize(db)
        db.close()
        reopened = self.make_db()
        rows = reopened.get_recent_activity()
        self.assertEqual(rows[0]["filename"], "a.txt")
        self.assertIsNotNone(reopened.get_last_undoable_operation())

    def test_undo_actions_are_logged(self):
        self.make_file("a.txt")
        db = self.make_db()
        self.organize(db)
        undo_last_operation(db)
        undo_rows = [r for r in db.get_recent_activity() if r["operation"] == "Undo"]
        self.assertEqual(undo_rows[0]["status"], "Success")


if __name__ == "__main__":
    unittest.main()
