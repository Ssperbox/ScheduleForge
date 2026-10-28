"""Client for Thunderstore's public package API (Schedule I community).

Endpoint used: ``GET https://thunderstore.io/c/schedule-i/api/v1/package/``
This is the community-scoped v1 listing. The public API is not a formally
versioned contract, so parsing is defensive: unknown fields are ignored and a
malformed package is skipped rather than crashing the app.

The client caches the listing (15 minutes) to avoid repeated large requests.
Downloads are explicit user actions and go directly to thunderstore.io; the
launcher never reuploads or bundles third-party mods.
"""

from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from .archive import manifest_member
from .config import cache_dir
from .models import ResolvedPackage, ThunderstorePackage, ThunderstoreVersion
from .net import NetworkError, download_file, http_json, safe_filename

LOGGER = logging.getLogger(__name__)

COMMUNITY = "schedule-i"
API_INDEX_URL = f"https://thunderstore.io/c/{COMMUNITY}/api/v1/package/"
COMMUNITY_URL = f"https://thunderstore.io/c/{COMMUNITY}/"
CACHE_SECONDS = 15 * 60
MAX_INDEX_BYTES = 80 * 1024**2
MAX_PACKAGE_BYTES = 150 * 1024**2
LOADER_PACKAGE_NAMES = {"melonloader", "bepinexpack", "bepinex"}

LogFn = Callable[[str], None]


class ThunderstoreError(Exception):
    """User-presentable Thunderstore problem."""


def _noop(_: str) -> None:
    return None


def _version_key(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _parse_version(
    raw: dict, namespace: str, package_name: str
) -> ThunderstoreVersion | None:
    if not isinstance(raw, dict):
        return None
    download_url = raw.get("download_url")
    version_number = str(raw.get("version_number") or "").strip()
    if not isinstance(download_url, str) or not version_number:
        return None
    if download_url.startswith("/"):
        download_url = "https://thunderstore.io" + download_url
    dependencies = raw.get("dependencies")
    deps = tuple(str(item) for item in dependencies) if isinstance(dependencies, list) else ()
    try:
        file_size = int(raw["file_size"]) if raw.get("file_size") else None
    except (TypeError, ValueError):
        file_size = None
    return ThunderstoreVersion(
        name=str(raw.get("name") or f"{package_name}-{version_number}"),
        full_name=str(raw.get("full_name") or f"{namespace}-{package_name}-{version_number}"),
        version_number=version_number,
        description=str(raw.get("description") or ""),
        dependencies=deps,
        download_url=download_url,
        file_size=file_size,
        website_url=str(raw.get("website_url") or ""),
    )


def _parse_package(raw: dict) -> ThunderstorePackage | None:
    if not isinstance(raw, dict):
        return None
    namespace = str(raw.get("namespace") or raw.get("owner") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not namespace or not name:
        return None
    raw_versions = raw.get("versions") if isinstance(raw.get("versions"), list) else []
    versions = [
        parsed
        for version in raw_versions
        if (parsed := _parse_version(version, namespace, name)) is not None
    ]
    if not versions:
        return None
    versions.sort(key=lambda item: _version_key(item.version_number), reverse=True)
    latest = versions[0]
    try:
        downloads = int(raw["total_downloads"]) if raw.get("total_downloads") is not None else None
    except (TypeError, ValueError):
        downloads = None
    return ThunderstorePackage(
        namespace=namespace,
        name=name,
        full_name=str(raw.get("full_name") or f"{namespace}-{name}"),
        description=str(raw.get("description") or latest.description),
        latest=latest,
        versions=tuple(versions),
        total_downloads=downloads,
        is_deprecated=bool(raw.get("is_deprecated", False)),
    )


def split_download_origin(origin: str | None) -> tuple[str, str] | None:
    """Extract ``(namespace, name)`` from a Thunderstore download URL.

    Returns ``None`` for anything that is not a Thunderstore package download.
    """
    if not origin:
        return None
    match = re.search(r"/package/download/([^/]+)/([^/]+)/", origin)
    if not match:
        return None
    return match.group(1), match.group(2)


def split_dependency(dependency: str) -> tuple[str, str, str] | None:
    """Split ``Namespace-Package-1.2.3`` into its three parts."""
    text = dependency.strip()
    if not text:
        return None
    pieces = text.rsplit("-", 2)
    if len(pieces) != 3:
        return None
    namespace, name, version = pieces
    if not namespace or not name or not version:
        return None
    return namespace, name, version


class ThunderstoreClient:
    """Small, cache-aware client. Safe to call from a worker thread."""

    def __init__(self, cache_directory: Path | None = None) -> None:
        self._cache_directory = cache_directory or (cache_dir() / "thunderstore")
        self._memory: list[ThunderstorePackage] | None = None

    # -- index -------------------------------------------------------------

    @property
    def cache_file(self) -> Path:
        return self._cache_directory / f"index-{COMMUNITY}.json"

    def _load_cache(self) -> tuple[list[ThunderstorePackage], float] | None:
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        raw_packages = payload.get("packages") if isinstance(payload, dict) else None
        if not isinstance(raw_packages, list):
            return None
        packages = [pkg for raw in raw_packages if (pkg := _parse_package(raw)) is not None]
        fetched_at = float(payload.get("fetched_at", 0)) if isinstance(payload, dict) else 0.0
        return packages, fetched_at

    def _save_cache(self, raw_packages: list, fetched_at: float) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps({"fetched_at": fetched_at, "packages": raw_packages}),
                encoding="utf-8",
            )
        except OSError:
            LOGGER.warning("Could not cache the Thunderstore index", exc_info=True)

    def fetch_packages(
        self, *, force: bool = False, log: LogFn = _noop
    ) -> list[ThunderstorePackage]:
        if self._memory is not None and not force:
            return self._memory
        cached = self._load_cache()
        if cached and not force and (time.time() - cached[1]) < CACHE_SECONDS:
            self._memory = cached[0]
            return self._memory

        try:
            log("Fetching the Thunderstore Schedule I package list…")
            payload = http_json(
                API_INDEX_URL,
                allowed_hosts=("thunderstore.io",),
                max_bytes=MAX_INDEX_BYTES,
                timeout=35,
            )
            if not isinstance(payload, list):
                raise ThunderstoreError("Thunderstore returned an unexpected package list.")
            packages = [
                parsed
                for raw in payload
                if (parsed := _parse_package(raw)) is not None
            ]
            if not packages:
                raise ThunderstoreError("No packages could be parsed from the Thunderstore index.")
            self._save_cache(payload, time.time())
            self._memory = packages
            log(f"Loaded {len(packages)} Thunderstore packages.")
            return packages
        except (NetworkError, ThunderstoreError) as exc:
            if cached:
                log(f"Offline: using the cached Thunderstore list. ({exc})")
                self._memory = cached[0]
                return self._memory
            raise ThunderstoreError(
                f"Could not load Thunderstore packages: {exc}"
            ) from exc

    # -- search ------------------------------------------------------------

    def search(self, query: str, limit: int = 100, log: LogFn = _noop) -> list[ThunderstorePackage]:
        packages = [
            package for package in self.fetch_packages(log=log) if not package.is_deprecated
        ]
        needle = query.strip().casefold()
        if needle:
            def score(package: ThunderstorePackage) -> int:
                full = package.full_name.casefold()
                description = package.description.casefold()
                if full.startswith(needle):
                    return 0
                if needle in full:
                    return 1
                if needle in description:
                    return 2
                return 9

            packages = [p for p in packages if score(p) < 9]
            packages.sort(
                key=lambda p: (score(p), -(p.total_downloads or 0), p.full_name.casefold())
            )
        else:
            packages.sort(key=lambda p: (-(p.total_downloads or 0), p.full_name.casefold()))
        return packages[:limit]

    # -- dependencies ------------------------------------------------------

    def resolve(
        self,
        package: ThunderstorePackage,
        version: ThunderstoreVersion | None = None,
        log: LogFn = _noop,
    ) -> tuple[list[ResolvedPackage], list[str], list[str]]:
        """Resolve dependencies depth-first, dependencies before dependents.

        Returns ``(ordered, missing, notes)``. Loader packages are *not* installed
        as mods; they are reported in ``notes`` so the UI can offer to install
        MelonLoader itself.
        """
        packages = self.fetch_packages(log=log)
        by_key = {
            (item.namespace.casefold(), item.name.casefold()): item for item in packages
        }
        ordered: list[ResolvedPackage] = []
        missing: list[str] = []
        notes: list[str] = []
        done: set[str] = set()
        visiting: set[str] = set()

        def version_for(item: ThunderstorePackage, wanted: str) -> ThunderstoreVersion:
            for candidate in item.versions:
                if candidate.version_number.casefold() == wanted.casefold():
                    return candidate
            return item.latest

        def visit(
            item: ThunderstorePackage,
            chosen: ThunderstoreVersion,
            requested_by: str | None,
            depth: int = 0,
        ) -> None:
            key = item.full_name.casefold()
            if key in done or depth > 12:
                return
            if key in visiting:  # dependency cycle
                return
            visiting.add(key)
            for dependency in chosen.dependencies:
                parsed = split_dependency(dependency)
                if parsed is None:
                    missing.append(dependency)
                    continue
                namespace, name, wanted = parsed
                if name.casefold() in LOADER_PACKAGE_NAMES:
                    notes.append(
                        f"{item.full_name} expects {dependency}. Install MelonLoader with the "
                        "loader button; it is not added as a mod."
                    )
                    continue
                dependency_package = by_key.get((namespace.casefold(), name.casefold()))
                if dependency_package is None:
                    missing.append(dependency)
                    continue
                visit(dependency_package, version_for(dependency_package, wanted), item.full_name, depth + 1)
            visiting.discard(key)
            done.add(key)
            ordered.append(ResolvedPackage(item, chosen, requested_by))

        visit(package, version or package.latest, None)
        return ordered, missing, notes

    # -- updates -----------------------------------------------------------

    def check_updates(
        self, installed: list[tuple[str, str, str, str]], log: LogFn = _noop
    ) -> dict[str, str]:
        """Map ``mod_id`` -> latest version for installed Thunderstore mods.

        ``installed`` holds ``(mod_id, namespace, name, installed_version)``.
        Only mods whose installed version is strictly older are returned.
        """
        packages = self.fetch_packages(log=log)
        by_key = {
            (item.namespace.casefold(), item.name.casefold()): item for item in packages
        }
        updates: dict[str, str] = {}
        for mod_id, namespace, name, current in installed:
            package = by_key.get((namespace.casefold(), name.casefold()))
            if package is None or not current:
                continue
            latest = package.latest.version_number
            if _version_key(latest) > _version_key(current):
                updates[mod_id] = latest
        return updates

    # -- download ----------------------------------------------------------

    def download(
        self, package: ThunderstorePackage, version: ThunderstoreVersion, log: LogFn = _noop
    ) -> Path:
        directory = self._cache_directory / "downloads"
        destination = directory / (
            safe_filename(f"{package.namespace}-{package.name}-{version.version_number}") + ".zip"
        )
        if destination.is_file() and (
            version.file_size is None or destination.stat().st_size == version.file_size
        ):
            log(f"Using cached download for {package.full_name} {version.version_number}.")
            return destination
        log(f"Downloading {package.full_name} {version.version_number}…")
        try:
            download_file(
                version.download_url,
                destination,
                allowed_hosts=("thunderstore.io",),
                max_bytes=MAX_PACKAGE_BYTES,
                expected_size=version.file_size,
                log=log,
            )
        except NetworkError as exc:
            raise ThunderstoreError(f"Download failed for {package.full_name}: {exc}") from exc

        # Origin/metadata check: the ZIP manifest should identify this package.
        try:
            with zipfile.ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise ThunderstoreError("The downloaded ZIP is corrupt.")
        except (OSError, zipfile.BadZipFile) as exc:
            destination.unlink(missing_ok=True)
            raise ThunderstoreError(f"The downloaded file is not a valid ZIP: {exc}") from exc
        manifest = manifest_member(destination)
        if manifest:
            manifest_name = str(manifest.get("name", "")).casefold()
            manifest_version = str(manifest.get("version_number", "")).casefold()
            if manifest_name and manifest_name not in (package.name.casefold(), package.full_name.casefold()):
                destination.unlink(missing_ok=True)
                raise ThunderstoreError(
                    "The downloaded package manifest does not match the requested mod; "
                    "the download was discarded."
                )
            if manifest_version and manifest_version != version.version_number.casefold():
                destination.unlink(missing_ok=True)
                raise ThunderstoreError(
                    "The downloaded package version does not match the request; "
                    "the download was discarded."
                )
        return destination
