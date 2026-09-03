"""Executable entry point for python -m audit."""

from __future__ import annotations

import sys

from audit.cli import main

if __name__ == "__main__":
    sys.exit(main())
