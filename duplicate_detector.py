"""Content-based duplicate detection using SHA-256.

Strategy (fast to slow):
1. Group files by size. A file with a unique size cannot have a duplicate,
   so it is never hashed.
2. Hash only files that share a size, reading each in chunks.
3. Files with the same size AND the same hash are duplicates.

File names are never used to decide anything. Nothing is ever deleted.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from file_ops import CHUNK_SIZE, hash_file
from scanner import FileInfo, ProgressCallback


@dataclass
class DuplicateGroup:
    """A set of two or more files with byte-for-byte identical content."""

    group_id: int
    sha256: str
    size_bytes: int
    files: list[FileInfo]

    @property
    def wasted_bytes(self) -> int:
        """Space used by the extra copies (everything except one file)."""
        return self.size_bytes * (len(self.files) - 1)


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    files_hashed: int = 0
    cancelled: bool = False

    @property
    def wasted_bytes(self) -> int:
        return sum(group.wasted_bytes for group in self.groups)

    @property
    def duplicate_file_count(self) -> int:
        return sum(len(group.files) for group in self.groups)


def find_duplicates(
    files: list[FileInfo],
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> DuplicateReport:
    """Find duplicate groups and update each FileInfo's duplicate fields.

    The oldest file in each group (by modified time) is labelled "Original";
    the others are labelled "Duplicate". The label is only a suggestion.
    """
    report = DuplicateReport()

    by_size: dict[int, list[FileInfo]] = {}
    for info in files:
        info.duplicate_status = "Unique"
        info.duplicate_group = None
        info.is_primary_copy = False
        if info.size_bytes == 0:
            info.duplicate_status = "Empty file"   # empty files are not compared
            continue
        by_size.setdefault(info.size_bytes, []).append(info)

    candidates = [group for group in by_size.values() if len(group) > 1]
    total = sum(len(group) for group in candidates)
    done = 0
    by_hash: dict[tuple[int, str], list[FileInfo]] = {}

    for same_size_files in candidates:
        for info in same_size_files:
            if cancel_event is not None and cancel_event.is_set():
                report.cancelled = True
                for item in files:
                    item.duplicate_status = "Not checked"
                    item.duplicate_group = None
                    item.is_primary_copy = False
                return report
            try:
                digest = hash_file(info.path, chunk_size)
            except OSError as exc:
                info.duplicate_status = "Unreadable"
                report.errors.append((info.name, exc.strerror or str(exc)))
            else:
                by_hash.setdefault((info.size_bytes, digest), []).append(info)
                report.files_hashed += 1
            done += 1
            if progress_callback is not None:
                progress_callback(done, total)

    raw_groups = [
        (size, digest, members)
        for (size, digest), members in by_hash.items()
        if len(members) > 1
    ]
    raw_groups.sort(key=lambda g: (-g[0] * (len(g[2]) - 1), g[2][0].name.lower()))

    for group_id, (size, digest, members) in enumerate(raw_groups, start=1):
        members.sort(key=lambda f: (f.modified_ns, f.name.lower()))
        for position, info in enumerate(members):
            info.duplicate_group = group_id
            info.is_primary_copy = position == 0
            label = "Original" if position == 0 else "Duplicate"
            info.duplicate_status = f"{label} (Group {group_id})"
        report.groups.append(DuplicateGroup(group_id, digest, size, members))

    return report
