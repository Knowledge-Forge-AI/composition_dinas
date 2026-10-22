from __future__ import annotations
import sys
sys.dont_write_bytecode = True

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from promotion_qualifier import (
    CATEGORY_PROMOTION_CONTRACT,
    COMPOSITION_KEYS,
    MODULE_RECORD_KEYS,
    PROMOTABLE_INPUTS_KEYS,
    PROMOTION_TOP_LEVEL_KEYS,
    QUALIFICATION_KEYS,
    RELEASE_CHANNELS_KEYS,
    RELEASE_CHANNELS_POLICY,
    PromotionQualificationError,
    validate_promotion_contract,
)
from registry_contract import load_registry


class PromotionContractTests(unittest.TestCase):
    def test_promotion_contract_file_structure(self):
        source = (ROOT / "lib/promotion.nix").read_text(encoding="utf-8")
        self.assertIn('schema = "composition-nd-public-promotion-v1";', source)
        self.assertIn("version = 2;", source)
        self.assertIn('policy = "explicit-head-refs-v1";', source)
        self.assertIn("value = entry.releaseRef;", source)
        self.assertIn('name = "composition-nd";', source)
        self.assertIn(
            'repositoryUrl = "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git";',
            source,
        )
        self.assertNotIn("composition-nd.git", source)
        self.assertIn("tools/qualify-promotion", source)
        self.assertIn("exact-candidate", source)
        self.assertIn("registered-direct-inputs-only", source)

    def test_promotion_contract_exact_allowed_keys_and_schema(self):
        registry_entries = load_registry(ROOT / "registry.json")
        valid_contract = {
            "schema": "composition-nd-public-promotion-v1",
            "version": 2,
            "composition": {
                "name": "composition-nd",
                "repositoryUrl": "git+ssh://git@github.com/Knowledge-Forge-AI/composition_dinas.git",
            },
            "system": "aarch64-darwin",
            "modules": [
                {
                    "name": entry["name"],
                    "input": entry["input"],
                    "url": entry["url"],
                    "checkout": entry["checkout"],
                    "bootstrapStrategy": entry["bootstrap"]["strategy"],
                    "ownsMachineLocalConfiguration": entry["bootstrap"]["strategy"]
                    != "none",
                }
                for entry in registry_entries
            ],
            "releaseChannels": {
                "policy": RELEASE_CHANNELS_POLICY,
                "refs": {
                    entry["input"]: entry["releaseRef"]
                    for entry in registry_entries
                },
            },
            "packageSetIdentity": "/nix/store/test-package-set",
            "hosts": ["example-workstation", "example-primary"],
            "checks": [
                "application-claims",
                "agent-namespace",
                "contracts",
                "lock-provenance",
                "registered-input-checks",
                "tests",
            ],
            "qualification": {
                "entryPoint": "tools/qualify-promotion",
                "mode": "exact-candidate",
            },
            "promotableInputs": {
                "rule": "registered-direct-inputs-only",
                "names": [entry["input"] for entry in registry_entries],
            },
        }

        # Exact keys verification
        self.assertEqual(set(valid_contract.keys()), PROMOTION_TOP_LEVEL_KEYS)
        self.assertEqual(set(valid_contract["composition"].keys()), COMPOSITION_KEYS)
        self.assertEqual(set(valid_contract["qualification"].keys()), QUALIFICATION_KEYS)
        self.assertEqual(set(valid_contract["releaseChannels"].keys()), RELEASE_CHANNELS_KEYS)
        self.assertEqual(
            set(valid_contract["promotableInputs"].keys()), PROMOTABLE_INPUTS_KEYS
        )
        for mod in valid_contract["modules"]:
            self.assertEqual(set(mod.keys()), MODULE_RECORD_KEYS)

        # Successfully validates
        validated = validate_promotion_contract(
            valid_contract, registry_entries, "agentic-praxis-grimoire_dinas"
        )
        self.assertEqual(validated["schema"], "composition-nd-public-promotion-v1")

        for label, mutate in (
            ("missing", lambda value: value["refs"].pop("agentic-praxis-grimoire_dinas")),
            ("extra", lambda value: value["refs"].update({"extra-nd": "refs/heads/main"})),
            ("policy", lambda value: value.update({"policy": "moving-main"})),
            ("short", lambda value: value["refs"].update({"agentic-praxis-grimoire_dinas": "main"})),
            ("tag", lambda value: value["refs"].update({"agentic-praxis-grimoire_dinas": "refs/tags/v1"})),
            ("mismatch", lambda value: value["refs"].update({"developer-ux_dinas": "refs/heads/main"})),
        ):
            with self.subTest(release_channels=label):
                bad = json.loads(json.dumps(valid_contract))
                mutate(bad["releaseChannels"])
                with self.assertRaises(PromotionQualificationError):
                    validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")

        # Rejects extra top-level key
        with self.assertRaises(PromotionQualificationError) as ctx:
            bad = dict(valid_contract, unexpected="field")
            validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")
        self.assertEqual(ctx.exception.category, CATEGORY_PROMOTION_CONTRACT)

        # Rejects missing top-level key
        with self.assertRaises(PromotionQualificationError) as ctx:
            bad = dict(valid_contract)
            del bad["qualification"]
            validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")
        self.assertEqual(ctx.exception.category, CATEGORY_PROMOTION_CONTRACT)

        # Rejects extra key in composition
        with self.assertRaises(PromotionQualificationError) as ctx:
            bad = dict(valid_contract)
            bad["composition"] = dict(valid_contract["composition"], extra=1)
            validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")
        self.assertEqual(ctx.exception.category, CATEGORY_PROMOTION_CONTRACT)

        # Rejects extra key in module record
        with self.assertRaises(PromotionQualificationError) as ctx:
            bad = dict(valid_contract)
            bad["modules"] = [dict(valid_contract["modules"][0], extra=1)]
            validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")
        self.assertEqual(ctx.exception.category, CATEGORY_PROMOTION_CONTRACT)

        # Rejects historical physical composition repository URL
        with self.assertRaises(PromotionQualificationError) as ctx:
            bad = json.loads(json.dumps(valid_contract))
            bad["composition"]["repositoryUrl"] = "git+ssh://git@github.com/lair001/composition-nd.git"
            validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")
        self.assertEqual(ctx.exception.category, CATEGORY_PROMOTION_CONTRACT)

        # Rejects logical name changed to composition_dinas
        with self.assertRaises(PromotionQualificationError) as ctx:
            bad = json.loads(json.dumps(valid_contract))
            bad["composition"]["name"] = "composition_dinas"
            validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")
        self.assertEqual(ctx.exception.category, CATEGORY_PROMOTION_CONTRACT)

        # Rejects arbitrary composition repository URL
        with self.assertRaises(PromotionQualificationError) as ctx:
            bad = json.loads(json.dumps(valid_contract))
            bad["composition"]["repositoryUrl"] = "git+ssh://git@github.com/lair001/other-repo.git"
            validate_promotion_contract(bad, registry_entries, "agentic-praxis-grimoire_dinas")
        self.assertEqual(ctx.exception.category, CATEGORY_PROMOTION_CONTRACT)

    def test_promotion_contract_static_consistency_with_registry(self):
        registry_data = json.loads(
            (ROOT / "registry.json").read_text(encoding="utf-8")
        )
        registry_inputs = [m["input"] for m in registry_data["modules"]]
        registry_names = [m["name"] for m in registry_data["modules"]]

        self.assertEqual(len(registry_inputs), len(set(registry_inputs)))
        self.assertEqual(len(registry_names), len(set(registry_names)))
        self.assertNotIn("core", registry_inputs)
        self.assertNotIn("localConfig", registry_inputs)
        self.assertNotIn("self", registry_inputs)

    def test_promotion_contract_excludes_private_and_host_specific_data(self):
        source = (ROOT / "lib/promotion.nix").read_text(encoding="utf-8")
        for forbidden in (
            "/Users/",
            "operator",
            "composition-private-nd",
            "secrets",
            "password",
            "token",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
