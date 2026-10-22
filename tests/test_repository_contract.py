from __future__ import annotations
import sys
sys.dont_write_bytecode = True

import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEX_40 = re.compile(r"^[0-9a-f]{40}$")

# Stable canonical core repository constants
CORE_DECLARATION_URL = "git+ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"
CORE_LOCK_URL = "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git"


def extract_core_declaration_url(source: str) -> str:
    """Extract literal direct core.url assignment from flake.nix inputs.

    Rejects missing, duplicate, commented, or multi-assignment matches.
    """
    # Preserve quoted URLs (including # selectors) while removing Nix comments.
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|#[^\n]*', re.DOTALL)
    uncommented = tokens.sub(
        lambda match: match.group(0) if match.group(0).startswith('"') else " ",
        source,
    )
    matches = re.findall(r'^\s*core\.url\s*=\s*"([^"\n]+)"\s*;', uncommented, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(
            f"malformed/unsupported declaration: Expected exactly one active direct core.url assignment in flake.nix, found {len(matches)}: {matches}"
        )
    return matches[0]


def normalize_core_declaration_url(url: str) -> str:
    """Normalize declaration git+ssh:// prefix to ssh:// while preserving .git suffix."""
    if not url.startswith("git+ssh://"):
        raise AssertionError(f"malformed/unsupported declaration: core declaration URL must begin with git+ssh://, got: {url!r}")
    return url.replace("git+ssh://", "ssh://", 1)


def verify_core_repository_contract(flake_source: str, lock: dict[str, Any]) -> tuple[str, str, str]:
    """Verify the canonical core repository and structural lock contract."""
    decl_url = extract_core_declaration_url(flake_source)
    if "?" in decl_url or "#" in decl_url:
        raise AssertionError(
            f"malformed/unsupported declaration: core declaration URL must not contain query/fragment selectors: {decl_url!r}"
        )
    normalized_decl_url = normalize_core_declaration_url(decl_url)
    if decl_url != CORE_DECLARATION_URL:
        raise AssertionError(
            f"wrong canonical repository: core declaration URL must be {CORE_DECLARATION_URL!r}, got: {decl_url!r}"
        )
    if normalized_decl_url != CORE_LOCK_URL:
        raise AssertionError(
            f"lock URL mismatch: normalized declaration URL {normalized_decl_url!r} does not match canonical lock URL {CORE_LOCK_URL!r}"
        )

    if not isinstance(lock, dict) or "nodes" not in lock or "root" not in lock:
        raise AssertionError("Malformed lock: missing 'nodes' or 'root'")
    nodes = lock["nodes"]
    root_node = nodes.get(lock["root"])
    if not isinstance(root_node, dict) or "inputs" not in root_node:
        raise AssertionError("Malformed lock: invalid root node")
    root_inputs = root_node["inputs"]
    if "core" not in root_inputs:
        raise AssertionError("Root lock node inputs missing 'core'")

    core_ref = root_inputs["core"]
    if not isinstance(core_ref, str):
        raise AssertionError(f"Direct string node reference required for 'core', got {type(core_ref).__name__}")
    if core_ref not in nodes:
        raise AssertionError(f"core node {core_ref!r} not found in lock nodes")

    core_node = nodes[core_ref]
    if not isinstance(core_node, dict):
        raise AssertionError(f"core node {core_ref!r} is not a dictionary")

    original = core_node.get("original")
    locked = core_node.get("locked")
    if not isinstance(original, dict) or not isinstance(locked, dict):
        raise AssertionError("core node must declare both 'original' and 'locked' dictionaries")

    if original.get("type") != "git":
        raise AssertionError(f"core original type must be 'git', got {original.get('type')!r}")
    if locked.get("type") != "git":
        raise AssertionError(f"core locked type must be 'git', got {locked.get('type')!r}")

    if "dir" in original:
        raise AssertionError(f"core original must not declare 'dir', got {original['dir']!r}")
    if "ref" in original:
        raise AssertionError(f"core original must not declare 'ref', got {original['ref']!r}")
    if "rev" in original:
        raise AssertionError(f"core original must not declare 'rev', got {original['rev']!r}")

    if "dir" in locked:
        raise AssertionError(f"core locked must not declare 'dir', got {locked['dir']!r}")
    if "ref" in locked and locked["ref"] != "refs/heads/main":
        raise AssertionError(
            f"invalid ref: core locked ref if present must equal 'refs/heads/main', got {locked['ref']!r}"
        )

    orig_url = original.get("url")
    locked_url = locked.get("url")
    if orig_url != normalized_decl_url:
        raise AssertionError(
            f"lock URL mismatch: core original url {orig_url!r} does not match normalized declaration url {normalized_decl_url!r}"
        )
    if locked_url != normalized_decl_url:
        raise AssertionError(
            f"lock URL mismatch: core locked url {locked_url!r} does not match normalized declaration url {normalized_decl_url!r}"
        )

    locked_rev = locked.get("rev")
    if not isinstance(locked_rev, str) or not HEX_40.fullmatch(locked_rev):
        raise AssertionError(
            f"invalid revision shape: core locked rev must be a lowercase 40-character hex revision, got {locked_rev!r}"
        )

    return (decl_url, normalized_decl_url, locked_rev)


class RepositoryContractTests(unittest.TestCase):
    def test_required_composition_surfaces_exist(self):
        for relative in (
            "flake.nix",
            "registry.json",
            "lib/promotion.nix",
            "config/default/flake.nix",
            "config/default/developer-ux.toml",
            "config/default/browser-ux.toml",
            "config/default/agentic-praxis-grimoire.toml",
            "tools/bootstrap",
            "tools/check-inherited-lock-provenance",
            "tools/qualify-promotion",
            "python/registry_contract.py",
            "python/promotion_qualifier.py",
            "python/lock_provenance.py",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_flake_uses_only_canonical_direct_repository_inputs(self):
        source = (ROOT / "flake.nix").read_text(encoding="utf-8")
        decl_url = extract_core_declaration_url(source)
        self.assertEqual(decl_url, CORE_DECLARATION_URL)
        self.assertNotIn('vscode-nd.url = "git+ssh://git@github.com/lair001/vscode-nd.git?ref=main";', source)
        self.assertNotIn('vscode-nd.inputs.nixpkgs.follows = "core/nixpkgs";', source)
        self.assertNotIn(
            'vscode-nd.inputs.nix-darwin.follows = "core/nix-darwin";', source
        )
        self.assertNotIn("agentic-praxis-grimoire-nd", source)
        self.assertIn(
            'agentic-praxis-grimoire_dinas.inputs.nixpkgs.follows = "core/nixpkgs";',
            source,
        )
        self.assertIn(
            'agentic-praxis-grimoire_dinas.inputs.nix-darwin.follows = "core/nix-darwin";',
            source,
        )
        self.assertIn("entertainment-nd.url =", source)
        self.assertIn(
            '"git+ssh://git@github.com/lair001/entertainment-nd.git?ref=main";', source
        )
        self.assertIn(
            'entertainment-nd.inputs.nixpkgs.follows = "core/nixpkgs";', source
        )
        self.assertIn(
            'entertainment-nd.inputs.nix-darwin.follows = "core/nix-darwin";',
            source,
        )
        self.assertIn("developer-ux_dinas.url =", source)
        self.assertIn(
            '"git+ssh://git@github.com/lair001/developer-ux_dinas.git?ref=deploy";', source
        )
        self.assertIn(
            'developer-ux_dinas.inputs.nixpkgs.follows = "core/nixpkgs";', source
        )
        self.assertIn(
            'developer-ux_dinas.inputs.nix-darwin.follows = "core/nix-darwin";',
            source,
        )
        self.assertIn("browser-ux_dinas.url =", source)
        self.assertIn(
            '"git+ssh://git@github.com/Knowledge-Forge-AI/browser-ux_dinas.git?ref=main";',
            source,
        )
        self.assertIn(
            'browser-ux_dinas.inputs.nixpkgs.follows = "core/nixpkgs";', source
        )
        self.assertIn(
            'browser-ux_dinas.inputs.nix-darwin.follows = "core/nix-darwin";',
            source,
        )
        self.assertIn("builtins.map configurationModuleFor configuredRegistry", source)
        self.assertIn("builtins.map projectionAllowlistFor configuredRegistry", source)
        self.assertIn("registryInputsComplete", source)
        self.assertNotIn('localConfig.outPath + "/vscode-nd.toml"', source)
        self.assertNotIn("path:/Users/", source)
        self.assertNotIn("nix-darwin.lib.darwinSystem", source)
        self.assertEqual(source.count("core.lib.mkDarwinConfiguration"), 1)

    def test_application_claims_are_registry_driven_and_validated_once(self):
        source = (ROOT / "flake.nix").read_text(encoding="utf-8")
        self.assertIn('claimsLib.attachOwner "core" core.applicationClaims', source)
        self.assertIn("claimsForInput entry.name inputs.${entry.input}", source)
        self.assertIn("must declare applicationClaims", source)
        self.assertEqual(source.count("claimsLib.assertValid"), 1)
        self.assertEqual(source.count("claimsLib = core.lib.applicationClaims;"), 1)

    def test_registry_is_explicit_versioned_and_contains_registered_modules_once(self):
        registry = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(registry["schema"], "nix-darwin-composition-registry-v2")
        names = [entry["name"] for entry in registry["modules"]]
        self.assertEqual(
            names,
            [
                "agentic-praxis-grimoire",
                "entertainment",
                "developer-ux",
                "browser-ux",
                "agent",
                "virtual-machines",
            ],
        )
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            next(entry for entry in registry["modules"] if entry["name"] == "developer-ux")["bootstrap"],
            {
                "strategy": "copy-default",
                "source": "config/default/developer-ux.toml",
                "config": "config/developer-ux.toml",
            },
        )
        self.assertEqual(
            next(entry for entry in registry["modules"] if entry["name"] == "browser-ux")["bootstrap"],
            {
                "strategy": "copy-default",
                "source": "config/default/browser-ux.toml",
                "config": "config/browser-ux.toml",
            },
        )
        self.assertEqual(
            registry["modules"][0],
            {
                "name": "agentic-praxis-grimoire",
                "input": "agentic-praxis-grimoire_dinas",
                "url": (
                    "git+ssh://git@github.com/"
                    "lair001/agentic-praxis-grimoire_dinas.git"
                ),
                "releaseRef": "refs/heads/main",
                "checkout": "agentic-praxis-grimoire_dinas",
                "bootstrap": {
                    "strategy": "copy-default",
                    "source": (
                        "config/default/agentic-praxis-grimoire.toml"
                    ),
                    "config": "config/agentic-praxis-grimoire.toml",
                },
            },
        )
        self.assertEqual(
            registry["modules"][1],
            {
                "name": "entertainment",
                "input": "entertainment-nd",
                "url": "git+ssh://git@github.com/lair001/entertainment-nd.git",
                "releaseRef": "refs/heads/main",
                "checkout": "entertainment-nd",
                "bootstrap": {"strategy": "none"},
            },
        )
        self.assertEqual(
            registry["modules"][2],
            {
                "name": "developer-ux",
                "input": "developer-ux_dinas",
                "url": "git+ssh://git@github.com/lair001/developer-ux_dinas.git",
                "releaseRef": "refs/heads/deploy",
                "checkout": "developer-ux_dinas",
                "bootstrap": {"strategy": "copy-default", "source": "config/default/developer-ux.toml", "config": "config/developer-ux.toml"},
            },
        )
        self.assertEqual(
            registry["modules"][3],
            {
                "name": "browser-ux",
                "input": "browser-ux_dinas",
                "url": "git+ssh://git@github.com/Knowledge-Forge-AI/browser-ux_dinas.git",
                "releaseRef": "refs/heads/main",
                "checkout": "browser-ux_dinas",
                "bootstrap": {
                    "strategy": "copy-default",
                    "source": "config/default/browser-ux.toml",
                    "config": "config/browser-ux.toml",
                },
            },
        )
        self.assertEqual(
            registry["modules"][4],
            {
                "name": "agent",
                "input": "agent_dinas",
                "url": "git+ssh://git@github.com/lair001/agent_dinas.git",
                "releaseRef": "refs/heads/main",
                "checkout": "agent_dinas",
                "bootstrap": {"strategy": "none"},
            },
        )
        self.assertFalse((ROOT / "config/default/entertainment.toml").exists())
        self.assertFalse((ROOT / "config/entertainment.toml").exists())
        self.assertTrue((ROOT / "config/default/developer-ux.toml").exists())
        self.assertFalse((ROOT / "config/developer-ux.toml").exists())
        self.assertTrue((ROOT / "config/default/browser-ux.toml").exists())
        self.assertFalse((ROOT / "config/browser-ux.toml").exists())

    def test_public_browser_ux_default_has_only_the_consolidated_owner(self):
        default = ROOT / "config/default/browser-ux.toml"
        self.assertEqual(tomllib.loads(default.read_text(encoding="utf-8")), {
            "browser-ux": {
                "schema": 1,
                "keepassxc": {"enable": False},
            },
        })
        self.assertFalse((ROOT / "config/browser-ux.toml").exists())

    def test_public_editor_default_has_only_the_consolidated_owner(self):
        default = ROOT / "config/default/developer-ux.toml"
        self.assertEqual(tomllib.loads(default.read_text(encoding="utf-8")), {
            "developer-ux": {
                "schema": 1,
                "vscode": {"settings": {"delete": [], "values": {}}},
            },
        })
        self.assertFalse((ROOT / "config/default/vscode-nd.toml").exists())
        source = (ROOT / "flake.nix").read_text(encoding="utf-8")
        self.assertNotIn("inputs.vscode-nd", source)
        self.assertIn(
            "inputs.developer-ux_dinas.lib.editorComponents.vscode.applicationClaims",
            source,
        )

    def test_lock_dynamic_structure_and_core_follows(self):
        lock = json.loads((ROOT / "flake.lock").read_text(encoding="utf-8"))
        registry = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
        root_inputs = lock["nodes"][lock["root"]]["inputs"]

        self.assertEqual(set(root_inputs),
                         {"core", "localConfig"} | {entry["input"] for entry in registry["modules"]})
        flake_source = (ROOT / "flake.nix").read_text(encoding="utf-8")
        verify_core_repository_contract(flake_source, lock)
        for node in lock["nodes"].values():
            self.assertNotIn("vscode-nd", node.get("inputs", {}))
            self.assertNotIn("/vscode-nd", node.get("locked", {}).get("url", ""))

        def normalized(url: str) -> str:
            return url.replace("git+ssh://", "ssh://", 1).removesuffix(".git")

        for entry in registry["modules"]:
            input_name = entry["input"]
            self.assertIn(input_name, root_inputs)
            node_key = root_inputs[input_name]
            node = lock["nodes"][node_key]
            self.assertRegex(node["locked"]["rev"], HEX_40)
            self.assertEqual(node["inputs"]["nixpkgs"], ["core", "nixpkgs"])
            self.assertEqual(node["inputs"]["nix-darwin"], ["core", "nix-darwin"])
            self.assertEqual(normalized(entry["url"]), normalized(node["locked"]["url"]))
            self.assertEqual(
                f"refs/heads/{node['original']['ref']}", entry["releaseRef"]
            )
            self.assertEqual(
                f"refs/heads/{node['locked']['ref']}", entry["releaseRef"]
            )

        self.assertEqual(
            {entry["input"]: entry["releaseRef"] for entry in registry["modules"]},
            {
                "agent_dinas": "refs/heads/main",
                "virtual-machines_dinas": "refs/heads/main",
                "agentic-praxis-grimoire_dinas": "refs/heads/main",
                "developer-ux_dinas": "refs/heads/deploy",
                "browser-ux_dinas": "refs/heads/main",
                "entertainment-nd": "refs/heads/main",
            },
        )

    def test_source_does_not_duplicate_leaf_revisions_or_release_identities(self):
        lock = json.loads((ROOT / "flake.lock").read_text(encoding="utf-8"))
        registry = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
        root_inputs = lock["nodes"][lock["root"]]["inputs"]

        locked_revs = [
            lock["nodes"][root_inputs[entry["input"]]]["locked"]["rev"]
            for entry in registry["modules"]
        ]

        # Locked revisions of leaf modules must not appear as literals in flake.nix or tests
        flake_source = (ROOT / "flake.nix").read_text(encoding="utf-8")
        for rev in locked_revs:
            with self.subTest(rev=rev):
                self.assertNotIn(rev, flake_source)

        for test_file in (ROOT / "tests").glob("test_*.py"):
            test_source = test_file.read_text(encoding="utf-8")
            for rev in locked_revs:
                with self.subTest(file=test_file.name, rev=rev):
                    self.assertNotIn(rev, test_source)

        # Leaf-owned release identity assertions must not exist in composition source
        for forbidden_pattern in (
            "apgrUpstream.version",
            "apgrUpstream.commit",
            "apgrUpstream.tree",
            "apgrUpstream.tagObject",
            "apgrUpstream.parent",
            "apgrUpstream.releaseTime",
            "apgrUpstream.corpus",
            "apgrUpstream.embeddedBinary",
            "expectedEntertainmentClaims",
            "expectedDeveloperUxClaims",
            'ownerClaimCount "developer-ux"',
            'ownerClaimCount "entertainment"',
            'ownerClaimCount "core" ==',
        ):
            with self.subTest(pattern=forbidden_pattern):
                self.assertNotIn(forbidden_pattern, flake_source)

    def test_lock_retires_legacy_core_repo_map_deployment_inputs(self):
        lock = json.loads((ROOT / "flake.lock").read_text(encoding="utf-8"))
        nodes = lock["nodes"]
        root_inputs = nodes[lock["root"]]["inputs"]
        core_inputs = nodes[root_inputs["core"]]["inputs"]
        retired_inputs = {
            "repo-map-manager-src",
            "repo-map-orch33-tools-src",
            "repo-map-runner-src",
            "repo-map-tools-src",
        }

        self.assertTrue(retired_inputs.isdisjoint(core_inputs))
        self.assertTrue(retired_inputs.isdisjoint(nodes))
        self.assertIn("repo-map-src", core_inputs)
        self.assertIn(core_inputs["repo-map-src"], nodes)

    def test_public_default_apgr_config_is_quiescent_and_operator_neutral(self):
        source = (
            ROOT / "config/default/agentic-praxis-grimoire.toml"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            source,
            "[agentic-praxis-grimoire]\nschema = 1\nagents = []\n",
        )
        for forbidden in ("/Users/", "operator", "composition-private-nd"):
            self.assertNotIn(forbidden, source)

    def test_public_capable_source_contains_no_operator_private_identifier(self):
        forbidden = (
            b"/Users/" + b"operator",
            b"composition-" + b"private",
            b"private" + b"_dinas",
        )
        surfaces = [
            "README.md",
            "flake.nix",
            "registry.json",
            "lib/promotion.nix",
            "tools/bootstrap",
            "tools/check-inherited-lock-provenance",
            "tools/qualify-promotion",
            "tools/migrate-release-lock",
            "python/registry_contract.py",
            "python/promotion_qualifier.py",
            "python/lock_provenance.py",
            "python/lock_transition.py",
            "config/default/agentic-praxis-grimoire.toml",
            "config/default/developer-ux.toml",
            "config/default/browser-ux.toml",
            "docs/architecture/registry-bootstrap-contract.md",
        ]
        # Preserve the original guard's start date; older historical records
        # contain legacy identifiers and need a separately owned remediation.
        # Recursion covers every subsequent campaign without a date allowlist.
        for path in (ROOT / "docs/status").rglob("*"):
            record_date = path.relative_to(ROOT / "docs/status").parts[:3]
            if path.is_file() and record_date >= ("2026", "08", "26"):
                surfaces.append(str(path.relative_to(ROOT)))
        for path in (ROOT / "docs/architecture").glob("*.md"):
            if path.is_file():
                surfaces.append(str(path.relative_to(ROOT)))

        for relative in surfaces:
            path = ROOT / relative
            if path.is_file():
                payload = path.read_bytes()
                for value in forbidden:
                    with self.subTest(path=relative, value=value):
                        self.assertFalse(value in payload, "private identifier detected")

    def test_bootstrap_stops_short_of_activation(self):
        source = (ROOT / "tools" / "bootstrap").read_text(encoding="utf-8")
        for forbidden in ("darwin-rebuild switch", "nix-update switch", "brew install"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("init-vscode-nd-config", source)
        self.assertNotIn('"--override-input", "localConfig"', source)
        self.assertNotIn("eval(", source)

    def test_qualifier_performs_no_persistent_writes(self):
        for relative in ("tools/qualify-promotion", "python/promotion_qualifier.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("--output", source)
            self.assertNotIn(".write_text(", source)
            self.assertNotIn(".write_bytes(", source)
            # Forbid write/create operations and high-level file modification open calls
            self.assertNotIn("open(", source.replace("Popen(", "").replace("os.open(", "").replace("open_flags", ""))
            self.assertNotIn("os.O_WRONLY", source)
            self.assertNotIn("os.O_RDWR", source)
            self.assertNotIn("os.O_CREAT", source)
            self.assertNotIn("mkdir(", source)
            self.assertNotIn("rmtree(", source)
            self.assertNotIn("unlink(", source)

    def test_python_entrypoints_suppress_bytecode(self):
        for relative in (
            "tools/bootstrap",
            "tools/check-inherited-lock-provenance",
            "tools/qualify-promotion",
            "tools/migrate-release-lock",
            "python/promotion_qualifier.py",
            "python/lock_transition.py",
        ):
            path = ROOT / relative
            if path.is_file():
                source = path.read_text(encoding="utf-8")
                self.assertIn("sys.dont_write_bytecode = True", source)

    def test_apgr_dinas_migration_contract(self):
        lock = json.loads((ROOT / "flake.lock").read_text(encoding="utf-8"))
        registry = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
        root_inputs = lock["nodes"][lock["root"]]["inputs"]

        self.assertNotIn("agentic-praxis-grimoire-nd", root_inputs)
        self.assertNotIn("agentic-praxis-grimoire-nd", lock["nodes"])
        self.assertIn("agentic-praxis-grimoire_dinas", root_inputs)

        apgr_node = lock["nodes"][root_inputs["agentic-praxis-grimoire_dinas"]]
        self.assertEqual(
            apgr_node["locked"]["url"],
            "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
        )
        self.assertEqual(apgr_node["locked"]["ref"], "main")
        self.assertEqual(len(apgr_node["locked"]["rev"]), 40)
        self.assertEqual(apgr_node["inputs"]["nixpkgs"], ["core", "nixpkgs"])
        self.assertEqual(apgr_node["inputs"]["nix-darwin"], ["core", "nix-darwin"])

        apgr_entry = next(
            entry for entry in registry["modules"] if entry["name"] == "agentic-praxis-grimoire"
        )
        self.assertEqual(apgr_entry["input"], "agentic-praxis-grimoire_dinas")
        self.assertEqual(apgr_entry["checkout"], "agentic-praxis-grimoire_dinas")
        self.assertEqual(
            apgr_entry["url"],
            "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
        )
        self.assertEqual(
            apgr_entry["bootstrap"],
            {
                "strategy": "copy-default",
                "source": "config/default/agentic-praxis-grimoire.toml",
                "config": "config/agentic-praxis-grimoire.toml",
            },
        )

    def test_canonical_core_repository_constants(self):
        self.assertEqual(
            CORE_DECLARATION_URL,
            "git+ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        )
        self.assertEqual(
            CORE_LOCK_URL,
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git",
        )
        self.assertEqual(normalize_core_declaration_url(CORE_DECLARATION_URL), CORE_LOCK_URL)

    def test_real_core_lock_passes_canonical_contract(self):
        source = (ROOT / "flake.nix").read_text(encoding="utf-8")
        lock = json.loads((ROOT / "flake.lock").read_text(encoding="utf-8"))
        core_node_name = lock["nodes"][lock["root"]]["inputs"]["core"]
        real_rev = lock["nodes"][core_node_name]["locked"]["rev"]

        # 1. Actual current lock passes
        canonical_triple = verify_core_repository_contract(source, lock)
        self.assertEqual(canonical_triple, (CORE_DECLARATION_URL, CORE_LOCK_URL, real_rev))

        # 2. Canonical declaration/lock passes with locked ref omitted
        without_ref = copy.deepcopy(lock)
        del without_ref["nodes"][core_node_name]["locked"]["ref"]
        self.assertEqual(
            verify_core_repository_contract(source, without_ref),
            (CORE_DECLARATION_URL, CORE_LOCK_URL, real_rev),
        )

        # 3. Canonical declaration/lock passes with ref=refs/heads/main
        with_main_ref = copy.deepcopy(lock)
        with_main_ref["nodes"][core_node_name]["locked"]["ref"] = "refs/heads/main"
        self.assertEqual(
            verify_core_repository_contract(source, with_main_ref),
            (CORE_DECLARATION_URL, CORE_LOCK_URL, real_rev),
        )

        # 4. Canonical declaration with wrapped comments/newlines passes
        wrapped = (
            source.replace('core.url = "', 'core.url = # declaration follows\n    "')
            + " # trailing comment"
        )
        self.assertEqual(
            verify_core_repository_contract(wrapped, lock),
            (CORE_DECLARATION_URL, CORE_LOCK_URL, real_rev),
        )

    def test_real_core_lock_accepts_canonical_future_and_historical_revisions(self):
        source = (ROOT / "flake.nix").read_text(encoding="utf-8")
        lock = json.loads((ROOT / "flake.lock").read_text(encoding="utf-8"))
        core_node_name = lock["nodes"][lock["root"]]["inputs"]["core"]

        candidate_revisions = (
            # Historical core promotion candidate A (entry pin)
            "2b7e617f7aa618e2ad192268202150df12182545",
            # Historical core promotion candidate B
            "f6d7e70e05502c00c31abc6869a3ba1d3ce1ae3f",
            # BUX2 promotion blocker regression revision
            "80094769f76993c7257ccd9e50539f67bf6f6759",
            # Former canonical core revisions
            "640c43eaf1059254b2b057625730a42d7f8969aa",
            "e6f8b5e543f78368ca61ffbdcff56c47af0a3bb2",
            "02ddaafdeceba7a0b000d4ad0b1529aff1119af5",
            # Historical fixture revision when under canonical current core repository
            "49a67af6bdaf00bfab1e626e4d9501bdead393cc",
            # Arbitrary lowercase synthetic 40-hex revisions
            "1" * 40,
            "0123456789abcdef0123456789abcdef01234567",
            "f" * 40,
        )

        for revision in candidate_revisions:
            for ref in (None, "refs/heads/main"):
                with self.subTest(revision=revision, ref=ref):
                    candidate = copy.deepcopy(lock)
                    locked = candidate["nodes"][core_node_name]["locked"]
                    locked["rev"] = revision
                    if ref is None:
                        locked.pop("ref", None)
                    else:
                        locked["ref"] = ref
                    self.assertEqual(
                        verify_core_repository_contract(source, candidate),
                        (CORE_DECLARATION_URL, CORE_LOCK_URL, revision),
                    )

    def test_core_contract_regression_bux2_blocker_revision(self):
        """Explicit regression proof that BUX2 candidate 80094769... passes under canonical repository contract."""
        bux2_rev = "80094769f76993c7257ccd9e50539f67bf6f6759"
        flake_source = f'core.url = "{CORE_DECLARATION_URL}";'
        lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core"}},
                "core": {
                    "original": {"type": "git", "url": CORE_LOCK_URL},
                    "locked": {
                        "type": "git",
                        "url": CORE_LOCK_URL,
                        "rev": bux2_rev,
                        "ref": "refs/heads/main",
                    },
                },
            },
        }
        self.assertEqual(
            verify_core_repository_contract(flake_source, lock),
            (CORE_DECLARATION_URL, CORE_LOCK_URL, bux2_rev),
        )

    def test_core_contract_negative_wrong_canonical_repository(self):
        canonical_flake_source = f'core.url = "{CORE_DECLARATION_URL}";'
        canonical_lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core"}},
                "core": {
                    "original": {"type": "git", "url": CORE_LOCK_URL},
                    "locked": {
                        "type": "git",
                        "url": CORE_LOCK_URL,
                        "rev": "2b7e617f7aa618e2ad192268202150df12182545",
                        "ref": "refs/heads/main",
                    },
                },
            },
        }

        # 1. Historical .flakes repository declaration/lock rejected regardless of revision
        historical_decl_url = "git+ssh://git@github.com/lair001/.flakes.git"
        historical_lock_url = "ssh://git@github.com/lair001/.flakes.git"
        historical_flake_source = f'core.url = "{historical_decl_url}";'

        for rev in (
            "49a67af6bdaf00bfab1e626e4d9501bdead393cc",  # historical rev
            "2b7e617f7aa618e2ad192268202150df12182545",  # current core rev
            "80094769f76993c7257ccd9e50539f67bf6f6759",  # BUX2 blocker rev
            "1" * 40,                                     # synthetic 40-hex rev
        ):
            with self.subTest(historical_flakes_rev=rev):
                hist_lock = copy.deepcopy(canonical_lock)
                hist_lock["nodes"]["core"]["original"]["url"] = historical_lock_url
                hist_lock["nodes"]["core"]["locked"]["url"] = historical_lock_url
                hist_lock["nodes"]["core"]["locked"]["rev"] = rev
                with self.assertRaisesRegex(AssertionError, r"^wrong canonical repository:"):
                    verify_core_repository_contract(historical_flake_source, hist_lock)

        # 2. Mixed declaration/lock: old (.flakes) declaration + canonical lock fails on declaration repository identity
        with self.assertRaisesRegex(AssertionError, r"^wrong canonical repository:"):
            verify_core_repository_contract(historical_flake_source, canonical_lock)

        # 3. Other repository names
        bad_repo_flake = 'core.url = "git+ssh://git@github.com/lair001/other-repo.git";'
        bad_repo_lock = copy.deepcopy(canonical_lock)
        bad_repo_lock["nodes"]["core"]["original"]["url"] = "ssh://git@github.com/lair001/other-repo.git"
        bad_repo_lock["nodes"]["core"]["locked"]["url"] = "ssh://git@github.com/lair001/other-repo.git"
        with self.assertRaisesRegex(AssertionError, r"^wrong canonical repository:"):
            verify_core_repository_contract(bad_repo_flake, bad_repo_lock)

        # 4. Other repository owners
        other_owner_flake = 'core.url = "git+ssh://git@github.com/other-owner/darwin-is-not-a-snowflake.git";'
        other_owner_lock = copy.deepcopy(canonical_lock)
        other_owner_lock["nodes"]["core"]["original"]["url"] = "ssh://git@github.com/other-owner/darwin-is-not-a-snowflake.git"
        other_owner_lock["nodes"]["core"]["locked"]["url"] = "ssh://git@github.com/other-owner/darwin-is-not-a-snowflake.git"
        with self.assertRaisesRegex(AssertionError, r"^wrong canonical repository:"):
            verify_core_repository_contract(other_owner_flake, other_owner_lock)

        # 5. Repository aliases violating exact canonical identity
        for repo_alias in (
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake",
            "ssh://git@github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git/",
        ):
            with self.subTest(repo_alias=repo_alias):
                alt_lock = copy.deepcopy(canonical_lock)
                for section in ("original", "locked"):
                    alt_lock["nodes"]["core"][section]["url"] = repo_alias
                with self.assertRaisesRegex(AssertionError, r"^wrong canonical repository:"):
                    verify_core_repository_contract(f'core.url = "git+{repo_alias}";', alt_lock)

    def test_core_contract_negative_malformed_unsupported_declaration(self):
        canonical_lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core"}},
                "core": {
                    "original": {"type": "git", "url": CORE_LOCK_URL},
                    "locked": {
                        "type": "git",
                        "url": CORE_LOCK_URL,
                        "rev": "2b7e617f7aa618e2ad192268202150df12182545",
                        "ref": "refs/heads/main",
                    },
                },
            },
        }

        # 1. Unsupported transport scheme (must be git+ssh://)
        bad_scheme_flake = 'core.url = "https://github.com/Knowledge-Forge-AI/darwin-is-not-a-snowflake.git";'
        with self.assertRaisesRegex(AssertionError, r"^malformed/unsupported declaration: core declaration URL must begin with git\+ssh://"):
            verify_core_repository_contract(bad_scheme_flake, canonical_lock)

        # 2. Selectors in declaration (?ref=, ?dir=, ?rev=, #anchor)
        for bad_suffix in (
            "?ref=main",
            "?dir=core",
            "?rev=80094769f76993c7257ccd9e50539f67bf6f6759",
            "#main",
        ):
            with self.subTest(bad_suffix=bad_suffix):
                bad_sel_flake = f'core.url = "{CORE_DECLARATION_URL}{bad_suffix}";'
                with self.assertRaisesRegex(AssertionError, r"^malformed/unsupported declaration: core declaration URL must not contain query/fragment selectors"):
                    verify_core_repository_contract(bad_sel_flake, canonical_lock)

        # 3. Missing declaration
        with self.assertRaisesRegex(AssertionError, r"^malformed/unsupported declaration: Expected exactly one active direct core\.url assignment"):
            verify_core_repository_contract("", canonical_lock)

        # 4. Commented declaration
        commented_flake = f'# core.url = "{CORE_DECLARATION_URL}";'
        with self.assertRaisesRegex(AssertionError, r"^malformed/unsupported declaration: Expected exactly one active direct core\.url assignment"):
            verify_core_repository_contract(commented_flake, canonical_lock)

        # 5. Block-commented declaration
        block_commented_flake = f'/* core.url = "{CORE_DECLARATION_URL}"; */'
        with self.assertRaisesRegex(AssertionError, r"^malformed/unsupported declaration: Expected exactly one active direct core\.url assignment"):
            verify_core_repository_contract(block_commented_flake, canonical_lock)

        # 6. Duplicate declaration
        dup_flake = f'core.url = "{CORE_DECLARATION_URL}";\n    core.url = "{CORE_DECLARATION_URL}";'
        with self.assertRaisesRegex(AssertionError, r"^malformed/unsupported declaration: Expected exactly one active direct core\.url assignment.*found 2"):
            verify_core_repository_contract(dup_flake, canonical_lock)

    def test_core_contract_negative_lock_url_mismatch(self):
        canonical_flake_source = f'core.url = "{CORE_DECLARATION_URL}";'
        canonical_lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core"}},
                "core": {
                    "original": {"type": "git", "url": CORE_LOCK_URL},
                    "locked": {
                        "type": "git",
                        "url": CORE_LOCK_URL,
                        "rev": "2b7e617f7aa618e2ad192268202150df12182545",
                        "ref": "refs/heads/main",
                    },
                },
            },
        }

        # 1. Original URL mismatch with historical URL
        mismatched_orig_hist = copy.deepcopy(canonical_lock)
        mismatched_orig_hist["nodes"]["core"]["original"]["url"] = "ssh://git@github.com/lair001/.flakes.git"
        with self.assertRaisesRegex(AssertionError, r"^lock URL mismatch: core original url .* does not match normalized declaration url"):
            verify_core_repository_contract(canonical_flake_source, mismatched_orig_hist)

        # 2. Locked URL mismatch with historical URL
        mismatched_locked_hist = copy.deepcopy(canonical_lock)
        mismatched_locked_hist["nodes"]["core"]["locked"]["url"] = "ssh://git@github.com/lair001/.flakes.git"
        with self.assertRaisesRegex(AssertionError, r"^lock URL mismatch: core locked url .* does not match normalized declaration url"):
            verify_core_repository_contract(canonical_flake_source, mismatched_locked_hist)

        # 3. Arbitrary URL mismatch
        mismatched_orig_arb = copy.deepcopy(canonical_lock)
        mismatched_orig_arb["nodes"]["core"]["original"]["url"] = "ssh://git@github.com/lair001/mismatch.git"
        with self.assertRaisesRegex(AssertionError, r"^lock URL mismatch: core original url .* does not match normalized declaration url"):
            verify_core_repository_contract(canonical_flake_source, mismatched_orig_arb)

        mismatched_locked_arb = copy.deepcopy(canonical_lock)
        mismatched_locked_arb["nodes"]["core"]["locked"]["url"] = "ssh://git@github.com/lair001/mismatch.git"
        with self.assertRaisesRegex(AssertionError, r"^lock URL mismatch: core locked url .* does not match normalized declaration url"):
            verify_core_repository_contract(canonical_flake_source, mismatched_locked_arb)

        # 4. Suffixes on original or locked URL (?ref=main, ?dir=core, ?rev=..., #main)
        for section in ("original", "locked"):
            for suffix in (
                "?ref=main",
                "?dir=core",
                "?rev=80094769f76993c7257ccd9e50539f67bf6f6759",
                "#main",
            ):
                with self.subTest(section=section, suffix=suffix):
                    selected = copy.deepcopy(canonical_lock)
                    selected["nodes"]["core"][section]["url"] += suffix
                    with self.assertRaisesRegex(AssertionError, r"^lock URL mismatch:"):
                        verify_core_repository_contract(canonical_flake_source, selected)

    def test_core_contract_negative_invalid_ref(self):
        canonical_flake_source = f'core.url = "{CORE_DECLARATION_URL}";'
        canonical_lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core"}},
                "core": {
                    "original": {"type": "git", "url": CORE_LOCK_URL},
                    "locked": {
                        "type": "git",
                        "url": CORE_LOCK_URL,
                        "rev": "2b7e617f7aa618e2ad192268202150df12182545",
                        "ref": "refs/heads/main",
                    },
                },
            },
        }

        for bad_ref in ("refs/heads/custom-branch", "refs/tags/v1.0", "main", "refs/heads/master"):
            with self.subTest(bad_ref=bad_ref):
                bad_locked_ref = copy.deepcopy(canonical_lock)
                bad_locked_ref["nodes"]["core"]["locked"]["ref"] = bad_ref
                with self.assertRaisesRegex(AssertionError, r"^invalid ref: core locked ref if present must equal 'refs/heads/main'"):
                    verify_core_repository_contract(canonical_flake_source, bad_locked_ref)

    def test_core_contract_negative_invalid_revision_shape(self):
        canonical_flake_source = f'core.url = "{CORE_DECLARATION_URL}";'
        canonical_lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core"}},
                "core": {
                    "original": {"type": "git", "url": CORE_LOCK_URL},
                    "locked": {
                        "type": "git",
                        "url": CORE_LOCK_URL,
                        "rev": "2b7e617f7aa618e2ad192268202150df12182545",
                        "ref": "refs/heads/main",
                    },
                },
            },
        }

        for revision in (
            "not-a-40-hex-revision",
            "80094769F76993C7257CCD9E50539F67BF6F6759",  # uppercase hex
            "a" * 39,                                     # length 39
            "a" * 41,                                     # length 41
            None,
            42,
        ):
            with self.subTest(malformed_revision=revision):
                bad_hex_lock = copy.deepcopy(canonical_lock)
                bad_hex_lock["nodes"]["core"]["locked"]["rev"] = revision
                with self.assertRaisesRegex(AssertionError, r"^invalid revision shape: core locked rev must be a lowercase 40-character hex revision"):
                    verify_core_repository_contract(canonical_flake_source, bad_hex_lock)

    def test_core_contract_negative_structural_violations(self):
        canonical_flake_source = f'core.url = "{CORE_DECLARATION_URL}";'
        canonical_lock = {
            "root": "root",
            "nodes": {
                "root": {"inputs": {"core": "core"}},
                "core": {
                    "original": {"type": "git", "url": CORE_LOCK_URL},
                    "locked": {
                        "type": "git",
                        "url": CORE_LOCK_URL,
                        "rev": "2b7e617f7aa618e2ad192268202150df12182545",
                        "ref": "refs/heads/main",
                    },
                },
            },
        }

        # 1. Non-dict lock or missing nodes/root
        for malformed in (None, [], {}, {"nodes": {}}, {"root": "root"}):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(AssertionError, "Malformed lock"):
                    verify_core_repository_contract(canonical_flake_source, malformed)

        # 2. Missing core in root inputs
        missing_root_core = copy.deepcopy(canonical_lock)
        del missing_root_core["nodes"]["root"]["inputs"]["core"]
        with self.assertRaisesRegex(AssertionError, "Root lock node inputs missing 'core'"):
            verify_core_repository_contract(canonical_flake_source, missing_root_core)

        # 3. Follows / indirect root core input
        follows_root_core = copy.deepcopy(canonical_lock)
        follows_root_core["nodes"]["root"]["inputs"]["core"] = ["core", "input"]
        with self.assertRaisesRegex(AssertionError, "Direct string node reference required for 'core'"):
            verify_core_repository_contract(canonical_flake_source, follows_root_core)

        # 4. Core node missing from nodes or not dict
        missing_node_lock = copy.deepcopy(canonical_lock)
        del missing_node_lock["nodes"]["core"]
        with self.assertRaisesRegex(AssertionError, "core node 'core' not found in lock nodes"):
            verify_core_repository_contract(canonical_flake_source, missing_node_lock)

        non_dict_node_lock = copy.deepcopy(canonical_lock)
        non_dict_node_lock["nodes"]["core"] = "not-a-dict"
        with self.assertRaisesRegex(AssertionError, "core node 'core' is not a dictionary"):
            verify_core_repository_contract(canonical_flake_source, non_dict_node_lock)

        # 5. Core original or locked not dict
        for key in ("original", "locked"):
            with self.subTest(missing_dict_key=key):
                bad_subdict = copy.deepcopy(canonical_lock)
                del bad_subdict["nodes"]["core"][key]
                with self.assertRaisesRegex(AssertionError, "core node must declare both 'original' and 'locked' dictionaries"):
                    verify_core_repository_contract(canonical_flake_source, bad_subdict)

        # 6. Original type must be 'git'
        bad_orig_type = copy.deepcopy(canonical_lock)
        bad_orig_type["nodes"]["core"]["original"]["type"] = "github"
        with self.assertRaisesRegex(AssertionError, "core original type must be 'git'"):
            verify_core_repository_contract(canonical_flake_source, bad_orig_type)

        # 7. Locked type must be 'git'
        bad_locked_type = copy.deepcopy(canonical_lock)
        bad_locked_type["nodes"]["core"]["locked"]["type"] = "github"
        with self.assertRaisesRegex(AssertionError, "core locked type must be 'git'"):
            verify_core_repository_contract(canonical_flake_source, bad_locked_type)

        # 8. Original with dir, ref, or rev
        for field, val in (("dir", "subdir"), ("ref", "refs/heads/main"), ("rev", "2b7e617f7aa618e2ad192268202150df12182545")):
            with self.subTest(original_field=field):
                bad_orig = copy.deepcopy(canonical_lock)
                bad_orig["nodes"]["core"]["original"][field] = val
                with self.assertRaisesRegex(AssertionError, f"core original must not declare {field!r}"):
                    verify_core_repository_contract(canonical_flake_source, bad_orig)

        # 9. Locked with dir
        bad_locked_dir = copy.deepcopy(canonical_lock)
        bad_locked_dir["nodes"]["core"]["locked"]["dir"] = "subdir"
        with self.assertRaisesRegex(AssertionError, "core locked must not declare 'dir'"):
            verify_core_repository_contract(canonical_flake_source, bad_locked_dir)

    def test_browser_ux_configuration_projection_and_rejections(self):
        if shutil.which("nix") is None:
            self.skipTest("nix executable not available in test environment")
        # 1. Default evaluates cleanly through darwinConfigurations
        out = subprocess.check_output(
            [
                "nix", "eval", "--impure", "--json", "--expr",
                'let flake = builtins.getFlake (toString ./.); in flake.darwinConfigurations.example-workstation.config.browser-ux.configuration'
            ],
            cwd=str(ROOT),
            text=True,
        )
        # Public Browser UX has a larger closed schema than the initial BUX1
        # input. Every replacement/trial remains explicitly inactive by default.
        self.assertEqual(json.loads(out), {
            "schema": 1,
            "keepassxc": {"enable": False},
            "extensions": {
                "default-on-opt-out": [],
                "apple-proton-trial": {
                    "enable": False, "host": "example-current-mac",
                    "vivaldi-vpn-route": "standalone", "integrated-vpn-id": "",
                },
            },
            "replacements": {
                "floorp": {"enable": False, "inherit-from": "firefox"},
                "vivaldi": {
                    "enable": False, "inherit-from": "chrome",
                    "mail": {"enable": False, "preserve-resolved": False},
                },
            },
        })

        # 2. ConfiguredRegistry and moduleConfiguration contain browser-ux
        out_config = subprocess.check_output(
            [
                "nix", "eval", "--impure", "--json", "--expr",
                'let flake = builtins.getFlake (toString ./.); in builtins.hasAttr "browser-ux" flake.darwinConfigurations.example-workstation.config'
            ],
            cwd=str(ROOT),
            text=True,
        )
        self.assertEqual(out_config.strip(), "true")

        # 3. Closed schema rejections
        rejections = {
            "wrong schema": "{ browser-ux.configuration.schema = pkgs.lib.mkForce 2; }",
            "wrong type": "{ browser-ux.configuration.keepassxc.enable = pkgs.lib.mkForce \"not-a-bool\"; }",
            "unknown child key": "{ browser-ux.configuration.keepassxc.unknownKey = true; }",
            "unknown top key": "{ browser-ux.configuration.unknownKey = true; }",
        }
        for desc, override in rejections.items():
            check_expr = f"""
            let
              flake = builtins.getFlake (toString ./.);
              pkgs = flake.inputs.core.legacyPackages.aarch64-darwin;
              host = flake.lib.mkDarwinConfiguration {{
                hostName = "example-workstation";
                extraModules = [ {override} ];
              }};
            in builtins.tryEval (builtins.deepSeq host.config.browser-ux.configuration true)
            """
            res = subprocess.check_output(
                ["nix", "eval", "--impure", "--json", "--expr", check_expr],
                cwd=str(ROOT),
                text=True,
            )
            self.assertIn('"success":false', res, f"Expected {desc} to fail evaluation, got {res}")

    def test_browser_ux_registered_input_checks_and_claims_aggregation(self):
        if shutil.which("nix") is None:
            self.skipTest("nix executable not available in test environment")
        # (a) Verify all browser-ux checks are included in registered-input-checks
        out = subprocess.check_output(
            [
                "nix", "eval", "--impure", "--json", "--expr",
                '''
                let
                  flake = builtins.getFlake (toString ./.);
                  buxChecks = builtins.attrValues flake.inputs.browser-ux_dinas.checks.aarch64-darwin;
                  regCheckDrv = flake.checks.aarch64-darwin.registered-input-checks;
                  regDrvs = builtins.map (c: c.drvPath) regCheckDrv.checks;
                in builtins.all (c: builtins.elem c.drvPath regDrvs) buxChecks
                '''
            ],
            cwd=str(ROOT),
            text=True,
        )
        self.assertEqual(out.strip(), "true")

        # (b) Aggregate exactly the selected Browser UX claims, including handoffs.
        claims_out = subprocess.check_output(
            [
                "nix", "eval", "--impure", "--json", "--expr",
                '''let flake = builtins.getFlake (toString ./.); in {
                  aggregate = builtins.filter (entry: entry.owner == "browser-ux") flake.applicationClaims;
                  expected = flake.lib.applicationClaims.attachOwner "browser-ux"
                    flake.inputs.browser-ux_dinas.applicationClaims;
                }'''
            ],
            cwd=str(ROOT),
            text=True,
        )
        claims = json.loads(claims_out)
        self.assertEqual(claims["aggregate"], claims["expected"])


if __name__ == "__main__":
    unittest.main()
