from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import sys
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from lock_transition import (
    LockTransitionError,
    NEW_DEVELOPER_REV,
    OLD_DEVELOPER_REV,
    PETDEX_NODES,
    analyze_transition,
    load_registered_refs,
    update_lock_transactionally,
)
from registry_contract import load_registry


# This is the historical release-channel/Petdex transition, before the editor
# ownership cut. Its registry is intentionally independent of current registration.
HISTORICAL_REGISTERED_REFS = {
    "agent_dinas": "refs/heads/main",
    "agentic-praxis-grimoire-nd": "refs/heads/main",
    "developer-ux-nd": "refs/heads/deploy",
    "entertainment-nd": "refs/heads/main",
    "vscode-nd": "refs/heads/main",
}


def lock_pair() -> tuple[bytes, bytes]:
    follows = {"nix-darwin": ["core", "nix-darwin"], "nixpkgs": ["core", "nixpkgs"]}
    root_inputs = {
        "agent_dinas": "agent",
        "agentic-praxis-grimoire-nd": "apg",
        "core": "core",
        "developer-ux-nd": "dev",
        "entertainment-nd": "ent",
        "localConfig": "local",
        "vscode-nd": "vscode",
    }
    nodes: dict[str, object] = {
        "root": {"inputs": root_inputs},
        "core": {"locked": {"rev": "1"}},
        "local": {"locked": {"path": "./config/default", "type": "path"}},
    }
    for key in ("agent", "apg", "ent", "vscode"):
        nodes[key] = {
            "inputs": follows,
            "locked": {"type": "git", "url": f"ssh://example/{key}.git", "rev": key},
            "original": {"type": "git", "url": f"ssh://example/{key}.git"},
        }
    nodes["dev"] = {
        "inputs": {**follows, **{name: name for name in PETDEX_NODES}},
        "locked": {"type": "git", "url": "ssh://example/dev.git", "rev": OLD_DEVELOPER_REV, "ref": "refs/heads/main"},
        "original": {"type": "git", "url": "ssh://example/dev.git"},
    }
    for name in PETDEX_NODES:
        nodes[name] = {"locked": {"type": "file", "url": f"https://example/{name}"}}
    old = {"nodes": nodes, "root": "root", "version": 7}
    new = json.loads(json.dumps(old))
    for name in PETDEX_NODES:
        del new["nodes"][name]
    for key in ("agent", "apg", "ent", "vscode"):
        new["nodes"][key]["original"]["ref"] = "main"
        new["nodes"][key]["locked"]["ref"] = "main"
    new["nodes"]["dev"]["inputs"] = follows
    new["nodes"]["dev"]["locked"].update({"rev": NEW_DEVELOPER_REV, "ref": "deploy"})
    new["nodes"]["dev"]["original"]["ref"] = "deploy"
    return json.dumps(old, sort_keys=True).encode(), json.dumps(new, sort_keys=True).encode()


class LockTransitionTests(unittest.TestCase):
    def test_registered_refs_derive_directly_from_registry(self):
        derived = load_registered_refs(ROOT / "registry.json")
        expected = {
            entry["input"]: entry["releaseRef"]
            for entry in load_registry(ROOT / "registry.json")
        }
        self.assertEqual(derived, expected)
        self.assertEqual(derived["developer-ux_dinas"], "refs/heads/deploy")

    def test_exact_candidate_transition_accepts_petdex_removal_and_main_metadata(self):
        old, new = lock_pair()
        report = analyze_transition(old, new, registered_refs=HISTORICAL_REGISTERED_REFS)
        self.assertEqual(set(report["removedNodes"]), PETDEX_NODES)
        self.assertEqual(report["developerUx"]["toRevision"], NEW_DEVELOPER_REV)
        self.assertEqual(
            report["metadataOnlyInputs"],
            ["agent_dinas", "agentic-praxis-grimoire-nd", "entertainment-nd", "vscode-nd"],
        )

    def test_root_version_follows_and_unrelated_node_drift_reject(self):
        old, new = lock_pair()
        for label, mutate in (
            ("version", lambda value: value.update({"version": 8})),
            ("root", lambda value: value["nodes"]["root"]["inputs"].update({"core": "other"})),
            ("follows", lambda value: value["nodes"]["dev"]["inputs"].update({"nixpkgs": "moved"})),
            ("outside", lambda value: value["nodes"]["core"]["locked"].update({"rev": "2"})),
        ):
            with self.subTest(label=label):
                candidate = json.loads(new)
                mutate(candidate)
                with self.assertRaises(LockTransitionError):
                    analyze_transition(
                        old, json.dumps(candidate).encode(),
                        registered_refs=HISTORICAL_REGISTERED_REFS,
                    )

    def test_unexpected_mutation_restores_exact_preimage(self):
        old, new = lock_pair()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / "flake.lock"
            lock_path.write_bytes(old)

            def fake_runner(command, **kwargs):
                self.assertEqual(command, ["nix", "flake", "update", "developer-ux-nd"])
                lock_path.write_bytes(new.replace(b'"version": 7', b'"version": 8'))
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with self.assertRaises(LockTransitionError):
                update_lock_transactionally(
                    root, root.parent / f"{root.name}-report.json", fake_runner
                )
            self.assertEqual(lock_path.read_bytes(), old)
            report_path = root.parent / f"{root.name}-report.json"
            self.assertFalse(report_path.exists())

    def test_report_must_be_outside_repository(self):
        old, _ = lock_pair()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "flake.lock").write_bytes(old)
            with self.assertRaisesRegex(LockTransitionError, "outside"):
                update_lock_transactionally(root, root / "report.json")


if __name__ == "__main__":
    unittest.main()
