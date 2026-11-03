"""Application entry point: python main.py"""

from __future__ import annotations

import sys


def main() -> int:
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required (3.11 recommended).")
        return 1
    from gui import run   # imported here so a missing Tk gives a clear error

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
