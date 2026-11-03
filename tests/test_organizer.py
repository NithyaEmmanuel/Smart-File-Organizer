import os
import unittest
from pathlib import Path
from unittest import mock

import organizer
from duplicate_detector import find_duplicates
from organizer import (
    ProtectedFolderError, StalePlanError, build_plan, execute_plan,
)
from scanner import ScanResult, scan_folder
from tests.helpers import TempFolderTestCase, can_symlink


class PlanTests(TempFolderTestCase):
    def test_empty_folder_gives_empty_plan(self):
        self.assertEqual(self.plan().actions, [])

    def test_preview_does_not_modify_anything(self):
        self.make_file("a.pdf")
        self.make_file("b.JPG")
        before = self.listing()
        dirs_before = sorted(p.name for p in self.root.iterdir())
        self.plan()
        self.assertEqual(before, self.listing())
        self.assertEqual(dirs_before, sorted(p.name for p in self.root.iterdir()))

    def test_destinations_by_category(self):
        self.make_file("a.pdf")
        self.make_file("b.JPG")
        self.make_file("README")
        dest = {a.name: a.destination for a in self.plan().actions}
        self.assertEqual(dest["a.pdf"], self.root / "Documents" / "a.pdf")
        self.assertEqual(dest["b.JPG"], self.root / "Images" / "b.JPG")
        self.assertEqual(dest["README"], self.root / "Others" / "README")

    def test_collision_gets_unique_name(self):
        self.make_file("a.txt", b"new")
        self.make_file("a.txt", b"old1", folder=self.root / "Documents")
        self.make_file("a (1).txt", b"old2", folder=self.root / "Documents")
        action = next(a for a in self.plan().actions if a.source.parent == self.root)
        self.assertEqual(action.destination.name, "a (2).txt")
        self.assertIn("renamed", action.note)

    def test_two_planned_files_never_share_a_destination(self):
        self.make_file("x.txt", b"1", folder=self.base / "p")
        self.make_file("x.txt", b"2", folder=self.base / "q")
        files = scan_folder(self.base / "p").files + scan_folder(self.base / "q").files
        plan = build_plan(ScanResult(folder=self.root, files=files), protected_paths=[])
        names = sorted(a.destination.name for a in plan.actions)
        self.assertEqual(names, ["x (1).txt", "x.txt"])

    def test_protected_file_skipped(self):
        keep = self.make_file("app.py")
        self.make_file("b.py")
        plan = build_plan(self.scan(), protected_paths=[organizer.path_key(keep.resolve())])
        actions = {a.name: a.action for a in plan.actions}
        self.assertEqual(actions, {"app.py": "Skip", "b.py": "Move"})

    def test_hidden_or_system_file_skipped(self):
        self.make_file("a.txt")
        scan = self.scan()
        scan.files[0].is_hidden_or_system = True
        plan = build_plan(scan, protected_paths=[])
        self.assertEqual(plan.actions[0].action, "Skip")

    def test_drive_root_is_rejected(self):
        with self.assertRaises(ProtectedFolderError):
            build_plan(ScanResult(folder=Path(self.root.anchor)), protected_paths=[])

    def test_file_blocking_category_folder_is_skipped(self):
        self.make_file("Documents", b"i am a file")
        self.make_file("a.pdf")
        actions = {a.name: a for a in self.plan().actions}
        self.assertEqual(actions["a.pdf"].action, "Skip")

    def test_duplicates_can_go_to_separate_folder(self):
        self.make_file("a.txt", b"same")
        self.make_file("b.txt", b"same")
        scan = self.scan()
        find_duplicates(scan.files)
        plan = build_plan(scan, separate_duplicates=True, protected_paths=[])
        folders = sorted(a.destination.parent.name for a in plan.actions)
        self.assertEqual(folders, ["Documents", "_Duplicates"])


class ExecuteTests(TempFolderTestCase):
    def test_moves_files_keeps_content_and_deletes_nothing(self):
        self.make_file("a.pdf", b"pdf-bytes")
        self.make_file("b.mp3", b"mp3-bytes")
        db = self.make_db()
        result = execute_plan(self.plan(), db)
        self.assertEqual(result.moved, 2)
        self.assertEqual(self.listing(), ["Audio/b.mp3", "Documents/a.pdf"])
        self.assertEqual((self.root / "Documents" / "a.pdf").read_bytes(), b"pdf-bytes")

    def test_category_folders_created_only_when_needed(self):
        self.make_file("a.pdf")
        execute_plan(self.plan(), self.make_db())
        self.assertEqual([p.name for p in self.root.iterdir()], ["Documents"])

    def test_existing_file_is_never_overwritten(self):
        self.make_file("a.txt", b"incoming")
        existing = self.make_file("a.txt", b"precious", folder=self.root / "Documents")
        execute_plan(self.plan(), self.make_db())
        self.assertEqual(existing.read_bytes(), b"precious")
        self.assertEqual((self.root / "Documents" / "a (1).txt").read_bytes(), b"incoming")

    def test_name_taken_after_preview_still_not_overwritten(self):
        self.make_file("a.txt", b"incoming")
        plan = self.plan()
        late = self.make_file("a.txt", b"late arrival", folder=self.root / "Documents")
        execute_plan(plan, self.make_db())
        self.assertEqual(late.read_bytes(), b"late arrival")
        self.assertEqual((self.root / "Documents" / "a (1).txt").read_bytes(), b"incoming")

    def test_modified_after_preview_requires_new_preview(self):
        path = self.make_file("a.txt", b"original")
        plan = self.plan()
        path.write_bytes(b"changed and longer")
        with self.assertRaises(StalePlanError) as ctx:
            execute_plan(plan, self.make_db())
        self.assertEqual(ctx.exception.changed[0][0], "a.txt")
        self.assertEqual(self.listing(), ["a.txt"])   # nothing moved

    def test_same_size_but_new_mtime_is_detected(self):
        path = self.make_file("a.txt", b"12345")
        plan = self.plan()
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        with self.assertRaises(StalePlanError):
            execute_plan(plan, self.make_db())

    def test_deleted_after_preview(self):
        path = self.make_file("a.txt")
        plan = self.plan()
        path.unlink()
        with self.assertRaises(StalePlanError):
            execute_plan(plan, self.make_db())

    def test_replaced_by_symlink_after_preview(self):
        if not can_symlink(self.base):
            self.skipTest("symlinks not available")
        path = self.make_file("a.txt", b"x")
        outside = self.make_file("outside.txt", b"x", folder=self.base)
        plan = self.plan()
        path.unlink()
        os.symlink(outside, path)
        with self.assertRaises(StalePlanError):
            execute_plan(plan, self.make_db())
        self.assertTrue(outside.exists())

    def test_partial_failure_continues_and_reports(self):
        for name in ("a.txt", "b.txt", "c.txt"):
            self.make_file(name, name.encode())
        real = organizer.safe_move_no_overwrite

        def flaky(src, dst):
            if str(src).endswith("b.txt"):
                raise PermissionError(13, "Permission denied")
            return real(src, dst)

        db = self.make_db()
        with mock.patch.object(organizer, "safe_move_no_overwrite", side_effect=flaky):
            result = execute_plan(self.plan(), db)
        self.assertEqual(result.moved, 2)
        self.assertEqual([n for n, _ in result.failed], ["b.txt"])
        self.assertIn("Permission denied", result.failed[0][1])
        self.assertEqual(self.listing(), ["Documents/a.txt", "Documents/c.txt", "b.txt"])
        statuses = sorted(r["status"] for r in db.get_recent_activity())
        self.assertEqual(statuses, ["Failed", "Success", "Success"])

    def test_unreadable_file_is_skipped_not_moved(self):
        self.make_file("a.txt")
        with mock.patch.object(organizer, "hash_file", side_effect=PermissionError(13, "denied")):
            result = execute_plan(self.plan(), self.make_db())
        self.assertEqual(result.moved, 0)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(self.listing(), ["a.txt"])

    def test_large_file_moves(self):
        data = b"z" * (12 * 1024 * 1024)
        self.make_file("big.zip", data)
        execute_plan(self.plan(), self.make_db())
        self.assertEqual((self.root / "Archives" / "big.zip").stat().st_size, len(data))

    def test_history_records_identity(self):
        self.make_file("a.txt", b"abc")
        db = self.make_db()
        execute_plan(self.plan(), db)
        row = db.get_recent_activity()[0]
        self.assertEqual(row["operation"], "Move")
        self.assertEqual(row["size_bytes"], 3)
        self.assertEqual(len(row["sha256"]), 64)

    def test_cancel_stops_cleanly(self):
        import threading
        self.make_file("a.txt")
        event = threading.Event()
        event.set()
        result = execute_plan(self.plan(), self.make_db(), cancel_event=event)
        self.assertTrue(result.cancelled)
        self.assertEqual(self.listing(), ["a.txt"])


if __name__ == "__main__":
    unittest.main()
