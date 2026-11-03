"""Maps file extensions to categories (Documents, Images, ...)."""

from __future__ import annotations

from pathlib import Path

OTHERS_CATEGORY = "Others"

# Extensions are stored lowercase and without the leading dot.
CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "Documents": frozenset({"pdf", "docx", "doc", "txt", "xlsx", "csv", "pptx"}),
    "Images": frozenset({"jpg", "jpeg", "png", "gif", "webp"}),
    "Videos": frozenset({"mp4", "avi", "mkv", "mov"}),
    "Audio": frozenset({"mp3", "wav", "flac"}),
    "Archives": frozenset({"zip", "rar", "7z"}),
    "Applications": frozenset({"exe", "msi"}),
    "Code": frozenset({"py", "java", "html", "css", "js", "json"}),
}

# Display order used by reports and summaries: known categories, then Others.
CATEGORY_ORDER: list[str] = [*CATEGORY_EXTENSIONS, OTHERS_CATEGORY]

# Reverse lookup built once: {"pdf": "Documents", "jpg": "Images", ...}
# A dict lookup is O(1), so we avoid looping over every category per file.
_EXTENSION_TO_CATEGORY: dict[str, str] = {
    extension: category
    for category, extensions in CATEGORY_EXTENSIONS.items()
    for extension in extensions
}


def get_extension(file_name: str | Path) -> str:
    """Return the lowercase extension without the dot ('Photo.JPG' -> 'jpg').

    Files without an extension (like 'README' or '.gitignore') return ''.
    """
    return Path(file_name).suffix.lower().lstrip(".")


def categorize_extension(extension: str) -> str:
    """Return the category for an extension (case-insensitive, dot optional)."""
    return _EXTENSION_TO_CATEGORY.get(extension.lower().lstrip("."), OTHERS_CATEGORY)


def categorize_file(file_name: str | Path) -> str:
    """Return the category for a file name or path."""
    return categorize_extension(get_extension(file_name))
