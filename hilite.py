#!/usr/bin/env python3
"""HiLite -- thin launcher.

The real entry point lives in ``hilite/cli.py`` so it is importable as the
``hilite`` console script (``hilite = "hilite.cli:main"``). This shim keeps
``python hilite.py`` / ``uv run hilite.py`` working.
"""

from hilite.cli import main

if __name__ == "__main__":
    main()
