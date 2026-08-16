"""Shared CLI and library for openelections-data-pa parsers."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSERS_DIR = REPO_ROOT / "parsers"
