#!/usr/bin/env python3
"""
Version bump utility for TubeCord.
Usage: python bump_version.py [major|minor|patch]
"""

import sys
import re
from pathlib import Path


def read_version(version_file: Path) -> tuple[int, int, int]:
    """Read current version from VERSION file."""
    version_text = version_file.read_text().strip()
    parts = version_text.split('.')
    return int(parts[0]), int(parts[1]), int(parts[2])


def write_version(version_file: Path, major: int, minor: int, patch: int):
    """Write new version to VERSION file."""
    version_file.write_text(f"{major}.{minor}.{patch}\n")


def update_version_py(version_py: Path, major: int, minor: int, patch: int):
    """Update __version__ in app/version.py."""
    content = version_py.read_text()
    new_version = f"{major}.{minor}.{patch}"
    content = re.sub(
        r'__version__ = "[^"]+"',
        f'__version__ = "{new_version}"',
        content
    )
    version_py.write_text(content)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ['major', 'minor', 'patch']:
        print("Usage: python bump_version.py [major|minor|patch]")
        sys.exit(1)
    
    bump_type = sys.argv[1]
    
    # Get project root
    project_root = Path(__file__).parent
    version_file = project_root / "VERSION"
    version_py = project_root / "app" / "version.py"
    
    # Read current version
    major, minor, patch = read_version(version_file)
    print(f"Current version: {major}.{minor}.{patch}")
    
    # Bump version
    if bump_type == 'major':
        major += 1
        minor = 0
        patch = 0
    elif bump_type == 'minor':
        minor += 1
        patch = 0
    elif bump_type == 'patch':
        patch += 1
    
    new_version = f"{major}.{minor}.{patch}"
    print(f"New version: {new_version}")
    
    # Write new version
    write_version(version_file, major, minor, patch)
    update_version_py(version_py, major, minor, patch)
    
    print(f"\nVersion bumped to {new_version}")
    print("\nNext steps:")
    print(f"1. Update CHANGELOG.md with changes for version {new_version}")
    print(f"2. Commit changes: git commit -am 'chore: bump version to {new_version}'")
    print(f"3. Create tag: git tag -a v{new_version} -m 'Release v{new_version}'")
    print(f"4. Push: git push origin main --tags")


if __name__ == "__main__":
    main()
