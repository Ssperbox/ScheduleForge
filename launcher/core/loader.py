"""MelonLoader detection, installation, repair and uninstallation.

Downloads come exclusively from the official GitHub releases
(``LavaGang/MelonLoader``), with the published SHA-256 digest verified when the
GitHub API provides one. A local ``MelonLoader.x64.zip`` can be used instead for
offline installs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import backup
from .archive import ArchiveError, extract_zip, safe_member_path
from .config import cache_dir
from .models import ImportReport, LoaderRelease, LoaderStatus
from .net import NetworkError, download_file, http_json, safe_filename

LOGGER = logging.getLogger(__name__)

MELONLOADER_REPO = "LavaGang/MelonLoader"
MELONLOADER_RELEASES_API = f"https://api.github.com/repos/{MELONLOADER_REPO}/releases/latest"
MELONLOADER_RELEASES_PAGE = f"https://github.com/{MELONLOADER_REPO}/releases"
MELONLOADER_ASSET = "MelonLoader.x64.zip"
MELONLOADER_DIR = "MelonLoader"
MARKER_NAME = ".launcher-install.json"
PROXY_DLLS = ("version.dll", "winhttp.dll", "winmm.dll", "dinput8.dll", "dxgi.dll")
MAX_RELEASE_BYTES = 200 * 1024**2
RELEASE_CACHE_SECONDS = 6 * 60 * 60

LogFn = Callable[[str], None]


class LoaderError(Exception):
    """User-presentable loader problem."""


def _noop(_: str) -> None:
    return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def read_marker(game_root: Path) -> dict:
    path = game_root / MELONLOADER_DIR / MARKER_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _version_from_log(loader_dir: Path) -> str | None:
    log = loader_dir / "Latest.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")[-500_000:]
    except OSError:
        return None
    match = re.search(r"MelonLoader v?(\d+\.\d+\.\d+[^\s]*)", text)
    return match.group(1) if match else None


def _has_core_dll(loader_dir: Path) -> bool:
    try:
        for candidate in loader_dir.rglob("MelonLoader.dll"):
            if candidate.is_file():
                return True
        # Case-insensitive filesystem check as a fallback.
        for candidate in loader_dir.rglob("*"):
            if candidate.is_file() and candidate.name.casefold() == "melonloader.dll":
                return True
    except OSError:
        return False
    return False


def detect(game_root: Path) -> LoaderStatus:
    """Inspect the game folder without modifying anything."""
    loader_dir = game_root / MELONLOADER_DIR
    if not loader_dir.is_dir():
        return LoaderStatus(installed=False)
    proxies = [name for name in PROXY_DLLS if (game_root / name).is_file()]
    marker = read_marker(game_root)
    version = marker.get("version") if isinstance(marker.get("version"), str) else None
    if version and version.casefold() in ("unknown", "none"):
        version = None
    if not version:
        version = _version_from_log(loader_dir)
    warnings: list[str] = []
    if not _has_core_dll(loader_dir):
        warnings.append(
            "The MelonLoader folder exists but MelonLoader.dll was not found; "
            "use 'Install / repair' to fix it."
        )
    if not proxies:
        warnings.append(
            "No MelonLoader proxy DLL (version.dll/winhttp.dll/…) was found in the "
            "game folder, so the loader may not start. Reinstall or repair it."
        )
    return LoaderStatus(
        installed=True,
        version=version,
        proxy_dll=proxies[0] if proxies else None,
        warnings=tuple(warnings),
    )


def loader_log_path(game_root: Path) -> Path:
    return game_root / MELONLOADER_DIR / "Latest.log"


# ---------------------------------------------------------------------------
# Official release metadata / download
# ---------------------------------------------------------------------------


def _release_cache_path() -> Path:
    return cache_dir() / "melonloader_release.json"


def _release_to_dict(release: LoaderRelease) -> dict:
    return {
        "version": release.version,
        "asset_name": release.asset_name,
        "download_url": release.download_url,
        "page_url": release.page_url,
        "size_bytes": release.size_bytes,
        "sha256": release.sha256,
        "cached_at": time.time(),
    }


def _release_from_dict(data: dict) -> LoaderRelease:
    return LoaderRelease(
        version=str(data.get("version", "unknown")),
        asset_name=str(data.get("asset_name", MELONLOADER_ASSET)),
        download_url=str(data.get("download_url", "")),
        page_url=str(data.get("page_url", MELONLOADER_RELEASES_PAGE)),
        size_bytes=int(data["size_bytes"]) if data.get("size_bytes") else None,
        sha256=str(data["sha256"]) if data.get("sha256") else None,
    )


def _release_from_api(data: dict) -> LoaderRelease:
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise LoaderError("GitHub returned an unexpected release payload.")
    assets = data.get("assets") or []
    asset = next(
        (a for a in assets if str(a.get("name", "")).casefold() == MELONLOADER_ASSET.casefold()),
        None,
    )
    if asset is None:
        asset = next(
            (
                a
                for a in assets
                if str(a.get("name", "")).casefold().endswith(".zip")
                and "x64" in str(a.get("name", "")).casefold()
                and "debug" not in str(a.get("name", "")).casefold()
            ),
            None,
        )
    if asset is None:
        raise LoaderError(
            "The latest MelonLoader release has no x64 ZIP asset. "
            f"Check {MELONLOADER_RELEASES_PAGE} manually."
        )
    digest = asset.get("digest")
    sha256 = None
    if isinstance(digest, str) and digest.casefold().startswith("sha256:"):
        sha256 = digest.split(":", 1)[1].strip() or None
    return LoaderRelease(
        version=str(data["tag_name"]).lstrip("vV"),
        asset_name=str(asset.get("name")),
        download_url=str(asset.get("browser_download_url", "")),
        page_url=str(data.get("html_url", MELONLOADER_RELEASES_PAGE)),
        size_bytes=int(asset["size"]) if asset.get("size") else None,
        sha256=sha256,
    )


def fetch_latest_release(
    log: LogFn = _noop, *, force: bool = False, timeout: int = 25
) -> LoaderRelease:
    """Return the latest official MelonLoader release (cached for 6 hours)."""
    cache = _release_cache_path()
    if not force and cache.is_file():
        try:
            age = time.time() - cache.stat().st_mtime
            if age < RELEASE_CACHE_SECONDS:
                data = json.loads(cache.read_text(encoding="utf-8"))
                release = _release_from_dict(data)
                if release.download_url:
                    return release
        except (OSError, ValueError):
            pass

    try:
        payload = http_json(
            MELONLOADER_RELEASES_API,
            allowed_hosts=("api.github.com", "github.com"),
            timeout=timeout,
        )
        release = _release_from_api(payload)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(_release_to_dict(release), indent=2), encoding="utf-8")
        except OSError:
            LOGGER.warning("Could not cache release metadata", exc_info=True)
        return release
    except (NetworkError, LoaderError) as exc:
        if cache.is_file():
            try:
                release = _release_from_dict(json.loads(cache.read_text(encoding="utf-8")))
                if release.download_url:
                    log(f"Offline: using cached MelonLoader v{release.version} metadata. ({exc})")
                    return release
            except (OSError, ValueError):
                pass
        raise LoaderError(
            f"Could not check the latest MelonLoader release: {exc}\n"
            "You can install from a local MelonLoader.x64.zip instead."
        ) from exc


def download_release(release: LoaderRelease, log: LogFn = _noop) -> Path:
    """Download the release asset, verifying SHA-256 when published."""
    directory = cache_dir() / "downloads"
    destination = directory / f"MelonLoader-{safe_filename(release.version)}.zip"
    if destination.is_file():
        size_ok = not release.size_bytes or destination.stat().st_size == release.size_bytes
        hash_ok = True
        if release.sha256:
            digest = hashlib.sha256()
            try:
                with open(destination, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                hash_ok = digest.hexdigest().casefold() == release.sha256.casefold()
            except OSError:
                hash_ok = False
        if size_ok and hash_ok:
            log("Using the cached MelonLoader download.")
            return destination
        destination.unlink(missing_ok=True)

    log(f"Downloading MelonLoader v{release.version} from the official GitHub release…")
    try:
        return download_file(
            release.download_url,
            destination,
            allowed_hosts=("github.com", "githubusercontent.com"),
            max_bytes=MAX_RELEASE_BYTES,
            expected_size=release.size_bytes,
            sha256=release.sha256,
            log=log,
        )
    except NetworkError as exc:
        raise LoaderError(f"MelonLoader download failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def _payload_prefix(names: list[str]) -> str:
    """Find the archive prefix that contains the ``MelonLoader/`` payload."""
    lowered = [name.casefold() for name in names]
    if any(name.startswith("melonloader/") for name in lowered):
        return ""
    first_segments = {
        name.split("/", 1)[0] for name in names if "/" in name and name.split("/", 1)[0]
    }
    for candidate in sorted(first_segments):
        prefix = candidate + "/"
        inner = [name[len(prefix) :] for name in lowered if name.startswith(prefix.casefold())]
        if any(name.startswith("melonloader/") for name in inner):
            return candidate
    raise LoaderError(
        "This ZIP does not contain a MelonLoader folder. Download "
        f"'{MELONLOADER_ASSET}' from {MELONLOADER_RELEASES_PAGE} or pick another file."
    )


def install_from_zip(
    zip_path: Path,
    game_root: Path,
    log: LogFn = _noop,
    *,
    version: str | None = None,
    sha256: str | None = None,
    source: str = "local file",
) -> ImportReport:
    """Install or repair MelonLoader from an official release ZIP.

    Existing loader files are backed up first. Nothing outside the loader
    payload is touched.
    """
    if not zip_path.is_file():
        raise LoaderError(f"File not found: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [
                info.filename.replace("\\", "/")
                for info in archive.infolist()
                if not info.is_dir()
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        raise LoaderError(f"'{zip_path.name}' is not a valid ZIP archive.") from exc
    if not names:
        raise LoaderError("The archive is empty.")
    names = [name for name in names if not name.startswith("__MACOSX/")]

    prefix = _payload_prefix(names)
    if not any("melonloader.dll" in name.casefold() for name in names):
        raise LoaderError("MelonLoader.dll is missing from this archive; refusing to install.")

    # Validate every destination before writing anything.
    destinations: list[Path] = []
    for name in names:
        relative = name[len(prefix) + 1 :] if prefix and name.startswith(prefix + "/") else name
        if relative:
            destinations.append(safe_member_path(game_root, relative))

    existing_loader = game_root / MELONLOADER_DIR
    existing_proxies = [game_root / name for name in PROXY_DLLS if (game_root / name).is_file()]
    if existing_loader.is_dir() or existing_proxies:
        try:
            backup.create_backup(
                "Before MelonLoader install/repair",
                "loader-install",
                game_root,
                [existing_loader, *existing_proxies],
                log,
            )
        except backup.BackupError as exc:
            raise LoaderError(
                f"Could not back up the existing loader, so installation stopped: {exc}"
            ) from exc

    log(f"Extracting MelonLoader into {game_root}…")
    try:
        written = extract_zip(zip_path, game_root, strip_prefix=prefix, skip=("__macosx",))
    except ArchiveError as exc:
        raise LoaderError(f"Could not extract the loader: {exc}") from exc

    written_relative = [str(path.relative_to(game_root)).replace("\\", "/") for path in written]
    proxies = [name for name in PROXY_DLLS if (game_root / name).is_file()]
    warnings: list[str] = []
    if not proxies:
        warnings.append(
            "The archive did not place a proxy DLL in the game folder, so the mod "
            "loader may not start. Try the official MelonLoader.x64.zip."
        )
    if (game_root / "BepInEx").is_dir():
        warnings.append(
            "A BepInEx folder is present. BepInEx and MelonLoader use competing "
            "proxy DLLs and may not work together; consider keeping only one loader."
        )

    marker_path = game_root / MELONLOADER_DIR / MARKER_NAME
    marker = {
        "version": version or "unknown",
        "source": source,
        "sha256": sha256,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": written_relative,
    }
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    except OSError as exc:
        warnings.append(f"Loader installed, but the install marker could not be written: {exc}")

    if version:
        log(f"MelonLoader v{version} installed ({len(written_relative)} files).")
    else:
        log(f"MelonLoader installed from {zip_path.name} ({len(written_relative)} files).")
    return ImportReport(installed=written_relative, warnings=warnings)


def install_latest(game_root: Path, log: LogFn = _noop) -> ImportReport:
    """Download and install the latest official MelonLoader release."""
    release = fetch_latest_release(log)
    log(f"Latest official MelonLoader release: v{release.version}")
    archive = download_release(release, log)
    report = install_from_zip(
        archive,
        game_root,
        log,
        version=release.version,
        sha256=release.sha256,
        source=release.download_url,
    )
    log(f"Release notes: {release.page_url}")
    return report


# ---------------------------------------------------------------------------
# Uninstallation
# ---------------------------------------------------------------------------


def _remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink(missing_ok=True)


def uninstall(
    game_root: Path, log: LogFn = _noop, *, remove_mods: bool = False
) -> ImportReport:
    """Remove MelonLoader, optionally deleting its mod folders (with backups)."""
    status = detect(game_root)
    if not status.installed:
        raise LoaderError("MelonLoader is not installed in this folder.")
    marker = read_marker(game_root)
    loader_dir = game_root / MELONLOADER_DIR
    proxies = [game_root / name for name in PROXY_DLLS if (game_root / name).is_file()]
    mod_dirs = [game_root / name for name in ("Mods", "Plugins", "UserLibs", "UserData")]
    binex_present = (game_root / "BepInEx").is_dir()

    backup_targets: list[Path] = [loader_dir, *proxies]
    if remove_mods:
        backup_targets.extend(mod_dirs)
    try:
        backup.create_backup(
            "Before MelonLoader uninstall", "loader-uninstall", game_root, backup_targets, log
        )
    except backup.BackupError as exc:
        raise LoaderError(
            f"Could not back up the loader, so uninstallation stopped: {exc}"
        ) from exc

    removed: list[str] = []
    warnings: list[str] = []

    # Root-level files we installed (proxy DLL, dobby.dll, …), recorded in the marker.
    marker_files = marker.get("files") if isinstance(marker.get("files"), list) else []
    for relative in marker_files:
        if not isinstance(relative, str) or "/" in relative.replace("\\", "/").strip("/"):
            continue
        candidate = game_root / relative
        if candidate.is_file() and candidate.suffix.casefold() == ".dll":
            try:
                candidate.unlink()
                removed.append(relative)
            except OSError as exc:
                warnings.append(f"Could not remove {relative}: {exc}")

    # Detected proxies not covered by the marker. Never remove one when another
    # loader (BepInEx) is present, because proxy DLLs may be shared.
    if binex_present:
        warnings.append(
            "A BepInEx folder was found; proxy DLLs were left in place to avoid "
            "breaking the other loader."
        )
    else:
        for proxy in proxies:
            if proxy.name in removed:
                continue
            try:
                proxy.unlink(missing_ok=True)
                removed.append(proxy.name)
            except OSError as exc:
                warnings.append(f"Could not remove {proxy.name}: {exc}")

    try:
        _remove_tree(loader_dir)
        removed.append(MELONLOADER_DIR + "/")
    except OSError as exc:
        raise LoaderError(
            f"Could not delete the MelonLoader folder: {exc}. "
            "Close the game and any file explorer windows, then retry."
        ) from exc

    if remove_mods:
        for directory in mod_dirs:
            if not directory.exists():
                continue
            try:
                _remove_tree(directory)
                removed.append(directory.name + "/")
            except OSError as exc:
                warnings.append(f"Could not delete {directory.name}: {exc}")

    log("MelonLoader uninstalled." + (" Mod folders were removed." if remove_mods else " Mods were kept."))
    return ImportReport(skipped=[], warnings=warnings, installed=[])
