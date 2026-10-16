"""Scan the repository text (code, docs, site) for long dashes, placeholders and leftover markers.

Exit status is non-zero when a problem is found, so the check can run in CI.
Vendored libraries, raw downloads, the virtual environment and binary files are skipped.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".probe",
    "vendor",
    "raw",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "screenshots",
}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".html", ".css", ".js", ".json", ".yml", ".yaml", ".toml", ".csv", ".sh", ".cfg"}
PATTERNS = {
    "em dash": re.compile(chr(0x2014)),
    "en dash": re.compile(chr(0x2013)),
    "placeholder": re.compile(r"(?i)\b(lorem ipsum|placeholder|TODO|FIXME|XXX|TBD|dummy data|fake data)\b"),
}
ALLOW = {"scripts/check_text.py"}


def main() -> int:
    """Walk the tree and report matches."""
    problems = 0
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = str(path.relative_to(ROOT))
        if rel in ALLOW:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pat in PATTERNS.items():
                if pat.search(line):
                    problems += 1
                    print(f"{rel}:{lineno}: {label}: {line.strip()[:120]}")
    print(f"{problems} problem(s) found")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
