import sys
sys.dont_write_bytecode = True

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lock_provenance", ROOT / "python" / "lock_provenance.py"
)
PROVENANCE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PROVENANCE)


def node(identity, inputs=None):
    return {
        "locked": {"type": "git", "url": f"ssh://example/{identity}", "rev": identity},
        "inputs": inputs or {},
    }


class LockProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.core = {
            "version": 7,
            "root": "root",
            "nodes": {
                "root": {"inputs": {"nixpkgs": "nixpkgs", "legacy": "legacy"}},
                "nixpkgs": node("nixpkgs"),
                "legacy": node("legacy", {"leaf": "leaf"}),
                "leaf": node("leaf"),
            },
        }
        self.composition = {
            "version": 7,
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core", "vscode-nd": "vscode"}},
                "core": node("core", {"nixpkgs": "nixpkgs", "legacy": "legacy"}),
                "nixpkgs": node("nixpkgs"),
                "legacy": node("legacy", {"leaf": "leaf"}),
                "leaf": node("leaf"),
                "vscode": node("vscode", {"nixpkgs": ["core", "nixpkgs"]}),
            },
        }

    def test_classifies_exact_embedded_core_graph_and_shared_nodes(self):
        result = PROVENANCE.classify(self.composition, self.core, "core")
        self.assertTrue(result["core_graph_matches"])
        self.assertEqual(result["core_only"], ["leaf", "legacy"])
        self.assertEqual(result["shared"], ["nixpkgs"])
        self.assertEqual(result["composition_owned"], ["vscode"])

    def test_changed_inherited_identity_fails(self):
        self.composition["nodes"]["legacy"]["locked"]["rev"] = "changed"
        result = PROVENANCE.classify(self.composition, self.core, "core")
        self.assertFalse(result["core_graph_matches"])

    def test_direct_machine_path_is_rejected(self):
        self.composition["nodes"]["vscode"]["locked"] = {
            "type": "path",
            "path": "/Users/operator/vscode-nd",
        }
        with self.assertRaises(PROVENANCE.ProvenanceError):
            PROVENANCE.classify(self.composition, self.core, "core")


if __name__ == "__main__":
    unittest.main()
