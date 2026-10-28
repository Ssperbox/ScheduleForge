"""HTTPS helpers with host allow-lists, size caps and optional SHA-256 checks.

No telemetry: every request is made only because the user asked for a loader
download or a mod search/install. Nothing is sent anywhere else.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .models import APP_NAME, APP_VERSION

USER_AGENT = f"{APP_NAME}/{APP_VERSION} (local desktop mod manager)"
DEFAULT_TIMEOUT = 25


class NetworkError(Exception):
    """User-presentable network failure."""


def _check_url(url: str, allowed_hosts: tuple[str, ...] | None) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise NetworkError("Only HTTPS downloads are allowed.")
    if not parsed.hostname:
        raise NetworkError("Malformed download URL.")
    if allowed_hosts:
        host = parsed.hostname.casefold()
        if not any(
            host == allowed or host.endswith("." + allowed)
            for allowed in (h.casefold() for h in allowed_hosts)
        ):
            raise NetworkError(f"Refusing to contact unexpected host: {host}")


def _open(
    url: str,
    *,
    allowed_hosts: tuple[str, ...] | None,
    timeout: int = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
):
    _check_url(url, allowed_hosts)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/zip, */*",
            **(headers or {}),
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NetworkError("Not found (HTTP 404).") from exc
        if exc.code == 429:
            raise NetworkError(
                "Rate limited by the server (HTTP 429). Wait a few minutes and retry."
            ) from exc
        raise NetworkError(f"Server returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(
            f"Could not connect: {exc.reason}. Check your internet connection."
        ) from exc
    except TimeoutError as exc:
        raise NetworkError("The server took too long to respond.") from exc

    final_url = response.geturl()
    try:
        _check_url(final_url, allowed_hosts)
    except NetworkError:
        response.close()
        raise
    return response


def http_json(
    url: str,
    *,
    allowed_hosts: tuple[str, ...] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = 40 * 1024**2,
):
    """Fetch and decode JSON with a size cap."""
    response = _open(url, allowed_hosts=allowed_hosts, timeout=timeout)
    try:
        length = response.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise NetworkError("The server response is larger than expected.")
        raw = response.read(max_bytes + 1)
    except OSError as exc:
        raise NetworkError(f"Download interrupted: {exc}") from exc
    finally:
        response.close()
    if len(raw) > max_bytes:
        raise NetworkError("The server response is larger than expected.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise NetworkError("The server returned invalid JSON.") from exc


def download_file(
    url: str,
    destination: Path,
    *,
    allowed_hosts: tuple[str, ...] | None = None,
    max_bytes: int = 250 * 1024**2,
    expected_size: int | None = None,
    sha256: str | None = None,
    timeout: int = 45,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Stream a URL to *destination* atomically, verifying size and hash."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    received = 0
    response = _open(url, allowed_hosts=allowed_hosts, timeout=timeout)
    try:
        length_header = response.headers.get("Content-Length")
        total = int(length_header) if length_header and length_header.isdigit() else None
        if total and total > max_bytes:
            raise NetworkError("The file is larger than the allowed download size.")
        if expected_size and total and total != expected_size:
            raise NetworkError(
                f"Server reported {total} bytes but {expected_size} were expected."
            )
        with open(temporary, "wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise NetworkError("Download exceeded the allowed size.")
                digest.update(chunk)
                handle.write(chunk)
                if log and total and total > 0:
                    percent = int(received * 100 / total)
                    if percent in (25, 50, 75) and received % (256 * 1024) == 0:
                        log(f"  … {percent}%")
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise NetworkError(f"Download failed: {exc}") from exc
    finally:
        response.close()

    if expected_size and received != expected_size:
        temporary.unlink(missing_ok=True)
        raise NetworkError(
            f"Download size mismatch ({received} bytes, expected {expected_size})."
        )
    if sha256:
        actual = digest.hexdigest()
        if actual.casefold() != sha256.casefold():
            temporary.unlink(missing_ok=True)
            raise NetworkError(
                "SHA-256 checksum mismatch; the download was discarded. "
                "The file may be corrupt or tampered with."
            )
        if log:
            log("SHA-256 checksum verified.")
    else:
        if log:
            log("No published checksum available; verified origin and size only.")
    try:
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise NetworkError(f"Could not save the download: {exc}") from exc
    return destination


def safe_filename(text: str, default: str = "download") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._+-]+", "-", text).strip("-.")
    return cleaned[:120] or default
