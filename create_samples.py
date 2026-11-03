"""Create safe sample files in ./sample_files for manual testing.

Usage:  python create_samples.py
"""

from __future__ import annotations

from pathlib import Path

# name -> (fill character, size in bytes). Same fill + size = identical content.
SAMPLES: dict[str, tuple[str, int]] = {
    "report.pdf": ("p", 2048),
    "Budget.XLSX": ("b", 5120),            # uppercase extension
    "notes_a.txt": ("a", 1024),            # same size as notes_b, different content
    "notes_b.txt": ("b", 1024),
    "photo.JPG": ("j", 3072),
    "photo_copy.jpg": ("j", 3072),         # duplicate of photo.JPG, different name
    "photo_backup.jpeg": ("j", 3072),      # a third copy
    "holiday.png": ("h", 4096),
    "clip.mp4": ("m", 10240),
    "song.mp3": ("s", 8192),
    "backup.zip": ("z", 6144),
    "setup.exe": ("e", 7168),
    "script.py": ("c", 512),
    "README": ("r", 100),                  # no extension
    "empty.txt": ("x", 0),                 # zero-byte file
    "mystery.xyz": ("q", 256),             # unknown extension
    "old_stuff/ignored.txt": ("i", 999),   # subfolder: never scanned
}


def create_samples(folder: Path) -> int:
    for relative, (fill, size) in SAMPLES.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((fill * size).encode("ascii"))
    return len(SAMPLES)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "sample_files"
    print(f"Created {create_samples(target)} sample files in {target}")
