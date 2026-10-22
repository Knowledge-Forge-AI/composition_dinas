"""Structural comparison of a composition lock with its exact core lock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ProvenanceError(ValueError):
    """Raised when a lock graph cannot satisfy the provenance contract."""


def _resolve(lock: dict[str, Any], reference: object) -> str:
    if isinstance(reference, str):
        if reference not in lock["nodes"]:
            raise ProvenanceError(f"lock references missing node {reference!r}")
        return reference
    if not isinstance(reference, list) or not reference:
        raise ProvenanceError(f"unsupported lock input reference: {reference!r}")
    current = lock["root"]
    for segment in reference:
        if not isinstance(segment, str):
            raise ProvenanceError(f"non-string follows segment: {segment!r}")
        inputs = lock["nodes"][current].get("inputs", {})
        if segment not in inputs:
            raise ProvenanceError(f"follows path cannot resolve {segment!r}")
        current = _resolve(lock, inputs[segment])
    return current


def _reachable(lock: dict[str, Any], start: str) -> set[str]:
    reached: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        for reference in lock["nodes"][current].get("inputs", {}).values():
            pending.append(_resolve(lock, reference))
    return reached


def _fingerprint(lock: dict[str, Any], name: str, active: set[str]) -> object:
    if name in active:
        raise ProvenanceError("lock graph contains a cycle")
    node = lock["nodes"][name]
    children = {
        input_name: _fingerprint(lock, _resolve(lock, reference), active | {name})
        for input_name, reference in sorted(node.get("inputs", {}).items())
    }
    return {"locked": node.get("locked"), "inputs": children}


def _input_fingerprints(lock: dict[str, Any], name: str) -> dict[str, object]:
    return {
        input_name: _fingerprint(lock, _resolve(lock, reference), {name})
        for input_name, reference in sorted(lock["nodes"][name].get("inputs", {}).items())
    }


def _is_absolute_machine_path(node: dict[str, Any]) -> bool:
    locked = node.get("locked", {})
    return locked.get("type") == "path" and str(locked.get("path", "")).startswith("/")


def classify(
    composition: dict[str, Any], core: dict[str, Any], core_input: str
) -> dict[str, object]:
    for document, label in ((composition, "composition"), (core, "core")):
        if document.get("version") != 7 or document.get("root") not in document.get("nodes", {}):
            raise ProvenanceError(f"{label} is not a supported version-7 lock graph")
    root_inputs = composition["nodes"][composition["root"]].get("inputs", {})
    if core_input not in root_inputs:
        raise ProvenanceError(f"composition root has no {core_input!r} input")
    core_node = _resolve(composition, root_inputs[core_input])
    core_reachable = _reachable(composition, core_node) - {core_node}
    owned_reachable: set[str] = set()
    for input_name, reference in root_inputs.items():
        if input_name != core_input:
            owned_reachable |= _reachable(composition, _resolve(composition, reference))
    composition_owned = owned_reachable - core_reachable
    for name in composition_owned:
        if _is_absolute_machine_path(composition["nodes"][name]):
            raise ProvenanceError(f"composition-owned node {name!r} uses an absolute path")
    matches = _input_fingerprints(composition, core_node) == _input_fingerprints(core, core["root"])
    return {
        "core_graph_matches": matches,
        "core_only": sorted(core_reachable - owned_reachable),
        "shared": sorted(core_reachable & owned_reachable),
        "composition_owned": sorted(composition_owned),
    }


def load_lock(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read lock graph {path}: {error}") from error
    if not isinstance(document, dict):
        raise ProvenanceError(f"lock graph {path} must be a JSON object")
    return document
