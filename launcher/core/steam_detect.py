"""Steam installation discovery on Windows.

The reliable route is:

1. registry (per-user ``SteamPath`` / machine ``InstallPath``) → Steam root;
2. ``<root>/steamapps/libraryfolders.vdf`` or ``<root>/config/libraryfolders.vdf``
   → every library folder, including legacy and modern formats;
3. ``<library>/steamapps/appmanifest_<appid>.acf`` → ``installdir``, ``buildid``.

Never guess the game folder from the desktop or from a hard-coded drive: treat
registry and library results as candidates and validate the game files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import GAME_FOLDER_NAME, STEAM_APP_ID
from .platform_win import (
    default_steam_paths,
    normalize_path_key,
    steam_registry_paths,
)
from .vdf import get_ci, iter_library_paths, read

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SteamAppLocation:
    """Where Steam says an app is installed."""

    library: Path
    install_dir: Path
    manifest_path: Path
    build_id: str | None = None
    last_updated: str | None = None


def steam_roots() -> list[Path]:
    """All plausible Steam client roots, deduplicated and existing."""
    candidates = [*steam_registry_paths(), *default_steam_paths()]
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        key = normalize_path_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def library_folders(steam_root: Path) -> list[Path]:
    """Read every library listed by Steam, plus the Steam root itself."""
    candidates = [
        steam_root / "steamapps" / "libraryfolders.vdf",
        steam_root / "config" / "libraryfolders.vdf",
    ]
    libraries: list[Path] = [steam_root]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        tree = read(candidate)
        for raw_path in iter_library_paths(tree):
            try:
                path = Path(raw_path.replace("\\\\", "\\"))
            except (OSError, ValueError):
                continue
            libraries.append(path)

    unique: list[Path] = []
    seen: set[str] = set()
    for library in libraries:
        try:
            resolved = library.expanduser()
            if not resolved.is_dir():
                continue
        except OSError:
            continue
        key = normalize_path_key(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def all_libraries() -> list[Path]:
    libraries: list[Path] = []
    seen: set[str] = set()
    for root in steam_roots():
        for library in library_folders(root):
            key = normalize_path_key(library)
            if key not in seen:
                seen.add(key)
                libraries.append(library)
    # No caching: the library list can change while Steam is running.
    return libraries


def read_app_manifest(manifest_path: Path) -> dict:
    tree = read(manifest_path)
    state = get_ci(tree, "AppState", {})
    return state if isinstance(state, dict) else {}


def _format_last_updated(value: object) -> str | None:
    try:
        stamp = int(str(value))
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, OverflowError, ValueError):
        return None


def find_app(appid: str = STEAM_APP_ID) -> list[SteamAppLocation]:
    """Return every library that contains this app's manifest."""
    results: list[SteamAppLocation] = []
    for library in all_libraries():
        manifest = library / "steamapps" / f"appmanifest_{appid}.acf"
        if not manifest.is_file():
            continue
        state = read_app_manifest(manifest)
        found_appid = str(get_ci(state, "appid", "") or "")
        if found_appid and found_appid != appid:
            LOGGER.warning("Skipping manifest with mismatched appid: %s", manifest)
            continue
        install_name = str(get_ci(state, "installdir", "") or GAME_FOLDER_NAME)
        install_dir = library / "steamapps" / "common" / install_name
        results.append(
            SteamAppLocation(
                library=library,
                install_dir=install_dir,
                manifest_path=manifest,
                build_id=str(get_ci(state, "buildid", "") or "") or None,
                last_updated=_format_last_updated(get_ci(state, "LastUpdated")),
            )
        )
    return results


def find_steam_executable() -> Path | None:
    for root in steam_roots():
        for name in ("steam.exe", "steam"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def steam_uri(appid: str = STEAM_APP_ID) -> str:
    return f"steam://rungameid/{appid}"
