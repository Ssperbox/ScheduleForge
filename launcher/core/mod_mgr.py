"""Mod discovery, enable/disable, import and removal.

Enable/disable works by renaming ``Foo.dll`` → ``Foo.dll.disabled`` (and back),
which MelonLoader ignores. Nothing is copied or deleted when toggling, so the
same files can be shared by several profiles.

Imports route files using the MelonLoader folder conventions
(``Mods``/``Plugins``/``UserLibs``/``UserData``), read Thunderstore
``manifest.json`` metadata when present, and write a small sidecar
``<name>.modinfo.json`` so the UI can show name/version/author later.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import backup
from .archive import ArchiveError, extract_zip, manifest_member, staging_dir
from .models import (
    DISABLED_SUFFIX,
    MOD_DIRS,
    SIDECAR_SUFFIX,
    ImportReport,
    ModFile,
    ModKind,
    Profile,
)

LOGGER = logging.getLogger(__name__)

TOP_DIR_MAP = {
    "mods": ("Mods", ModKind.MOD),
    "plugins": ("Plugins", ModKind.PLUGIN),
    "userlibs": ("UserLibs", ModKind.USERLIB),
    "userdata": ("UserData", None),
}
ROOT_DOCS = {
    "manifest.json",
    "readme.md",
    "readme.txt",
    "icon.png",
    "changelog.md",
    "changelog.txt",
    "license",
    "license.md",
    "license.txt",
    ".gitignore",
}
CONFIG_SUFFIXES = {".json", ".xml", ".cfg", ".ini", ".yaml", ".yml", ".txt", ".toml"}
_FILENAME_META_RE = re.compile(
    r"^(?P<author>[A-Za-z0-9_.]+)-(?P<name>[A-Za-z0-9_. ]+)-(?P<version>\d+\.\d+(?:\.\d+)*)$"
)
_VERSION_RE = re.compile(r"[vV]?(\d+\.\d+(?:\.\d+)*)")


class ModError(Exception):
    """User-presentable mod management problem."""


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _strip_disabled(path: Path) -> Path:
    if path.name.casefold().endswith(DISABLED_SUFFIX):
        return path.with_name(path.name[: -len(DISABLED_SUFFIX)])
    return path


def sidecar_for(path: Path) -> Path:
    base = _strip_disabled(path)
    return base.with_name(base.stem + SIDECAR_SUFFIX)


def _read_sidecar(path: Path) -> dict:
    try:
        data = json.loads(sidecar_for(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _filename_metadata(stem: str) -> dict:
    match = _FILENAME_META_RE.match(stem)
    if match:
        return {
            "name": match.group("name").replace("_", " ").strip(),
            "author": match.group("author"),
            "version": match.group("version"),
            "source": "filename",
        }
    version_match = _VERSION_RE.search(stem)
    version = version_match.group(1) if version_match else None
    name = stem[: version_match.start()].rstrip(" -_") if version_match else stem
    return {
        "name": name.replace("_", " ").strip() or stem,
        "author": None,
        "version": version,
        "source": "filename",
    }


def _write_sidecar(
    target: Path,
    *,
    name: str,
    version: str | None,
    author: str | None,
    dependencies: list[str] | tuple[str, ...] = (),
    description: str | None = None,
    origin: str | None = None,
) -> None:
    payload = {
        "name": name,
        "version": version,
        "author": author,
        "dependencies": list(dependencies),
        "description": description,
        "origin": origin,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        sidecar_for(target).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        raise ModError(f"Could not write metadata for {target.name}: {exc}") from exc


def _unique_rel(relative: str, used: set[str]) -> str:
    """Avoid two archive members colliding on the same destination."""
    candidate = relative
    stem = Path(relative).stem
    suffix = Path(relative).suffix
    parent = str(Path(relative).parent).replace("\\", "/")
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{parent}/{stem}-{counter}{suffix}" if parent != "." else f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _kind_for_dir(dir_name: str) -> ModKind:
    return ModKind.from_dir_name(dir_name)


def scan(game_root: Path) -> list[ModFile]:
    """List every mod DLL in the loader folders, enabled or disabled."""
    mods: list[ModFile] = []
    for dir_name in MOD_DIRS:
        directory = game_root / dir_name
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.rglob("*"), key=lambda p: str(p).casefold())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            name_lower = entry.name.casefold()
            enabled = not name_lower.endswith(DISABLED_SUFFIX)
            base_name = _strip_disabled(entry).name
            if not base_name.casefold().endswith(".dll"):
                continue
            relative = entry.relative_to(game_root).as_posix()
            if not enabled:
                relative = relative[: -len(DISABLED_SUFFIX)]
            meta = _read_sidecar(entry)
            if meta:
                source = "sidecar"
                meta = {
                    "name": str(meta.get("name") or Path(base_name).stem),
                    "version": meta.get("version"),
                    "author": meta.get("author"),
                    "dependencies": meta.get("dependencies") or (),
                    "description": meta.get("description"),
                    "origin": meta.get("origin"),
                }
            else:
                meta = _filename_metadata(Path(base_name).stem)
                source = "filename"
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            mods.append(
                ModFile(
                    path=entry,
                    rel=relative,
                    name=str(meta.get("name")),
                    version=str(meta["version"]) if meta.get("version") else None,
                    author=str(meta["author"]) if meta.get("author") else None,
                    kind=_kind_for_dir(directory.name),
                    enabled=enabled,
                    size=size,
                    source=source,
                    dependencies=tuple(str(dep) for dep in (meta.get("dependencies") or ())),
                    description=str(meta["description"]) if meta.get("description") else None,
                    origin=str(meta["origin"]) if meta.get("origin") else None,
                )
            )
    return mods


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------


def set_enabled(mod: ModFile, enabled: bool, log=None) -> ModFile:
    """Rename a mod file to enable/disable it, without deleting anything."""
    if mod.enabled == enabled:
        return mod
    source = mod.path
    target = source.with_name(source.name[: -len(DISABLED_SUFFIX)]) if enabled else source.with_name(
        source.name + DISABLED_SUFFIX
    )
    if target.exists():
        raise ModError(
            f"Cannot toggle {mod.name}: '{target.name}' already exists. "
            "Resolve the duplicate file first."
        )
    try:
        source.rename(target)
    except OSError as exc:
        raise ModError(
            f"Could not {'enable' if enabled else 'disable'} {mod.name}: {exc}. "
            "Close the game if it is running."
        ) from exc
    if log:
        log(f"{'Enabled' if enabled else 'Disabled'} {mod.rel}")
    return replace(
        mod,
        path=target,
        enabled=enabled,
        rel=mod.rel,
    )


def apply_profile(game_root: Path, profile: Profile, log=None) -> tuple[int, list[str]]:
    """Make the loader folders match *profile*. Returns (changes, problems)."""
    changed = 0
    problems: list[str] = []
    for mod in scan(game_root):
        desired = profile.is_enabled(mod.mod_id)
        if mod.enabled == desired:
            continue
        try:
            set_enabled(mod, desired, log)
            changed += 1
        except ModError as exc:
            problems.append(str(exc))
    return changed, problems


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _ensure_mod_dir(game_root: Path, dir_name: str) -> Path:
    canonical = {name.casefold(): name for name in MOD_DIRS}
    folder = canonical.get(dir_name.casefold(), "Mods")
    directory = game_root / folder
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _import_dll(
    source: Path,
    game_root: Path,
    log,
    *,
    target_dir: str = "Mods",
    author: str | None = None,
    origin: str | None = None,
    report: ImportReport,
) -> None:
    if source.name.casefold() in ("melonloader.dll",):
        raise ModError(
            "That file is the mod loader itself. Use 'Install / repair loader' instead."
        )
    directory = _ensure_mod_dir(game_root, target_dir)
    destination = directory / source.name
    if destination.exists():
        try:
            if destination.stat().st_size == source.stat().st_size and destination.read_bytes() == source.read_bytes():
                log(f"Already installed, skipped: {source.name}")
                report.skipped.append(f"{directory.name}/{source.name}")
                return
        except OSError:
            pass
        backup.create_backup(
            f"Before replacing {source.name}", "mod-import", game_root, [destination], log
        )
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        raise ModError(f"Could not copy {source.name}: {exc}") from exc
    meta = _filename_metadata(source.stem)
    _write_sidecar(
        destination,
        name=str(meta["name"]),
        version=meta.get("version"),
        author=author or meta.get("author"),
        origin=origin,
    )
    report.installed.append(f"{directory.name}/{source.name}")
    log(f"Added {directory.name}/{source.name}")


def _plan_staging(staging: Path, package_name: str) -> tuple[list[tuple[Path, str, ModKind | None]], list[str]]:
    """Map archive files to game-relative destinations."""
    plan: list[tuple[Path, str, ModKind | None]] = []
    warnings: list[str] = []
    used: set[str] = set()
    # The manifest name becomes a path segment, so it must be one safe segment.
    # Strip dots to rule out ".", ".." and traversal tricks from hostile zips.
    safe_package = re.sub(r"[^A-Za-z0-9._ -]+", "-", package_name)
    safe_package = re.sub(r"\.{2,}", ".", safe_package).strip(" .-")[:80] or "Mod"

    for file in sorted(staging.rglob("*"), key=lambda p: str(p).casefold()):
        if not file.is_file():
            continue
        parts = list(file.relative_to(staging).parts)
        lowered = [part.casefold() for part in parts]
        if any(part.startswith("__macosx") for part in lowered) or lowered[-1] in (".ds_store", "thumbs.db"):
            continue
        if len(parts) == 1 and lowered[0] in ROOT_DOCS:
            continue
        if "bepinex" in lowered:
            warnings.append(f"Skipped BepInEx file: {'/'.join(parts)} (Schedule I mods use MelonLoader).")
            continue

        top_index = next((i for i, part in enumerate(lowered) if part in TOP_DIR_MAP), None)
        kind: ModKind | None = None
        if top_index is not None:
            canonical, kind = TOP_DIR_MAP[lowered[top_index]]
            rest = parts[top_index + 1 :]
            if not rest:
                continue
            if canonical == "UserData":
                if rest[0].casefold() != safe_package.casefold():
                    relative = f"UserData/{safe_package}/" + "/".join(rest)
                else:
                    relative = "UserData/" + "/".join(rest)
            else:
                relative = f"{canonical}/" + "/".join(rest)
            if kind is not None and not relative.casefold().endswith(".dll"):
                kind = None
        else:
            suffix = file.suffix.casefold()
            if suffix == ".dll":
                relative = f"Mods/{file.name}"
                kind = ModKind.MOD
            elif suffix in CONFIG_SUFFIXES:
                relative = f"UserData/{safe_package}/{file.name}"
            else:
                relative = f"UserData/{safe_package}/assets/{file.relative_to(staging).as_posix()}"
        relative = _unique_rel(relative.replace("\\", "/"), used)
        plan.append((file, relative, kind))
    return plan, warnings


def _is_loader_archive(zip_path: Path) -> bool:
    import zipfile

    try:
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                lowered = name.replace("\\", "/").casefold()
                if lowered.startswith("melonloader/") or lowered.endswith("/melonloader/melonloader.dll"):
                    return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def import_archive(
    zip_path: Path,
    game_root: Path,
    log,
    *,
    author: str | None = None,
    origin: str | None = None,
) -> ImportReport:
    """Import a Thunderstore-style mod ZIP into the correct loader folders."""
    if not zip_path.is_file():
        raise ModError(f"Archive not found: {zip_path}")
    if _is_loader_archive(zip_path):
        raise ModError(
            "That archive contains MelonLoader itself. Use 'Install / repair loader' "
            "instead of adding it as a mod."
        )
    manifest = manifest_member(zip_path)
    package_name = str(manifest.get("name") or zip_path.stem)
    version = str(manifest.get("version_number")) if manifest.get("version_number") else None
    dependencies = manifest.get("dependencies") if isinstance(manifest.get("dependencies"), list) else []
    description = str(manifest.get("description")) if manifest.get("description") else None

    report = ImportReport()
    staging = staging_dir()
    try:
        try:
            extract_zip(zip_path, staging)
        except ArchiveError as exc:
            raise ModError(f"Could not read {zip_path.name}: {exc}") from exc
        plan, warnings = _plan_staging(staging, package_name)
        report.warnings.extend(warnings)
        if not plan:
            raise ModError(
                f"'{zip_path.name}' contains no .dll or config files that can be installed."
            )

        targets = [game_root / relative for _, relative, _ in plan]
        existing = [target for target in targets if target.is_file()]
        if existing:
            try:
                backup.create_backup(
                    f"Before importing {package_name}", "mod-import", game_root, existing, log
                )
            except backup.BackupError as exc:
                raise ModError(f"Import cancelled because the backup failed: {exc}") from exc

        sidecars: list[tuple[Path, ModKind]] = []
        for source, relative, kind in plan:
            destination = game_root / relative
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            except OSError as exc:
                raise ModError(f"Could not write {relative}: {exc}") from exc
            report.installed.append(relative)
            if kind is not None and destination.suffix.casefold() == ".dll":
                sidecars.append((destination, kind))

        for destination, _kind in sidecars:
            _write_sidecar(
                destination,
                name=package_name,
                version=version,
                author=author,
                dependencies=dependencies,
                description=description,
                origin=origin or zip_path.name,
            )
            log(f"Added {destination.relative_to(game_root).as_posix()}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return report


def import_paths(
    paths: list[Path],
    game_root: Path,
    log,
    *,
    target_dir: str = "Mods",
    origin: str | None = None,
) -> ImportReport:
    """Import a mix of ``.dll`` and ``.zip`` files chosen by the user."""
    report = ImportReport()
    if not paths:
        return report
    for path in paths:
        suffix = path.suffix.casefold()
        try:
            if suffix == ".zip":
                partial = import_archive(path, game_root, log, origin=origin or path.name)
                report.installed.extend(partial.installed)
                report.skipped.extend(partial.skipped)
                report.warnings.extend(partial.warnings)
            elif suffix == ".dll":
                _import_dll(
                    path,
                    game_root,
                    log,
                    target_dir=target_dir,
                    origin=origin,
                    report=report,
                )
            else:
                report.skipped.append(path.name)
                report.warnings.append(
                    f"Unsupported file type: {path.name} (only .dll and .zip are supported)."
                )
        except ModError as exc:
            report.warnings.append(f"{path.name}: {exc}")
    if not report.installed:
        raise ModError(
            "No mods were installed.\n" + "\n".join(report.warnings or [])
        )
    return report


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


def remove_mod(mod: ModFile, game_root: Path, log) -> None:
    """Delete a mod (and its sidecar) after taking a backup."""
    sidecar = sidecar_for(mod.path)
    try:
        backup.create_backup(
            f"Before deleting {mod.name}",
            "mod-delete",
            game_root,
            [mod.path, sidecar],
            log,
        )
        mod.path.unlink(missing_ok=True)
        if sidecar.is_file():
            sidecar.unlink(missing_ok=True)
    except (OSError, backup.BackupError) as exc:
        raise ModError(f"Could not remove {mod.name}: {exc}") from exc
    log(f"Removed {mod.rel}")


# ---------------------------------------------------------------------------
# Backend compatibility (IL2CPP vs Mono)
# ---------------------------------------------------------------------------


def mod_backend_hint(mod: ModFile) -> str | None:
    """Guess which Unity backend a mod DLL was built for.

    IL2CPP mods reference ``Il2Cpp*`` assemblies; Mono mods reference
    ``Assembly-CSharp``. Returns ``"il2cpp"``, ``"mono"`` or ``None`` if unclear.
    """
    if mod.path.suffix.casefold() != ".dll" or not mod.path.is_file():
        return None
    try:
        data = mod.path.read_bytes()
    except OSError:
        return None
    if b"Il2Cpp" in data:
        return "il2cpp"
    if b"Assembly-CSharp" in data:
        return "mono"
    return None


def backend_mismatch(mod: ModFile, game_backend: str) -> bool:
    """True when a mod's detected backend contradicts the installed game."""
    hint = mod_backend_hint(mod)
    return hint is not None and game_backend in ("il2cpp", "mono") and hint != game_backend


# ---------------------------------------------------------------------------
# Profile import / export
# ---------------------------------------------------------------------------

PROFILE_FORMAT = "schedule-i-launcher-profile"


def export_profile(profile: Profile, path: Path) -> None:
    """Write a profile (name, disabled mods, save slot) to a JSON file."""
    payload = {
        "format": PROFILE_FORMAT,
        "version": 1,
        "name": profile.name,
        "disabled": sorted(profile.disabled),
        "save_slot": profile.save_slot,
    }
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        raise ModError(f"Could not write profile: {exc}") from exc


def read_profile_file(path: Path) -> Profile:
    """Parse a profile JSON file produced by :func:`export_profile`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModError(f"Could not read profile file: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != PROFILE_FORMAT:
        raise ModError("This file is not a profile exported by the launcher.")
    name = str(data.get("name") or "").strip()
    if not name:
        raise ModError("The profile file has no name.")
    disabled = data.get("disabled")
    if not isinstance(disabled, list) or not all(isinstance(item, str) for item in disabled):
        raise ModError("The profile file has an invalid mod list.")
    save_slot = data.get("save_slot")
    return Profile(
        name=name,
        disabled=set(disabled),
        save_slot=save_slot if isinstance(save_slot, str) and save_slot else None,
    )


# ---------------------------------------------------------------------------
# Temporary loader disable
# ---------------------------------------------------------------------------


def loader_proxy_files(game_root: Path) -> list[Path]:
    """Proxy DLLs in the game root that MelonLoader uses to hook the game."""
    from .loader import PROXY_DLLS

    found: list[Path] = []
    for name in PROXY_DLLS:
        path = game_root / name
        if path.is_file():
            found.append(path)
        disabled = game_root / (name + DISABLED_SUFFIX)
        if disabled.is_file():
            found.append(disabled)
    return found


def loader_disabled(game_root: Path) -> bool:
    """True when the loader proxy exists only in its ``.disabled`` form."""
    from .loader import PROXY_DLLS

    has_disabled = any((game_root / (n + DISABLED_SUFFIX)).is_file() for n in PROXY_DLLS)
    has_active = any((game_root / n).is_file() for n in PROXY_DLLS)
    return has_disabled and not has_active


def set_loader_enabled(game_root: Path, enabled: bool, log=None) -> None:
    """Rename the loader proxy DLLs to disable or re-enable MelonLoader.

    Nothing is deleted; the files are only renamed.
    """
    from .loader import PROXY_DLLS

    for name in PROXY_DLLS:
        active = game_root / name
        disabled = game_root / (name + DISABLED_SUFFIX)
        source, target = (disabled, active) if enabled else (active, disabled)
        if not source.is_file():
            continue
        if target.exists():
            raise ModError(f"Cannot toggle the loader: '{target.name}' already exists.")
        try:
            source.rename(target)
        except OSError as exc:
            raise ModError(f"Could not {'enable' if enabled else 'disable'} the loader: {exc}") from exc
    if log:
        log("Loader enabled." if enabled else "Loader disabled; mods will not load.")


def open_mods_folder(game_root: Path) -> Path:
    for name in MOD_DIRS:
        directory = game_root / name
        if directory.is_dir():
            return directory
    directory = game_root / "Mods"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
