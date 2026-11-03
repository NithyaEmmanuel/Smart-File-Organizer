# Smart File Organizer & Duplicate Detector

A Windows desktop app (Python 3.11 + Tkinter) that scans a folder, categorizes files,
finds duplicates by **content (SHA-256)**, shows a **preview**, safely organizes files into
category folders, and can **undo** the operation. Works fully offline; standard library only.

## Features
- Scan top-level files: name, extension, size, category, duplicate status, totals, progress bar
- Background threads keep the window responsive
- Categories: Documents, Images, Videos, Audio, Archives, Applications, Code, Others
- Duplicate detection: size pre-filter, chunked SHA-256, review groups, **never deletes**
- Preview (dry run) shows original name, location, destination, category, duplicate status, action
- Safe organize: confirmation, never overwrites (unique names like `report (1).pdf`), skips
  unreadable/hidden/system/protected files, ignores symlinks, detects files changed since the
  preview, creates category folders only when needed
- Undo: restores files only if the destination file is unchanged (size, mtime, SHA-256) and the
  original path is free; reports partial failures; retry is possible
- SQLite activity history (`%LOCALAPPDATA%\SmartFileOrganizer\history.db`)

## Project structure
| File | Purpose |
|---|---|
| `main.py` | Entry point |
| `gui.py` | Tkinter interface (no file logic) |
| `scanner.py` | Folder scanning, `FileInfo`, `ScanResult` |
| `categorizer.py` | Extension -> category |
| `duplicate_detector.py` | Size grouping + SHA-256 |
| `organizer.py` | Preview plan + safe execution |
| `history.py` | SQLite history + undo |
| `file_ops.py` | Hashing, unique names, no-overwrite move |
| `create_samples.py` | Generates safe test files in `sample_files/` |
| `tests/` | 56 automated tests (unittest, pytest-compatible) |

Data flow: `gui` -> `scanner` -> `duplicate_detector` -> `organizer.build_plan` (preview)
-> `organizer.execute_plan` (writes to `history`) -> `history.undo_last_operation`.

## Setup (PowerShell)
```powershell
cd SmartFileOrganizer
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python create_samples.py
python main.py
```
If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

## Run tests
```powershell
python -m unittest discover -s tests -t . -v
python -m pytest -v
```

## Build the Windows executable
```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name SmartFileOrganizer main.py
```
(or `.\build_exe.ps1`). Output: `dist\SmartFileOrganizer.exe`.

## Publish on GitHub Releases
```powershell
git init; git add .; git commit -m "Smart File Organizer v1.0.0"
git branch -M main
git remote add origin https://github.com/<you>/SmartFileOrganizer.git
git push -u origin main
git tag v1.0.0; git push origin v1.0.0
gh release create v1.0.0 dist\SmartFileOrganizer.exe --title "v1.0.0" --notes "First release"
```
Without the GitHub CLI: repo -> Releases -> Draft a new release -> choose tag `v1.0.0` ->
drag in `SmartFileOrganizer.exe` -> Publish. Include the SHA-256 from `Get-FileHash`.
Unsigned executables may trigger Windows SmartScreen ("More info" -> "Run anyway").

## Safety design
Files are only ever *moved*. A move first claims the destination name atomically (`O_EXCL`),
so an existing file can never be overwritten, even if it appears after the preview. Undo
re-verifies every file before restoring it.

## Known limitations
- Only the top-level folder is scanned (by design for v1).
- Empty files are not treated as duplicates of each other.
- Undo walks back one organize operation at a time.
- Hash-based undo verification is skipped for files over 256 MB (size + mtime still checked).
