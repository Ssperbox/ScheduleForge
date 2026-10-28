"""Starting the game, through Steam or directly.

MelonLoader is injected by a proxy DLL in the game folder, so it loads the same
way whether Steam or the executable starts the process. Steam launching is the
default because it keeps achievements, playtime and Steamworks initialisation
behaving normally.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .models import GameInstall, LaunchMethod
from .platform_win import is_windows
from .steam_detect import steam_uri

LOGGER = logging.getLogger(__name__)


class LaunchError(Exception):
    """User-presentable launch failure."""


@dataclass(frozen=True)
class LaunchResult:
    method: str
    command: str
    pid: int | None = None


def parse_extra_args(text: str) -> list[str]:
    """Split launch arguments the way a Windows command line would."""
    text = text.strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=False)
    except ValueError as exc:
        raise LaunchError(f"Launch arguments are malformed: {exc}") from exc


def _open_uri(uri: str) -> None:
    if is_windows():
        os.startfile(uri)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", uri])
    else:
        subprocess.Popen(["xdg-open", uri])


def _reap_process(process: subprocess.Popen, pid: int) -> None:
    """Wait for a direct-launched game so Windows releases its process handle.

    Without this, an exited child can remain visible as a zombie ``Unknown``
    process, which used to make the launcher think the game was still open.
    """
    try:
        process.wait()
    except Exception:  # noqa: BLE001 - background cleanup must never crash
        LOGGER.debug("Could not reap game process %s", pid, exc_info=True)
    else:
        LOGGER.info("Game process %s exited.", pid)


def launch_game(
    game: GameInstall,
    method: str,
    extra_args: str = "",
    log: Callable[[str], None] | None = None,
) -> LaunchResult:
    """Launch Schedule I and return a description of what was started."""
    if not game.exe.is_file():
        raise LaunchError(
            f"The game executable was not found:\n{game.exe}\n"
            "Use Browse… to select the correct folder."
        )
    args = parse_extra_args(extra_args)
    if method == LaunchMethod.STEAM.value:
        if args:
            raise LaunchError(
                "Steam launch does not support custom arguments.\n"
                "Switch to 'Direct executable' in Settings, or clear the arguments."
            )
        uri = steam_uri()
        try:
            _open_uri(uri)
        except OSError as exc:
            raise LaunchError(
                f"Could not ask Steam to start the game: {exc}\n"
                "Make sure Steam is installed and running, or switch to a direct launch."
            ) from exc
        if log:
            log(f"Asked Steam to launch Schedule I ({uri}).")
        return LaunchResult(method=method, command=uri, pid=None)

    if log:
        log("Starting the game executable directly (Steam must be running).")
    creation_flags = 0
    if is_windows():
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            [str(game.exe), *args],
            cwd=str(game.root),
            close_fds=True,
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise LaunchError(f"Could not start the game: {exc}") from exc
    # Reap the child in the background. Holding a Popen handle after the game
    # exits leaves an "Unknown" zombie process in tasklist on Windows.
    threading.Thread(
        target=_reap_process,
        args=(process, process.pid),
        name="game-process-reaper",
        daemon=True,
    ).start()
    if log:
        log(f"Game process started (PID {process.pid}).")
    return LaunchResult(
        method=method,
        command=" ".join([str(game.exe), *args]),
        pid=process.pid,
    )


def loader_present(game: GameInstall) -> bool:
    return game.loader.installed and bool(game.loader.proxy_dll)
