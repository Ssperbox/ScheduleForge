"""ZIP-based backups taken before any destructive change.

Backups live in ``%APPDATA%\\ScheduleIModLauncher\\backups`` and contain a
``backup.json`` manifest plus a ``files/`` subtree with paths relative to the
directory they were taken from (normally the game folder).

Restores always create a safety backup of the files they are about to
overwrite, and extraction goes through the zip-slip-safe extractor.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from .archive import ArchiveError, extract_zip, safe_member_path
from .config import backups_dir
from .models import BackupInfo

LOGGER = logging.getLogger(__name__)

MANIFEST_NAME = "backup.json"
MAX_BACKUP_FILE_BYTES = 2 * 1024**3


class BackupError(Exception):
    """Raised when a backup cannot be created, read or restored."""


def _slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return (slug or "backup")[:48]


def _iter_files(base_dir: Path, paths: Iterable[Path]) -> list[Path]:
    """Expand files/directories into a deduplicated list of existing files."""
    seen: set[str] = set()
    result: list[Path] = []
    for entry in paths:
        entry = Path(entry)
        if entry.is_dir():
            candidates = [p for p in entry.rglob("*") if p.is_file()]
        elif entry.is_file():
            candidates = [entry]
        else:
            continue
        for candidate in candidates:
            try:
                candidate.resolve().relative_to(base_dir.resolve())
            except (OSError, ValueError):
                LOGGER.warning("Skipping %s: outside backup base %s", candidate, base_dir)
                continue
            key = str(candidate.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
    return result


def create_backup(
    label: str,
    kind: str,
    base_dir: Path,
    paths: Iterable[Path],
    log: Callable[[str], None] | None = None,
) -> BackupInfo | None:
    """Create a backup of *paths*; returns ``None`` when there is nothing to save."""
    base_dir = base_dir.resolve()
    files = _iter_files(base_dir, paths)
    if not files:
        return None
    total = 0
    for file in files:
        try:
            total += file.stat().st_size
        except OSError:
            continue
    if total > MAX_BACKUP_FILE_BYTES:
        raise BackupError(
            f"Refusing to back up {total / 1024**3:.1f} GiB at once; "
            "pick a smaller set of files."
        )

    directory = backups_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_id = f"{stamp}-{_slugify(label)}"
    destination = directory / f"{backup_id}.zip"
    suffix = 1
    while destination.exists():
        destination = directory / f"{backup_id}-{suffix}.zip"
        suffix += 1

    created = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = {
        "format": 1,
        "label": label,
        "kind": kind,
        "created_at": created,
        "base_dir": str(base_dir),
        "files": [str(file.relative_to(base_dir)).replace("\\", "/") for file in files],
    }
    try:
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
            for file in files:
                relative = str(file.relative_to(base_dir)).replace("\\", "/")
                try:
                    archive.write(file, f"files/{relative}")
                except (OSError, ValueError) as exc:
                    LOGGER.warning("Could not add %s to backup: %s", file, exc)
    except OSError as exc:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise BackupError(f"Could not create backup: {exc}") from exc

    if log:
        log(f"Backup created: {destination.name} ({len(files)} files)")
    info = BackupInfo(
        id=destination.stem,
        label=label,
        kind=kind,
        created_at=created,
        size_bytes=destination.stat().st_size,
        base_dir=str(base_dir),
        path=destination,
    )
    return info


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read(MANIFEST_NAME)
        data = json.loads(raw.decode("utf-8"))
    except (KeyError, OSError, ValueError) as exc:
        raise BackupError(f"Backup manifest is missing or corrupt: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise BackupError("Backup manifest has an unexpected format.")
    return data


def list_backups() -> list[BackupInfo]:
    """Newest first; unreadable backups are skipped."""
    directory = backups_dir()
    if not directory.is_dir():
        return []
    results: list[BackupInfo] = []
    for path in sorted(directory.glob("*.zip"), key=lambda p: p.name, reverse=True):
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = _read_manifest(archive)
            results.append(
                BackupInfo(
                    id=path.stem,
                    label=str(manifest.get("label", "Backup")),
                    kind=str(manifest.get("kind", "unknown")),
                    created_at=str(manifest.get("created_at", "")),
                    size_bytes=path.stat().st_size,
                    base_dir=str(manifest.get("base_dir", "")),
                    path=path,
                )
            )
        except (BackupError, OSError, zipfile.BadZipFile) as exc:
            LOGGER.warning("Ignoring unreadable backup %s: %s", path, exc)
    return results


def _find_backup(backup_id: str) -> Path:
    if not backup_id or any(sep in backup_id for sep in ("/", "\\", "..")):
        raise BackupError("Invalid backup id.")
    path = backups_dir() / f"{backup_id}.zip"
    if not path.is_file():
        raise BackupError(f"Backup not found: {backup_id}")
    return path


def restore_backup(
    backup_id: str,
    base_dir: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Restore a backup, returning the restored relative paths.

    *base_dir* overrides the recorded location (the UI passes the currently
    selected game folder). Files overwritten by the restore are themselves
    backed up first.
    """
    path = _find_backup(backup_id)
    with zipfile.ZipFile(path) as archive:
        manifest = _read_manifest(archive)
        target = Path(base_dir) if base_dir else Path(str(manifest.get("base_dir", "")))
        if not str(target) or target == Path("."):
            raise BackupError("Backup does not record a target folder.")
        target = target.resolve()
        if target.parent == target:
            raise BackupError("Refusing to restore into a drive root.")

        members: list[str] = []
        for name in archive.namelist():
            normalized = name.replace("\\", "/")
            if not normalized.startswith("files/"):
                continue
            # Validate now so a bad entry fails before anything is written.
            try:
                safe_member_path(target, normalized[len("files/") :])
            except ArchiveError as exc:
                raise BackupError(f"Backup contains an unsafe path: {exc}") from exc
            members.append(normalized)
        if not members:
            raise BackupError("Backup contains no files.")

        existing = [target / name[len("files/") :] for name in members]
        existing = [candidate for candidate in existing if candidate.is_file()]
        if existing:
            try:
                create_backup(
                    "Auto backup before restore",
                    "pre-restore",
                    target,
                    existing,
                    log,
                )
            except BackupError as exc:
                raise BackupError(
                    f"Could not back up files that would be overwritten: {exc}"
                ) from exc

        try:
            written = extract_zip(
                path, target, strip_prefix="files", only_under="files"
            )
        except ArchiveError as exc:
            raise BackupError(f"Restore failed: {exc}") from exc

    relative = [str(item.relative_to(target)).replace("\\", "/") for item in written]
    if log:
        log(f"Restored {len(relative)} file(s) from {path.name}.")
    return relative


def delete_backup(backup_id: str) -> None:
    path = _find_backup(backup_id)
    try:
        path.unlink()
    except OSError as exc:
        raise BackupError(f"Could not delete backup: {exc}") from exc


def backups_path() -> Path:
    """Public helper for the UI ("open backups folder")."""
    directory = backups_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def remove_file_with_backup(path: Path, base_dir: Path, log=None) -> None:
    """Convenience for deleting a single file after backing it up."""
    if path.is_file():
        create_backup(f"Before deleting {path.name}", "delete", base_dir, [path], log)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise BackupError(f"Could not delete {path.name}: {exc}") from exc
