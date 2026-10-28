"""Game save slots: listing, launcher-side names and safe deletion.

Each save slot is a folder under ``...\\Saves\\<user-id>``. The launcher never
edits what is inside a slot: display names live in the launcher config, and
deleting a slot always creates a ZIP backup first.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import backup, game
from .models import BackupInfo, Profile


class SaveError(Exception):
    """Raised when a save slot cannot be deleted safely."""


@dataclass(frozen=True)
class SaveSlot:
    folder: Path
    label: str
    modified: str
    size_bytes: int

    @property
    def key(self) -> str:
        return self.folder.name


def _folder_size(folder: Path) -> int:
    total = 0
    for file in folder.rglob("*"):
        try:
            if file.is_file():
                total += file.stat().st_size
        except OSError:
            continue
    return total


def list_slots(labels: dict[str, str] | None = None) -> list[SaveSlot]:
    """Return every save slot, using launcher labels when they exist."""
    labels = labels or {}
    slots: list[SaveSlot] = []
    for folder in game.save_profiles():
        try:
            modified = datetime.fromtimestamp(folder.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
        except OSError:
            modified = "—"
        slots.append(
            SaveSlot(
                folder=folder,
                label=labels.get(folder.name) or folder.name,
                modified=modified,
                size_bytes=_folder_size(folder),
            )
        )
    return slots


def delete_slot(
    slot: SaveSlot,
    log: Callable[[str], None] | None = None,
) -> BackupInfo | None:
    """Back up a slot into Backups, then remove it. Returns the backup info."""
    root = slot.folder.parent
    if not slot.folder.is_dir() or slot.folder.parent.name != "Saves":
        raise SaveError(f"Refusing to delete unexpected folder: {slot.folder}")
    # An empty slot yields no backup, but then there is no data to lose.
    info = backup.create_backup(
        f"Save {slot.label}", "saves", root, [slot.folder], log
    )
    shutil.rmtree(slot.folder)
    return info


def link_slot_to_profile(profile: Profile, slot_key: str | None) -> None:
    """Record which save slot belongs to a profile. Does not touch files."""
    profile.save_slot = slot_key or None

