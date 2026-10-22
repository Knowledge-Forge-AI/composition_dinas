"""Shared registry contract loading, schema validation, and URL normalization."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from urllib.parse import urlsplit


REGISTRY_SCHEMA = "nix-darwin-composition-registry-v2"
TOP_LEVEL_KEYS = frozenset({"schema", "modules"})
ENTRY_KEYS = frozenset({"name", "input", "url", "releaseRef", "checkout", "bootstrap"})
REPO_TOOL_KEYS = frozenset({"strategy", "executable", "config"})
COPY_DEFAULT_KEYS = frozenset({"strategy", "source", "config"})
NONE_KEYS = frozenset({"strategy"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REF_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RegistryError(ValueError):
    """Raised when registry document or entry violates the contract."""


def _exact_keys(value: object, expected: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        if missing:
            raise RegistryError(f"{label} is missing required key: {missing[0]}")
        extra = sorted(actual - expected)
        raise RegistryError(f"{label} has an invalid key: {extra[0]}")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise RegistryError(f"{label} must be a portable identifier")
    return value


def normalize_ref(value: object, label: str = "release ref") -> str:
    """Normalize a short branch or full head ref to one canonical full head ref."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise RegistryError(f"{label} must be a branch head ref")
    short = value.removeprefix("refs/heads/")
    if value.startswith("refs/") and not value.startswith("refs/heads/"):
        raise RegistryError(f"{label} must be below refs/heads")
    if not short or short == "@" or short.startswith("refs/"):
        raise RegistryError(f"{label} must name a branch below refs/heads")
    if any(token in short for token in ("..", "@{", "//", "\\")):
        raise RegistryError(f"{label} contains an ambiguous separator")
    if any(ord(char) < 32 or ord(char) == 127 for char in short):
        raise RegistryError(f"{label} contains a control character")
    if any(char in short for char in " ~^:?*["):
        raise RegistryError(f"{label} contains a forbidden ref character")
    components = short.split("/")
    if any(
        not component
        or REF_COMPONENT.fullmatch(component) is None
        or component == "HEAD"
        or component.startswith(".")
        or component.endswith(".")
        or component.endswith(".lock")
        for component in components
    ):
        raise RegistryError(f"{label} is not a normalized branch head ref")
    return f"refs/heads/{short}"


def _release_ref(value: object, label: str) -> str:
    normalized = normalize_ref(value, label)
    if value != normalized:
        raise RegistryError(f"{label} must use full refs/heads/... form")
    return normalized


def _relative_path(value: object, label: str, prefix: tuple[str, ...] = ()) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RegistryError(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise RegistryError(f"{label} must be a portable relative path")
    if prefix and path.parts[: len(prefix)] != prefix:
        raise RegistryError(f"{label} must be below {'/'.join(prefix)}")
    return value


def normalize_url(value: object, label: str = "repository URL") -> str:
    """Normalize ssh/https git URL to canonical scheme://authority/path form."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise RegistryError(f"{label} must be a canonical repository URL")
    url = value
    if url.startswith("git@") and ":" in url:
        authority, path = url.split(":", 1)
        url = f"ssh://{authority}/{path}"
    elif url.startswith(("git+ssh://", "git+https://")):
        url = url.removeprefix("git+")
    parsed = urlsplit(url)
    if parsed.scheme not in ("ssh", "https") or not parsed.netloc or not parsed.path:
        raise RegistryError(f"{label} must be an ssh or https repository URL")
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{parsed.scheme}://{parsed.netloc.casefold()}{path}"


def _bootstrap(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("strategy"), str):
        raise RegistryError(f"{label} must declare a strategy")
    strategy = value["strategy"]
    expected = (
        REPO_TOOL_KEYS
        if strategy == "repo-tool"
        else COPY_DEFAULT_KEYS
        if strategy == "copy-default"
        else NONE_KEYS
        if strategy == "none"
        else None
    )
    if expected is None:
        raise RegistryError(f"{label} has an unsupported strategy: {strategy}")
    selected = _exact_keys(value, expected, label)
    if strategy == "none":
        return selected
    _relative_path(selected["config"], f"{label}.config", ("config",))
    if strategy == "repo-tool":
        _relative_path(selected["executable"], f"{label}.executable")
    else:
        _relative_path(selected["source"], f"{label}.source", ("config", "default"))
    return selected


def load_registry(path: Path) -> list[dict[str, Any]]:
    """Load and validate the composition registry document from disk."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RegistryError(f"unable to read registry: {path}") from error
    selected = _exact_keys(document, TOP_LEVEL_KEYS, "registry")
    if selected["schema"] != REGISTRY_SCHEMA:
        raise RegistryError("registry schema is unsupported")
    modules = selected["modules"]
    if not isinstance(modules, list) or not modules:
        raise RegistryError("registry.modules must be a non-empty array")

    entries: list[dict[str, Any]] = []
    unique: dict[str, set[str]] = {
        "name": set(),
        "input": set(),
        "checkout": set(),
        "url": set(),
    }
    for index, raw_entry in enumerate(modules):
        label = f"registry.modules[{index}]"
        entry = _exact_keys(raw_entry, ENTRY_KEYS, label)
        values = {
            "name": _identifier(entry["name"], f"{label}.name"),
            "input": _identifier(entry["input"], f"{label}.input"),
            "checkout": _relative_path(entry["checkout"], f"{label}.checkout"),
            "url": normalize_url(entry["url"], f"{label}.url"),
            "releaseRef": _release_ref(entry["releaseRef"], f"{label}.releaseRef"),
        }
        if "/" in values["checkout"]:
            raise RegistryError(f"{label}.checkout must be one path segment")
        for field, value in values.items():
            if field == "releaseRef":
                continue
            if value in unique[field]:
                raise RegistryError(f"registry contains a duplicate {field}: {value}")
            unique[field].add(value)
        bootstrap = _bootstrap(entry["bootstrap"], f"{label}.bootstrap")
        if (
            bootstrap["strategy"] != "none"
            and bootstrap["config"] != f"config/{values['name']}.toml"
        ):
            raise RegistryError(
                f"{label}.bootstrap.config must match the registered module name"
            )
        if (
            bootstrap["strategy"] == "copy-default"
            and bootstrap["source"] != f"config/default/{values['name']}.toml"
        ):
            raise RegistryError(
                f"{label}.bootstrap.source must match the registered module name"
            )
        entries.append(entry)
    return entries


def get_module_by_input(entries: list[dict[str, Any]], input_name: str) -> dict[str, Any] | None:
    """Find a registered module entry by its flake input name."""
    for entry in entries:
        if entry.get("input") == input_name:
            return entry
    return None


def get_module_by_name(entries: list[dict[str, Any]], module_name: str) -> dict[str, Any] | None:
    """Find a registered module entry by its registered module name."""
    for entry in entries:
        if entry.get("name") == module_name:
            return entry
    return None
