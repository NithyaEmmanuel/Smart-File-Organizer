"""Small, safety-focused file helpers shared by several modules.

Nothing in here ever deletes or overwrites a user's file.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

CHUNK_SIZE = 1024 * 1024                    # read files 1 MB at a time
IDENTITY_HASH_LIMIT = 256 * 1024 * 1024     # hash files up to 256 MB for undo checks


def hash_file(path: str | Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the SHA-256 hex digest of a file, reading it in chunks.

    Reading in chunks keeps memory use tiny even for multi-gigabyte files.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def path_key(path: str | Path) -> str:
    """Case-insensitive key for comparing paths (Windows paths ignore case)."""
    return str(path).lower()


def unique_destination(
    directory: Path, file_name: str, taken: set[str] | None = None
) -> Path:
    """Return a path in `directory` that does not exist yet.

    'report.pdf' becomes 'report (1).pdf', 'report (2).pdf', ...
    `taken` holds path keys already promised to other files in the same plan.
    """
    taken = taken or set()
    candidate = directory / file_name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 0
    while os.path.lexists(candidate) or path_key(candidate) in taken:
        counter += 1
        candidate = directory / f"{stem} ({counter}){suffix}"
    return candidate


def safe_move_no_overwrite(source: str | Path, destination: str | Path) -> None:
    """Move a file but guarantee an existing file is never overwritten.

    Trick: first create the destination with O_EXCL ("create, but fail if it
    already exists"). That atomically claims the name. Only then do we move the
    source onto our own empty placeholder. Raises FileExistsError if the name
    was already taken.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    descriptor = os.open(destination, flags)
    os.close(descriptor)
    try:
        os.replace(source, destination)
    except OSError:
        try:
            os.remove(destination)  # removes only the empty placeholder we made
        except OSError:
            pass
        raise
