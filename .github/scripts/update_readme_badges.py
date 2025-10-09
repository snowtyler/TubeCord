"""Update README badges for version and Python support."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote
import re


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"Required file not found: {path}") from exc


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def encode_badge_value(value: str) -> str:
    """URL-encode a badge value for shields.io."""
    return quote(value, safe="._")


def update_badge(content: str, pattern: re.Pattern[str], replacement: str, label: str) -> str:
    if not pattern.search(content):
        raise SystemExit(f"Could not find existing {label} badge in README.md")
    return pattern.sub(replacement, content, count=1)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    readme_path = repo_root / "README.md"
    version_path = repo_root / "VERSION"
    python_support_path = repo_root / "PYTHON_SUPPORT"

    readme_content = read_text(readme_path)
    version = read_text(version_path).strip()
    python_support = read_text(python_support_path).strip()

    version_badge_value = encode_badge_value(version)
    python_badge_value = encode_badge_value(python_support)

    version_badge = (
        f"[![Version](https://img.shields.io/badge/version-{version_badge_value}-blue.svg)](./VERSION)"
    )
    python_badge = (
        "[![Python](https://img.shields.io/badge/"
        f"python-{python_badge_value}-blue.svg)](https://www.python.org/)"
    )

    version_pattern = re.compile(
        r"\[!\[Version\]\(https://img\.shields\.io/badge/version-[^/]+-blue\.svg\)\]\(\./VERSION\)"
    )
    python_pattern = re.compile(
        r"\[!\[Python\]\(https://img\.shields\.io/badge/python-[^/]+-blue\.svg\)\]\(https://www\.python\.org/\)"
    )

    updated = readme_content
    updated = update_badge(updated, version_pattern, version_badge, "version")
    updated = update_badge(updated, python_pattern, python_badge, "python")

    if updated != readme_content:
        write_text(readme_path, updated)
        print("README.md badges updated")
    else:
        print("README.md badges already up to date")

    return 0


if __name__ == "__main__":
    sys.exit(main())
