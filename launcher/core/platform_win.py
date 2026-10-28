"""Small Windows-specific helpers with graceful non-Windows fallbacks.

The launcher targets Windows 10/11, but isolating OS calls here keeps the rest
of the core testable on other platforms and prevents import-time crashes.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from pathlib import Path


def is_windows() -> bool:
    return os.name == "nt"


def steam_registry_paths() -> list[Path]:
    """Read Steam install candidates from the registry (32- and 64-bit views).

    ``HKCU\\Software\\Valve\\Steam`` → ``SteamPath`` and
    ``HKLM\\SOFTWARE\\Valve\\Steam`` → ``InstallPath`` are the documented,
    widely used locations. Failures (missing keys, permissions) are ignored.
    """
    if not is_windows():
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - only on non-Windows
        return []

    candidates: list[tuple[int, str, str]] = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ]
    paths: list[Path] = []
    for hive, subkey, value_name in candidates:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, value_name)
            except OSError:
                continue
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            if isinstance(value, str) and value.strip():
                paths.append(Path(value.strip()))
    return paths


def default_steam_paths() -> list[Path]:
    """Common Steam install locations, used as a fallback for the registry."""
    if is_windows():
        roots: list[Path] = []
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env)
            if base:
                roots.append(Path(base) / "Steam")
        return roots
    home = Path.home()
    return [
        home / ".steam" / "steam",
        home / ".local" / "share" / "Steam",
    ]


def normalize_path_key(path: Path) -> str:
    """Case-insensitive dedupe key on Windows; exact path elsewhere."""
    try:
        text = str(path.resolve())
    except OSError:
        text = str(path)
    return text.casefold() if is_windows() else text


def is_process_running(exe_name: str) -> bool:
    """Return True if a process with *exe_name* is actually running.

    Uses ``tasklist /V`` on Windows. Zombie entries (processes that exited but
    still appear because a handle was never closed) report status ``Unknown``;
    those must not block mod changes. ``STATUS ne Unknown`` keeps hung games
    (``Not Responding``) treated as running, while the status column is also
    checked defensively. A query failure returns False rather than blocking the
    UI with a false positive.
    """
    try:
        if is_windows():
            result = subprocess.run(
                [
                    "tasklist",
                    "/V",
                    "/FO",
                    "CSV",
                    "/NH",
                    "/FI",
                    f"IMAGENAME eq {exe_name}",
                    "/FI",
                    "STATUS ne Unknown",
                ],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for row in csv.reader(io.StringIO(result.stdout or "")):
                if len(row) < 6:
                    continue
                if row[0].strip().casefold() != exe_name.casefold():
                    continue
                status = row[5].strip().casefold()
                if status in ("", "unknown", "n/a"):
                    continue
                return True
            return False
        result = subprocess.run(
            ["ps", "-A", "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return any(
            Path(line.strip()).name.casefold() == exe_name.casefold()
            for line in (result.stdout or "").splitlines()
        )
    except (OSError, subprocess.SubprocessError):
        return False


def stop_process(exe_name: str) -> bool:
    """Ask every process named *exe_name* to close; returns True on success."""
    try:
        if is_windows():
            result = subprocess.run(
                ["taskkill", "/IM", exe_name, "/T"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0
        result = subprocess.run(["pkill", "-x", exe_name], timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def open_in_file_manager(path: Path) -> None:
    """Reveal a file/folder in Explorer/Finder/xdg-open."""
    if is_windows():
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def is_admin() -> bool:
    """Best-effort check for elevated privileges (Windows only)."""
    if not is_windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def app_executable() -> Path:
    """Path of the running executable (PyInstaller-aware)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.argv[0]).resolve()
