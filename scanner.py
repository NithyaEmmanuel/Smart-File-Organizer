"""Scans the top level of a folder and describes the regular files in it."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from categorizer import categorize_extension, get_extension

# A progress callback receives (items_done, items_total).
ProgressCallback = Callable[[int, int], None]

# Windows file attribute flags (only present in stat results on Windows).
_ATTR_HIDDEN = 0x2
_ATTR_SYSTEM = 0x4
_ATTR_REPARSE_POINT = 0x400


class FolderError(Exception):
    """Raised when the chosen folder is missing, not a folder, or unreadable."""


@dataclass
class FileInfo:
    """Everything we know about one file after scanning."""

    path: Path
    name: str
    extension: str              # lowercase, no dot; "" if none
    size_bytes: int
    modified_ns: int            # used later to detect files changed after preview
    category: str
    is_hidden_or_system: bool = False
    duplicate_status: str = "Not checked"
    duplicate_group: int | None = None
    is_primary_copy: bool = False


@dataclass
class ScanResult:
    """The outcome of one scan."""

    folder: Path
    files: list[FileInfo] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    cancelled: bool = False

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(file.size_bytes for file in self.files)


def format_size(size_bytes: int) -> str:
    """Convert bytes to a readable string: 2048 -> '2.0 KB'."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    size = float(size_bytes)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
    return f"{size_bytes} B"  # unreachable; keeps type checkers happy


def validate_folder(folder: str | Path) -> Path:
    """Check that the folder exists and is a directory; return its absolute path."""
    text = str(folder).strip().strip('"')   # tolerate pasted paths with quotes
    if not text:
        raise FolderError("No folder selected.")

    path = Path(text).expanduser()
    if not path.exists():
        raise FolderError(f"Folder does not exist: {path}")
    if not path.is_dir():
        raise FolderError(f"Not a folder: {path}")
    if not os.access(path, os.R_OK | os.X_OK):
        raise FolderError(f"Folder is not accessible: {path}")
    return path.resolve()


def scan_folder(
    folder: str | Path,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ScanResult:
    """Scan the top level of `folder` (no subfolders) and return a ScanResult.

    - Symbolic links are never followed; they are reported as skipped.
    - Files that cannot be read are reported as skipped instead of crashing.
    - This function never modifies anything on disk.
    """
    root = validate_folder(folder)

    try:
        with os.scandir(root) as iterator:
            entries = list(iterator)
    except PermissionError as exc:
        raise FolderError(f"Permission denied: {root}") from exc
    except OSError as exc:
        raise FolderError(f"Cannot read folder {root}: {exc}") from exc

    result = ScanResult(folder=root)
    total = len(entries)

    for index, entry in enumerate(entries, start=1):
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
            break

        _process_entry(entry, result)

        if progress_callback is not None:
            progress_callback(index, total)

    result.files.sort(key=lambda info: info.name.lower())
    return result


def _process_entry(entry: os.DirEntry[str], result: ScanResult) -> None:
    """Turn one directory entry into a FileInfo, or record why it was skipped."""
    try:
        if entry.is_symlink():
            result.skipped.append((entry.name, "Symbolic link (not followed)"))
            return
        if not entry.is_file(follow_symlinks=False):
            return  # subfolders and special entries are quietly ignored
        stat = entry.stat(follow_symlinks=False)
    except OSError as exc:
        result.skipped.append((entry.name, f"Cannot access: {exc.strerror or exc}"))
        return

    attributes = getattr(stat, "st_file_attributes", 0)
    if attributes & _ATTR_REPARSE_POINT:
        result.skipped.append((entry.name, "Reparse point / link (not followed)"))
        return

    extension = get_extension(entry.name)
    result.files.append(
        FileInfo(
            path=Path(entry.path),
            name=entry.name,
            extension=extension,
            size_bytes=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            category=categorize_extension(extension),
            is_hidden_or_system=bool(attributes & (_ATTR_HIDDEN | _ATTR_SYSTEM)),
        )
    )
