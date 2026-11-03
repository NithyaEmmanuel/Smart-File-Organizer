# Smart File Organizer & Duplicate Detector

A Windows desktop app (Python, Tkinter, SQLite) that scans a folder, sorts files into category folders, finds duplicates by content (SHA-256), and lets you undo changes. Works offline, standard library only.

## Features
- Auto-categorize files (Documents, Images, Videos, Audio, Archives, Applications, Code, Others)
- Duplicate detection by content, never deletes anything
- Preview before any file is moved
- Safe moves: no overwriting, name conflicts handled
- Undo last operation, with activity history saved in SQLite

## Run
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python main.py
```

## Limitations
- Only the top-level folder is scanned (no subfolders).
- Empty files are not treated as duplicates of each other.
- Undo reverses one organize operation at a time.
- Undo checks file contents by hash only for files up to 256 MB; larger files are checked by size and modified time.
- Windows only.
