"""Inspection, validation and save-location helpers for Schedule I."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import loader as loader_mod
from .models import (
    GAME_DATA_DIR,
    GAME_EXE_NAME,
    MONO_BRANCH,
    STEAM_APP_ID,
    Backend,
    GameInstall,
)
from .platform_win import is_process_running, is_windows, stop_process
from .steam_detect import SteamAppLocation, find_app

LOGGER = logging.getLogger(__name__)

_UNITY_VERSION_RE = re.compile(rb"(20\d{2}\.\d+\.\d+[abfp]\d+)")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str


def _case_insensitive_child(directory: Path, name: str) -> Path | None:
    try:
        for child in directory.iterdir():
            if child.name.casefold() == name.casefold():
                return child
    except OSError:
        return None
    return None


def find_executable(root: Path) -> Path | None:
    """Find ``Schedule I.exe`` tolerating case differences."""
    direct = root / GAME_EXE_NAME
    if direct.is_file():
        return direct
    if not root.is_dir():
        return None
    found = _case_insensitive_child(root, GAME_EXE_NAME)
    return found if found is not None and found.is_file() else None


def find_data_dir(root: Path) -> Path | None:
    direct = root / GAME_DATA_DIR
    if direct.is_dir():
        return direct
    if not root.is_dir():
        return None
    found = _case_insensitive_child(root, GAME_DATA_DIR)
    return found if found is not None and found.is_dir() else None


def detect_backend(root: Path) -> Backend:
    """IL2CPP ships ``GameAssembly.dll``; Mono has a managed ``Assembly-CSharp.dll``."""
    game_assembly = _case_insensitive_child(root, "GameAssembly.dll") if root.is_dir() else None
    data = find_data_dir(root)
    managed = data / "Managed" / "Assembly-CSharp.dll" if data else None
    has_il2cpp = bool(game_assembly and game_assembly.is_file())
    has_mono = bool(managed and managed.is_file())
    if has_mono and not has_il2cpp:
        return Backend.MONO
    if has_il2cpp and not has_mono:
        return Backend.IL2CPP
    if has_il2cpp and has_mono:
        # Should not happen for a shipped build; IL2CPP wins as the default.
        return Backend.IL2CPP
    return Backend.UNKNOWN


def detect_unity_version(root: Path) -> str | None:
    """Best-effort Unity version read from the serialized ``globalgamemanagers``.

    This is not a documented API; the version string appears in the serialized
    file header for the Unity versions we care about. Failure simply yields
    ``None`` and the UI shows "unknown".
    """
    data = find_data_dir(root)
    if data is None:
        return None
    for name in ("globalgamemanagers", "globalgamemanagers.assets"):
        candidate = data / name
        if not candidate.is_file():
            continue
        try:
            with open(candidate, "rb") as handle:
                head = handle.read(4096)
        except OSError:
            continue
        match = _UNITY_VERSION_RE.search(head)
        if match:
            return match.group(1).decode("ascii", "replace")
    return None


def validate_game_dir(path: Path) -> ValidationResult:
    """Check that *path* really is a Schedule I installation."""
    try:
        path = path.expanduser()
    except (OSError, RuntimeError):
        return ValidationResult(False, "That path is not valid.")
    if not path.exists():
        return ValidationResult(False, f"The folder does not exist:\n{path}")
    if not path.is_dir():
        return ValidationResult(False, "The selected path is a file, not a game folder.")
    exe = find_executable(path)
    if exe is None:
        return ValidationResult(
            False,
            f"'{GAME_EXE_NAME}' was not found in:\n{path}\n\n"
            "Pick the folder that contains the game executable "
            "(Steam → Schedule I → Manage → Browse local files).",
        )
    if find_data_dir(path) is None:
        return ValidationResult(
            False,
            f"'{GAME_DATA_DIR}' was not found next to the executable.\n"
            "This does not look like a complete Schedule I installation.",
        )
    return ValidationResult(True, "Valid Schedule I installation.")


def default_save_dir() -> Path | None:
    """Where the game stores saves on this machine (may not exist yet)."""
    if is_windows():
        profile = os.environ.get("USERPROFILE")
        if not profile:
            return None
        return (
            Path(profile)
            / "AppData"
            / "LocalLow"
            / "TVGS"
            / "Schedule I"
            / "Saves"
        )
    # Linux/Steam Deck (Proton) fallback; marked best-effort in the UI.
    home = Path.home()
    candidates = [
        home
        / ".steam"
        / "steam"
        / "steamapps"
        / "compatdata"
        / STEAM_APP_ID
        / "pfx"
        / "drive_c"
        / "users"
        / "steamuser"
        / "AppData"
        / "LocalLow"
        / "TVGS"
        / "Schedule I"
        / "Saves",
        home
        / ".local"
        / "share"
        / "Steam"
        / "steamapps"
        / "compatdata"
        / STEAM_APP_ID
        / "pfx"
        / "drive_c"
        / "users"
        / "steamuser"
        / "AppData"
        / "LocalLow"
        / "TVGS"
        / "Schedule I"
        / "Saves",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def save_profiles() -> list[Path]:
    """Existing per-player save folders (``...\\Saves\\<user-id>``)."""
    root = default_save_dir()
    if root is None or not root.is_dir():
        return []
    try:
        return sorted((child for child in root.iterdir() if child.is_dir()), key=lambda p: p.name)
    except OSError:
        return []


def inspect_game(
    root: Path, steam_app: SteamAppLocation | None = None
) -> GameInstall:
    """Build a :class:`GameInstall` with backend, loader and save information."""
    root = root.expanduser()
    exe = find_executable(root) or (root / GAME_EXE_NAME)
    backend = detect_backend(root)
    status = loader_mod.detect(root)

    # Loader warnings come first: they are usually the most actionable.
    warnings: list[str] = list(status.warnings)
    if backend is Backend.IL2CPP:
        warnings.append(
            "IL2CPP build detected. MelonLoader's IL2CPP support requires the "
            ".NET 6 Desktop Runtime to be installed before launching the game."
        )
    elif backend is Backend.MONO:
        warnings.append(
            "Mono build detected. This is the optional Steam beta branch "
            f"'{MONO_BRANCH}'; IL2CPP mods will not load on it."
        )
    else:
        warnings.append(
            "Could not determine the Unity scripting backend; the game files may "
            "be incomplete or from an unsupported version."
        )

    save = default_save_dir()
    return GameInstall(
        root=root,
        exe=exe,
        backend=backend,
        unity_version=detect_unity_version(root),
        build_id=steam_app.build_id if steam_app else None,
        last_updated=steam_app.last_updated if steam_app else None,
        steam_library=str(steam_app.library) if steam_app else None,
        save_dir=save,
        loader=status,
        warnings=warnings,
    )


def detect_installations() -> list[GameInstall]:
    """Find valid Schedule I installs across every Steam library."""
    results: list[GameInstall] = []
    seen: set[str] = set()
    for app in find_app():
        key = str(app.install_dir).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not app.install_dir.is_dir():
            LOGGER.info("Steam manifest points to a missing folder: %s", app.install_dir)
            continue
        validation = validate_game_dir(app.install_dir)
        if not validation.ok:
            LOGGER.warning("Rejected Steam candidate %s: %s", app.install_dir, validation.message)
            continue
        results.append(inspect_game(app.install_dir, app))
    return results


def is_game_running() -> bool:
    return is_process_running(GAME_EXE_NAME)


def stop_game() -> bool:
    """Ask the running game to close (taskkill without /F, so it saves like a normal close)."""
    return stop_process(GAME_EXE_NAME)
