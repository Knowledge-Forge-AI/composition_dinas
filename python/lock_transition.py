"""Exact semantic validation for the consumed release-channel lock migration.

Retained for historical fixtures; its frozen developer-ux-nd identity does not
support the current registry. Use the maintained structural bootstrap/source
workflow for repository renames and qualify-promotion for ordinary promotions.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable

sys.dont_write_bytecode = True


from registry_contract import load_registry

REPORT_SCHEMA = "composition-nd-release-channel-lock-delta-v1"
DEVELOPER_INPUT = "developer-ux-nd"
OLD_DEVELOPER_REV = "fe191eafee1dab96f7b42c91c62929dc9a8e9419"
NEW_DEVELOPER_REV = "c0f23ddc6df8845c2880b9263f0939180fe7e466"
PETDEX_NODES = frozenset({
    "petdex-cli-release",
    "petdex-desktop-release",
    "petdex-desktop-dmg",
})


def load_registered_refs(registry_path: Path | None = None) -> dict[str, str]:
    """Derive registered input release-ref mapping from registry contract."""
    if registry_path is None:
        registry_path = Path(__file__).resolve().parents[1] / "registry.json"
    entries = load_registry(registry_path)
    return {entry["input"]: entry["releaseRef"] for entry in entries}


class LockTransitionError(ValueError):
    """Raised when a generated lock differs outside the authorized transition."""


def _load(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise LockTransitionError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), dict):
        raise LockTransitionError(f"{label} is not a lock object")
    return value


def _without_ref(node: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(node))
    for field in ("locked", "original"):
        selected = value.get(field)
        if isinstance(selected, dict):
            selected.pop("ref", None)
    return value


def _without_keys(value: object, keys: set[str]) -> object:
    selected = json.loads(json.dumps(value))
    if isinstance(selected, dict):
        for key in keys:
            selected.pop(key, None)
    return selected


def _require_ref(node: dict[str, Any], full_ref: str, label: str) -> None:
    short_ref = full_ref.removeprefix("refs/heads/")
    for field in ("original", "locked"):
        value = node.get(field, {}).get("ref")
        normalized = full_ref if value in {short_ref, full_ref} else None
        if normalized != full_ref:
            raise LockTransitionError(
                f"{label}.{field}.ref does not normalize to {full_ref!r}"
            )


def analyze_transition(
    old_bytes: bytes,
    new_bytes: bytes,
    registered_refs: dict[str, str] | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Return a report only when the complete normalized transition is allowed."""
    if registered_refs is None:
        registered_refs = load_registered_refs(registry_path)
    if DEVELOPER_INPUT not in registered_refs:
        raise LockTransitionError(f"developer input {DEVELOPER_INPUT!r} missing from release refs")

    old = _load(old_bytes, "entry lock")
    new = _load(new_bytes, "candidate lock")
    if old.get("version") != new.get("version"):
        raise LockTransitionError("lock version changed")
    if old.get("root") != new.get("root"):
        raise LockTransitionError("lock root identity changed")

    old_nodes = old["nodes"]
    new_nodes = new["nodes"]
    root = old["root"]
    if old_nodes.get(root) != new_nodes.get(root):
        raise LockTransitionError("root node or root input mapping changed")
    root_inputs = old_nodes[root].get("inputs", {})
    if not isinstance(root_inputs, dict):
        raise LockTransitionError("root input mapping is invalid")

    old_keys = set(old_nodes)
    new_keys = set(new_nodes)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    if added:
        raise LockTransitionError(f"unexpected lock nodes added: {added}")
    if set(removed) != PETDEX_NODES:
        raise LockTransitionError(
            f"removed lock nodes do not equal obsolete Petdex nodes: {removed}"
        )

    direct_keys: dict[str, str] = {}
    for input_name, release_ref in registered_refs.items():
        node_key = root_inputs.get(input_name)
        if not isinstance(node_key, str) or node_key not in old_nodes or node_key not in new_nodes:
            raise LockTransitionError(f"registered input {input_name!r} is not stable")
        direct_keys[input_name] = node_key
        _require_ref(new_nodes[node_key], release_ref, input_name)

    developer_key = direct_keys[DEVELOPER_INPUT]
    old_developer = old_nodes[developer_key]
    new_developer = new_nodes[developer_key]
    if old_developer.get("locked", {}).get("rev") != OLD_DEVELOPER_REV:
        raise LockTransitionError("entry Developer UX revision is unexpected")
    if new_developer.get("locked", {}).get("rev") != NEW_DEVELOPER_REV:
        raise LockTransitionError("candidate Developer UX revision is unexpected")
    old_original = old_developer.get("original")
    new_original = new_developer.get("original")
    if _without_keys(old_original, {"ref"}) != _without_keys(new_original, {"ref"}):
        raise LockTransitionError("Developer UX original identity changed beyond ref")
    old_locked = old_developer.get("locked")
    new_locked = new_developer.get("locked")
    mutable_locked_fields = {"lastModified", "narHash", "ref", "rev", "revCount"}
    if _without_keys(old_locked, mutable_locked_fields) != _without_keys(
        new_locked, mutable_locked_fields
    ):
        raise LockTransitionError("Developer UX locked identity changed beyond candidate metadata")
    if not isinstance(old_locked, dict) or not isinstance(new_locked, dict):
        raise LockTransitionError("Developer UX locked identity is invalid")
    if set(old_locked) != set(new_locked):
        raise LockTransitionError("Developer UX locked field shape changed")
    expected_follows = {
        "nix-darwin": ["core", "nix-darwin"],
        "nixpkgs": ["core", "nixpkgs"],
    }
    if new_developer.get("inputs") != expected_follows:
        raise LockTransitionError("Developer UX inputs are not the exact core follows")
    old_developer_inputs = set(old_developer.get("inputs", {}))
    if old_developer_inputs - set(expected_follows) != PETDEX_NODES:
        raise LockTransitionError("entry Developer UX Petdex edges are unexpected")

    metadata_only: list[str] = []
    for input_name, node_key in direct_keys.items():
        if input_name == DEVELOPER_INPUT:
            continue
        if _without_ref(old_nodes[node_key]) != _without_ref(new_nodes[node_key]):
            raise LockTransitionError(f"{input_name} changed beyond explicit-ref metadata")
        metadata_only.append(input_name)

    allowed_changed = set(direct_keys.values())
    for node_key in sorted(old_keys & new_keys):
        if node_key in allowed_changed:
            continue
        if old_nodes[node_key] != new_nodes[node_key]:
            raise LockTransitionError(f"unrelated lock node changed: {node_key}")

    changed = sorted(
        key for key in old_keys & new_keys if old_nodes[key] != new_nodes[key]
    )
    return {
        "schema": REPORT_SCHEMA,
        "outcome": "pass",
        "entrySha256": hashlib.sha256(old_bytes).hexdigest(),
        "candidateSha256": hashlib.sha256(new_bytes).hexdigest(),
        "lockVersion": new["version"],
        "root": root,
        "addedNodes": added,
        "removedNodes": removed,
        "changedNodes": changed,
        "metadataOnlyInputs": sorted(metadata_only),
        "developerUx": {
            "fromRevision": OLD_DEVELOPER_REV,
            "toRevision": NEW_DEVELOPER_REV,
            "releaseRef": registered_refs[DEVELOPER_INPUT],
            "removedInputEdges": sorted(PETDEX_NODES),
        },
    }


def update_lock_transactionally(
    root: Path,
    report_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    """Run the sole narrow update and restore exact bytes after any unexpected result."""
    lock_path = root / "flake.lock"
    old_bytes = lock_path.read_bytes()
    old_mode = lock_path.stat().st_mode
    report_resolved = report_path.resolve()
    try:
        report_resolved.relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise LockTransitionError("semantic lock report must be outside the repository")
    try:
        completed = runner(
            ["nix", "flake", "update", DEVELOPER_INPUT],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise LockTransitionError(
                f"narrow lock update failed with exit status {completed.returncode}"
            )
        new_bytes = lock_path.read_bytes()
        report = analyze_transition(old_bytes, new_bytes)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except BaseException:
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as temporary:
            temporary.write(old_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.chmod(temporary_path, old_mode)
        os.replace(temporary_path, lock_path)
        if lock_path.read_bytes() != old_bytes:
            raise LockTransitionError("exact lock restoration could not be proven")
        raise

    return report
