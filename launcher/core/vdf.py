"""Minimal, tolerant Valve KeyValues (VDF) parser.

Used for ``libraryfolders.vdf`` and ``appmanifest_*.acf``. It supports the two
shapes Steam uses today:

* legacy values: ``"1" "D:\\SteamLibrary"``
* modern objects: ``"1" { "path" "D:\\SteamLibrary" ... }``

Valve does not publish a stable schema for these local files, so the parser is
intentionally forgiving: unknown syntax is skipped instead of raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TreeNode = dict[str, Any]


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char in " \t\r\n":
            i += 1
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "/":
            newline = text.find("\n", i)
            if newline == -1:
                break
            i = newline + 1
            continue
        if char in "{}":
            tokens.append(char)
            i += 1
            continue
        if char == '"':
            i += 1
            buffer: list[str] = []
            while i < length and text[i] != '"':
                if text[i] == "\\" and i + 1 < length:
                    escape = text[i + 1]
                    buffer.append(
                        {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(
                            escape, escape
                        )
                    )
                    i += 2
                else:
                    buffer.append(text[i])
                    i += 1
            i += 1  # closing quote
            tokens.append("".join(buffer))
            continue
        # Bare token (some tools omit quotes around keys).
        start = i
        while i < length and text[i] not in ' \t\r\n{}"':
            i += 1
        if i > start:
            tokens.append(text[start:i])
        else:  # pragma: no cover - defensive, avoids an infinite loop
            i += 1
    return tokens


def _parse(tokens: list[str], index: int = 0) -> tuple[TreeNode, int]:
    result: TreeNode = {}
    key: str | None = None
    while index < len(tokens):
        token = tokens[index]
        if token == "}":
            return result, index + 1
        if key is None:
            key = token
            index += 1
            continue
        if token == "{":
            value, index = _parse(tokens, index + 1)
            result[key] = value
        else:
            result[key] = token
            index += 1
        key = None
    return result, index


def parse(text: str) -> TreeNode:
    """Parse VDF/ACF text into nested dictionaries."""
    data, _ = _parse(_tokenize(text))
    return data


def read(path: Path) -> TreeNode:
    """Read and parse a VDF file, returning an empty tree when unreadable."""
    try:
        return parse(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def get_ci(data: TreeNode, key: str, default: Any = None) -> Any:
    """Case-insensitive dictionary lookup (Steam mixes key casing)."""
    if key in data:
        return data[key]
    lowered = key.lower()
    for candidate, value in data.items():
        if str(candidate).lower() == lowered:
            return value
    return default


def iter_library_paths(tree: TreeNode) -> list[str]:
    """Extract library paths from a parsed ``libraryfolders.vdf``.

    Handles both the legacy string form and the modern ``{ "path": ... }`` form
    without assuming anything about the outer numeric keys.
    """
    container = get_ci(tree, "libraryfolders", tree)
    if not isinstance(container, dict):
        return []
    paths: list[str] = []
    for key, value in container.items():
        if str(key).lower() == "contentstatsid":
            continue
        if isinstance(value, dict):
            path = get_ci(value, "path")
            if isinstance(path, str) and path.strip():
                paths.append(path.strip())
        elif isinstance(value, str) and value.strip():
            # Legacy shape. Skip a bare number, which is a stats id, not a path.
            if len(value) > 1 and (":" in value or "\\" in value or "/" in value):
                paths.append(value.strip())
    return paths
