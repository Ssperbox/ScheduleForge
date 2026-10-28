"""Archive handling with protection against path traversal ("zip slip").

Every ZIP the launcher touches (MelonLoader releases, mod packages, backups)
goes through :func:`extract_zip`. The rules are deliberately strict:

* absolute paths, drive letters and ``..`` segments are rejected;
* symlinks and other non-regular entries are skipped;
* total uncompressed size and entry count are capped to limit zip bombs;
* extraction is staged in a temporary directory first, always.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

MAX_TOTAL_BYTES = 4 * 1024**3  # 4 GiB per archive
MAX_ENTRIES = 20_000
MAX_SINGLE_FILE_BYTES = 1 * 1024**3

# Unix mode bits stored in the upper 16 bits of ZipInfo.external_attr.
_SYMLINK_MODE = 0o120000


class ArchiveError(Exception):
    """Raised when an archive is invalid, unsafe or too large."""


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == _SYMLINK_MODE


def safe_member_path(root: Path, member_name: str) -> Path:
    """Map a ZIP member name to a path under *root*, rejecting escapes.

    ZIP names always use forward slashes, but some tools insert backslashes or
    Windows drive letters. Both are normalised and validated here.
    """
    raw = member_name.replace("\\", "/").lstrip("/")
    if not raw or raw.endswith("/"):
        raise ArchiveError(f"Invalid archive entry: {member_name!r}")
    if "\x00" in raw:
        raise ArchiveError("Archive contains a NUL byte in a file name.")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in ("..", "") for part in pure.parts):
        raise ArchiveError(f"Unsafe archive entry (path traversal): {member_name!r}")
    # Reject drive letters and NTFS alternate data streams anywhere in the path.
    if any(":" in part for part in pure.parts):
        raise ArchiveError(f"Unsafe archive entry (drive/ADS): {member_name!r}")
    target = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:  # pragma: no cover - belt and braces
        raise ArchiveError(f"Unsafe archive entry: {member_name!r}") from exc
    return target


def extract_zip(
    zip_path: Path,
    destination: Path,
    *,
    strip_prefix: str = "",
    only_under: str | None = None,
    skip: Iterable[str] = (),
) -> list[Path]:
    """Safely extract *zip_path* into *destination*.

    ``strip_prefix`` removes a leading directory (with or without a trailing
    slash). ``only_under`` restricts extraction to one subtree (used by backup
    restores, which must never write their manifest into the target folder).
    ``skip`` holds top-level names to ignore.
    """
    destination.mkdir(parents=True, exist_ok=True)
    skipped = {name.lower() for name in skip}
    written: list[Path] = []
    total_bytes = 0

    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError(f"Not a valid ZIP archive: {zip_path.name} ({exc})") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ArchiveError(
                f"Archive has too many entries ({len(infos)} > {MAX_ENTRIES})."
            )
        prefix = strip_prefix.replace("\\", "/").strip("/")
        if prefix:
            prefix += "/"
        required = only_under.replace("\\", "/").strip("/") + "/" if only_under else None
        for info in infos:
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if required and not name.startswith(required):
                continue
            if prefix and name.startswith(prefix):
                name = name[len(prefix) :]
            if not name:
                continue
            if name.split("/", 1)[0].lower() in skipped:
                continue
            if _is_symlink(info):
                continue
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                raise ArchiveError(f"Archive entry is too large: {info.filename}")
            total_bytes += info.file_size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ArchiveError("Archive expands to more than the allowed size.")
            target = safe_member_path(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink, length=1024 * 256)
            except (OSError, RuntimeError) as exc:
                raise ArchiveError(
                    f"Could not extract {info.filename!r}: {exc}"
                ) from exc
            written.append(target)
    return written


def zip_contains(zip_path: Path, predicate) -> bool:
    """Return True if any member name satisfies *predicate* (case-insensitive)."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return any(predicate(info.filename.replace("\\", "/").lower()) for info in archive.infolist())
    except (OSError, zipfile.BadZipFile):
        return False


def manifest_member(zip_path: Path) -> dict:
    """Read a Thunderstore ``manifest.json`` from the archive root, if present.

    Returns an empty dict when it does not exist or cannot be decoded. Never
    raises, because metadata is optional for manually built mod zips.
    """
    import json

    try:
        with zipfile.ZipFile(zip_path) as archive:
            candidates = [
                info
                for info in archive.infolist()
                if info.filename.replace("\\", "/").lower() == "manifest.json"
            ]
            if not candidates:
                return {}
            with archive.open(candidates[0]) as handle:
                data = json.loads(handle.read(1_000_000).decode("utf-8", "replace"))
            return data if isinstance(data, dict) else {}
    except (OSError, zipfile.BadZipFile, ValueError):
        return {}


def staging_dir(prefix: str = "s1ml-") -> Path:
    """Create a temp directory that the caller must clean up."""
    return Path(tempfile.mkdtemp(prefix=prefix))
