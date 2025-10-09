"""
Version information for TubeCord.
Follows Semantic Versioning 2.0.0 (https://semver.org/)
"""

from pathlib import Path

__version__ = "1.0.0"
__version_info__ = tuple(int(part) for part in __version__.split('.'))


def get_version() -> str:
    """
    Get the current version of TubeCord.
    Reads from VERSION file if available, falls back to hardcoded version.
    
    Returns:
        Version string (e.g., "1.0.0")
    """
    try:
        # Try to read from VERSION file in project root
        version_file = Path(__file__).parent.parent / "VERSION"
        if version_file.exists():
            return version_file.read_text().strip()
    except Exception:
        pass
    
    # Fallback to hardcoded version
    return __version__


def get_version_info() -> dict:
    """
    Get detailed version information.
    
    Returns:
        Dictionary with version details
    """
    version = get_version()
    parts = version.split('.')
    
    return {
        'version': version,
        'major': int(parts[0]) if len(parts) > 0 else 0,
        'minor': int(parts[1]) if len(parts) > 1 else 0,
        'patch': int(parts[2]) if len(parts) > 2 else 0,
        'prerelease': parts[3] if len(parts) > 3 else None
    }


# Convenience exports
VERSION = get_version()
VERSION_INFO = get_version_info()
