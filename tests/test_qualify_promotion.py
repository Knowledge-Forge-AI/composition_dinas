from __future__ import annotations
import sys
sys.dont_write_bytecode = True

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import select
import stat
import subprocess
import tempfile
import time
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from promotion_qualifier import (
    CATEGORY_CANDIDATE_RESOLUTION,
    CATEGORY_COMMAND_FAILURE,
    CATEGORY_INTERNAL_ERROR,
    CATEGORY_MALFORMED_PROVIDER_OUTPUT,
    CATEGORY_PROMOTION_CONTRACT,
    CATEGORY_PROTECTED_FILE_MUTATION,
    CATEGORY_REGISTRY_CONTRACT,
    CATEGORY_REQUEST_VALIDATION,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_DIAGNOSTIC_BYTES,
    MAX_JSON_BYTES,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    RESULT_SCHEMA,
    CommandResult,
    PromotionQualificationError,
    default_runner,
    parse_candidate_reference,
    qualify_candidate,
)
from registry_contract import load_registry

SYNTHETIC_REV = "a" * 40
REGISTRY_ENTRIES = load_registry(ROOT / "registry.json")


def make_valid_promotion_data() -> dict[str, Any]:
    return {
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
            for entry in REGISTRY_ENTRIES
        ],
        "releaseChannels": {
            "policy": "explicit-head-refs-v1",
            "refs": {
                entry["input"]: entry["releaseRef"]
                for entry in REGISTRY_ENTRIES
            },
        },
        "packageSetIdentity": "/nix/store/11111111111111111111111111111111-source",
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
            "names": [entry["input"] for entry in REGISTRY_ENTRIES],
        },
    }


def make_valid_metadata_json(
    input_name: str,
    candidate_rev: str,
    candidate_url: str,
) -> str:
    return json.dumps({
        "locks": {
            "root": "root",
            "version": 7,
            "nodes": {
                "root": {
                    "inputs": {
                        input_name: input_name,
                    }
                },
                input_name: {
                    "locked": {
                        "type": "git",
                        "rev": candidate_rev,
                        "url": candidate_url,
                    }
                },
            },
        }
    })


class QualifyPromotionUnitTests(unittest.TestCase):
    def test_parse_candidate_reference_valid(self):
        for candidate, expected_url, expected_rev in (
            (
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas",
                SYNTHETIC_REV,
            ),
            (
                f"ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas",
                SYNTHETIC_REV,
            ),
            (
                f"git+https://github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                "https://github.com/lair001/agentic-praxis-grimoire_dinas",
                SYNTHETIC_REV,
            ),
            (
                f"https://github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                "https://github.com/lair001/agentic-praxis-grimoire_dinas",
                SYNTHETIC_REV,
            ),
        ):
            with self.subTest(candidate=candidate):
                url, rev = parse_candidate_reference(candidate)
                self.assertEqual(url, expected_url)
                self.assertEqual(rev, expected_rev)

    def test_parse_candidate_reference_table_driven_rejections(self):
        rejected_candidates = [
            ("missing_rev", "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git"),
            (
                "blank_rev",
                "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev=",
            ),
            (
                "ref_param",
                "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?ref=main",
            ),
            (
                "extra_ref",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}&ref=main",
            ),
            (
                "extra_dir",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}&dir=nix-darwin",
            ),
            (
                "extra_narhash",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}&narHash=sha256-abc",
            ),
            (
                "extra_shallow",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}&shallow=1",
            ),
            (
                "duplicate_rev",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}&rev={'b'*40}",
            ),
            (
                "duplicate_blank_rev",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}&rev=",
            ),
            (
                "unknown_param",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}&foo=bar",
            ),
            (
                "fragment",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}#fragment",
            ),
            (
                "uppercase_hex",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={'A'*40}",
            ),
            (
                "non_hex",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={'a'*39 + 'g'}",
            ),
            (
                "short_rev",
                "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev=12345",
            ),
            (
                "long_rev",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={'a'*41}",
            ),
            (
                "leading_whitespace",
                f" git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            ),
            (
                "trailing_whitespace",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV} ",
            ),
            (
                "embedded_whitespace",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git? rev={SYNTHETIC_REV}",
            ),
            ("empty_string", ""),
        ]
        for label, candidate in rejected_candidates:
            with self.subTest(label=label, candidate=candidate):
                result = qualify_candidate(ROOT, "agentic-praxis-grimoire_dinas", candidate)
                self.assertEqual(result["outcome"], "fail")
                self.assertEqual(
                    result["failure"]["category"], CATEGORY_REQUEST_VALIDATION
                )

    def test_qualify_candidate_rejects_unregistered_or_indirect_input(self):
        valid_candidate = (
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}"
        )
        for input_name in ("unregistered", "core", "localConfig", "self"):
            with self.subTest(input_name=input_name):
                result = qualify_candidate(ROOT, input_name, valid_candidate)
                self.assertEqual(result["outcome"], "fail")
                self.assertEqual(
                    result["failure"]["category"], CATEGORY_REQUEST_VALIDATION
                )
                self.assertIn(
                    "not a registered direct module",
                    result["failure"]["message"],
                )

    def test_qualify_candidate_rejects_repository_mismatch(self):
        mismatched_candidate = (
            f"git+ssh://git@github.com/lair001/wrong-repo.git?rev={SYNTHETIC_REV}"
        )
        result = qualify_candidate(ROOT, "agentic-praxis-grimoire_dinas", mismatched_candidate)
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_REQUEST_VALIDATION
        )
        self.assertIn(
            "does not match registered URL", result["failure"]["message"]
        )

    def test_qualify_candidate_with_fake_runner_success(self):
        commands_run: list[list[str]] = []

        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            commands_run.append(argv)
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            if "lib.promotion" in argv[-1]:
                return 0, json.dumps(make_valid_promotion_data()), ""
            if "drvPath" in argv[-1]:
                return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )

        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertEqual(result["outcome"], "pass")
        self.assertIsNone(result["failure"])
        self.assertEqual(
            result["checks"],
            {
                "agent-namespace": "pass",
                "application-claims": "pass",
                "contracts": "pass",
                "lock-provenance": "pass",
                "registered-input-checks": "pass",
                "tests": "pass",
            },
        )
        self.assertEqual(result["hosts"]["example-workstation"]["status"], "pass")
        self.assertEqual(
            result["hosts"]["example-workstation"]["drvPath"],
            "/nix/store/11111111111111111111111111111111-fake-system.drv",
        )
        self.assertTrue(result["lock"]["byteIdentical"])
        self.assertTrue(result["tree"]["byteIdentical"])
        self.assertTrue(result["candidate"]["revMatched"])
        self.assertTrue(result["candidate"]["urlMatched"])
        self.assertEqual(result["candidate"]["resolvedRev"], SYNTHETIC_REV)

        # Check all commands carried exact --override-input
        for cmd in commands_run:
            self.assertIn("--no-write-lock-file", cmd)
            self.assertIn("--override-input", cmd)
            override_idx = cmd.index("--override-input")
            self.assertEqual(cmd[override_idx + 1], "agentic-praxis-grimoire_dinas")
            self.assertEqual(
                cmd[override_idx + 2],
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            )

    def test_candidate_resolution_failure_on_nix_metadata_error(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 1, "", "cannot fetch candidate revision"
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_CANDIDATE_RESOLUTION
        )

    def test_candidate_resolution_failure_on_malformed_metadata_json(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, "not json", ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT
        )

    def test_candidate_resolution_failure_on_rev_mismatch(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    "b" * 40,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_CANDIDATE_RESOLUTION
        )
        self.assertIn(
            "does not match requested revision", result["failure"]["message"]
        )

    def test_candidate_resolution_failure_on_url_mismatch(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/wrong-url.git",
                ), ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_CANDIDATE_RESOLUTION
        )
        self.assertIn("does not match registered URL", result["failure"]["message"])

    def test_candidate_resolution_failure_on_type_mismatch(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                meta = {
                    "locks": {
                        "root": "root",
                        "nodes": {
                            "root": {"inputs": {"agentic-praxis-grimoire_dinas": "agentic-praxis-grimoire_dinas"}},
                            "agentic-praxis-grimoire_dinas": {
                                "locked": {
                                    "type": "path",
                                    "rev": SYNTHETIC_REV,
                                    "url": "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas",
                                }
                            },
                        },
                    }
                }
                return 0, json.dumps(meta), ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_CANDIDATE_RESOLUTION
        )

    def test_candidate_resolution_failure_on_disallowed_dir(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                meta = {
                    "locks": {
                        "root": "root",
                        "nodes": {
                            "root": {"inputs": {"agentic-praxis-grimoire_dinas": "agentic-praxis-grimoire_dinas"}},
                            "agentic-praxis-grimoire_dinas": {
                                "locked": {
                                    "type": "git",
                                    "rev": SYNTHETIC_REV,
                                    "url": "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                                    "dir": "submodule",
                                }
                            },
                        },
                    }
                }
                return 0, json.dumps(meta), ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_CANDIDATE_RESOLUTION
        )
        self.assertIn("disallowed subdirectory selector", result["failure"]["message"])

    def test_promotion_contract_validation_failures(self):
        cases = [
            ("wrong_schema", lambda d: d.update({"schema": "wrong-schema"})),
            ("wrong_version", lambda d: d.update({"version": 1})),
            ("missing_key", lambda d: d.pop("checks")),
            ("extra_key", lambda d: d.update({"extra": "value"})),
            ("invalid_system", lambda d: d.update({"system": "aarch64 darwin"})),
            ("empty_checks", lambda d: d.update({"checks": []})),
            (
                "duplicate_checks",
                lambda d: d.update({"checks": ["check-one", "check-one"]}),
            ),
            (
                "malformed_check_name",
                lambda d: d.update({"checks": ["check.with.dot"]}),
            ),
            ("empty_hosts", lambda d: d.update({"hosts": []})),
            (
                "duplicate_hosts",
                lambda d: d.update({"hosts": ["HostA", "HostA"]}),
            ),
            (
                "malformed_host_name",
                lambda d: d.update({"hosts": ["Host.With.Dot"]}),
            ),
            ("modules_wrong_length", lambda d: d["modules"].pop()),
            (
                "module_wrong_name",
                lambda d: d["modules"][0].update({"name": "wrong-name"}),
            ),
            (
                "module_wrong_strategy",
                lambda d: d["modules"][0].update({"bootstrapStrategy": "none"}),
            ),
            (
                "promotable_missing_input",
                lambda d: d["promotableInputs"].update({"names": []}),
            ),
            (
                "release_channels_missing_input",
                lambda d: d["releaseChannels"]["refs"].pop("agentic-praxis-grimoire_dinas"),
            ),
            (
                "release_channels_extra_input",
                lambda d: d["releaseChannels"]["refs"].update(
                    {"extra-nd": "refs/heads/main"}
                ),
            ),
            (
                "release_channels_wrong_policy",
                lambda d: d["releaseChannels"].update({"policy": "moving-main"}),
            ),
            (
                "release_channels_ref_mismatch",
                lambda d: d["releaseChannels"]["refs"].update(
                    {"developer-ux_dinas": "refs/heads/main"}
                ),
            ),
            (
                "composition_wrong_name",
                lambda d: d["composition"].update({"name": "composition_dinas"}),
            ),
            (
                "composition_wrong_url_preimage",
                lambda d: d["composition"].update(
                    {"repositoryUrl": "git+ssh://git@github.com/lair001/composition-nd.git"}
                ),
            ),
            (
                "composition_wrong_url_arbitrary",
                lambda d: d["composition"].update(
                    {"repositoryUrl": "git+ssh://git@github.com/lair001/other-repo.git"}
                ),
            ),
        ]

        for label, mutator in cases:
            with self.subTest(label=label):
                data = make_valid_promotion_data()
                mutator(data)

                def fake_runner(argv: list[str]) -> tuple[int, str, str]:
                    if argv[1] == "flake" and argv[2] == "metadata":
                        return 0, make_valid_metadata_json(
                            "agentic-praxis-grimoire_dinas",
                            SYNTHETIC_REV,
                            "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                        ), ""
                    if "lib.promotion" in argv[-1]:
                        return 0, json.dumps(data), ""
                    return 0, "", ""

                result = qualify_candidate(
                    ROOT,
                    "agentic-praxis-grimoire_dinas",
                    f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                    runner=fake_runner,
                )
                self.assertEqual(result["outcome"], "fail")
                self.assertEqual(
                    result["failure"]["category"], CATEGORY_PROMOTION_CONTRACT
                )

    def test_qualify_candidate_stops_and_fails_closed_on_check_failure(self):
        commands_run: list[list[str]] = []

        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            commands_run.append(argv)
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            if "lib.promotion" in argv[-1]:
                return 0, json.dumps(make_valid_promotion_data()), ""
            if "application-claims" in argv[-1]:
                return 1, "", "check failed intentional error"
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )

        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_COMMAND_FAILURE
        )
        self.assertEqual(result["checks"]["application-claims"], "fail")
        self.assertEqual(result["checks"]["contracts"], "not-run")
        self.assertEqual(result["hosts"]["example-workstation"]["status"], "not-run")
        # Verify stopped before running contracts or hosts
        self.assertFalse(any("contracts" in cmd[-1] for cmd in commands_run))
        self.assertFalse(any("example-workstation" in cmd[-1] for cmd in commands_run))

    def test_qualify_candidate_stops_and_fails_closed_on_host_build_failure(
        self,
    ):
        commands_run: list[list[str]] = []

        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            commands_run.append(argv)
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            if "lib.promotion" in argv[-1]:
                return 0, json.dumps(make_valid_promotion_data()), ""
            if "example-workstation" in argv[-1] and "build" in argv:
                return 1, "", "host build failed"
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )

        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_COMMAND_FAILURE
        )
        self.assertEqual(
            result["hosts"]["example-workstation"]["status"], "fail"
        )
        self.assertEqual(
            result["hosts"]["example-primary"]["status"], "not-run"
        )

    def test_qualify_candidate_fails_on_drvpath_eval_failure(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            if "lib.promotion" in argv[-1]:
                return 0, json.dumps(make_valid_promotion_data()), ""
            if "drvPath" in argv[-1]:
                return 1, "", "drvPath query failed"
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )

        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_COMMAND_FAILURE
        )

    def test_qualify_candidate_fails_on_non_store_drvpath(self):
        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            if "lib.promotion" in argv[-1]:
                return 0, json.dumps(make_valid_promotion_data()), ""
            if "drvPath" in argv[-1]:
                return 0, "not-a-store-path\n", ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )

        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT
        )

    def test_bounded_diagnostic_capture(self):
        long_output = "x" * (MAX_DIAGNOSTIC_BYTES + 5000)

        def fake_runner(argv: list[str]) -> CommandResult:
            if argv[1] == "flake" and argv[2] == "metadata":
                out = make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                )
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            if "lib.promotion" in argv[-1]:
                out = json.dumps(make_valid_promotion_data())
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=len(long_output.encode("utf-8")),
                    stderr_sha256=hashlib.sha256(long_output.encode("utf-8")).hexdigest(),
                    stderr_truncated=True,
                    stderr_text=long_output[:MAX_DIAGNOSTIC_BYTES],
                )
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=0,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )

        eval_cmd = next(c for c in result["commands"] if "lib.promotion" in c["argv"][-1])
        self.assertTrue(eval_cmd["stderr"]["truncated"])
        self.assertEqual(
            eval_cmd["stderr"]["bytes"], len(long_output.encode("utf-8"))
        )
        self.assertEqual(
            eval_cmd["stderr"]["sha256"],
            hashlib.sha256(long_output.encode("utf-8")).hexdigest(),
        )
    def test_command_vector_closed_family(self):
        commands_run: list[list[str]] = []

        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            commands_run.append(argv)
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            if "lib.promotion" in argv[-1]:
                return 0, json.dumps(make_valid_promotion_data()), ""
            if "drvPath" in argv[-1]:
                return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
            return 0, "", ""

        qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )

        allowed_subcommands = {("flake", "metadata"), ("eval",), ("build",)}
        for cmd in commands_run:
            self.assertEqual(cmd[0], "nix")
            if cmd[1] == "flake":
                self.assertEqual(cmd[2], "metadata")
            else:
                self.assertIn(cmd[1], ("eval", "build"))
            self.assertIn("--no-write-lock-file", cmd)

    def test_registry_contract_failure_on_malformed_registry_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for rel in ("flake.lock", "flake.nix"):
                shutil.copy2(ROOT / rel, tmp_path / rel)
            (tmp_path / "registry.json").write_text("not json", encoding="utf-8")

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_REGISTRY_CONTRACT
            )

    def test_internal_error_on_unexpected_runner_exception(self):
        def crashing_runner(argv: list[str]) -> Any:
            raise RuntimeError("unexpected internal crash")

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=crashing_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_INTERNAL_ERROR)
        self.assertEqual(result["failure"]["details"]["type"], "RuntimeError")

    def test_max_json_bytes_exceeded_in_metadata(self):
        huge_output = " " * (10 * 1024 * 1024 + 100)

        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, huge_output, ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT
        )
        self.assertIn("exceeded size limit", result["failure"]["message"])

    def test_max_json_bytes_exceeded_in_promotion(self):
        huge_output = " " * (10 * 1024 * 1024 + 100)

        def fake_runner(argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "flake" and argv[2] == "metadata":
                return 0, make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ), ""
            if "lib.promotion" in argv[-1]:
                return 0, huge_output, ""
            return 0, "", ""

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT
        )
        self.assertIn("exceeded size limit", result["failure"]["message"])

    def test_missing_protected_file_at_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            shutil.copy2(ROOT / "flake.lock", tmp_path / "flake.lock")
            shutil.copy2(ROOT / "registry.json", tmp_path / "registry.json")
            # flake.nix is intentionally omitted

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_REQUEST_VALIDATION
            )
            self.assertIn("protected file 'flake.nix' is missing", result["failure"]["message"])

    def test_valid_json_larger_than_64kib_succeeds(self):
        raw_meta = json.loads(
            make_valid_metadata_json(
                "agentic-praxis-grimoire_dinas",
                SYNTHETIC_REV,
                "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
            )
        )
        # Pad with dummy keys to create valid JSON around 256 KiB
        raw_meta["_padding"] = "x" * (256 * 1024)
        large_json = json.dumps(raw_meta)
        self.assertGreater(len(large_json.encode("utf-8")), 64 * 1024)
        self.assertLess(len(large_json.encode("utf-8")), MAX_JSON_BYTES)

        def fake_runner(argv: list[str], **kwargs: Any) -> CommandResult:
            if argv[1] == "flake" and argv[2] == "metadata":
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(large_json.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(large_json.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=large_json,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            if "lib.promotion" in argv[-1]:
                out = json.dumps(make_valid_promotion_data())
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            if "drvPath" in argv[-1]:
                out = "/nix/store/11111111111111111111111111111111-fake-system.drv\n"
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=0,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "pass")
        self.assertTrue(result["candidate"]["revMatched"])

    def test_exact_10mib_boundary_json_succeeds(self):
        base_json = make_valid_metadata_json(
            "agentic-praxis-grimoire_dinas",
            SYNTHETIC_REV,
            "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
        )
        # Pad with trailing whitespace so length is exactly MAX_JSON_BYTES
        padding_len = MAX_JSON_BYTES - len(base_json.encode("utf-8"))
        exact_10mib_json = base_json + (" " * padding_len)
        self.assertEqual(len(exact_10mib_json.encode("utf-8")), MAX_JSON_BYTES)

        def fake_runner(argv: list[str], **kwargs: Any) -> CommandResult:
            if argv[1] == "flake" and argv[2] == "metadata":
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(exact_10mib_json.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(exact_10mib_json.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=exact_10mib_json,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            if "lib.promotion" in argv[-1]:
                out = json.dumps(make_valid_promotion_data())
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            if "drvPath" in argv[-1]:
                out = "/nix/store/11111111111111111111111111111111-fake-system.drv\n"
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=0,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "pass")

    def test_over_10mib_json_fails_closed_before_decoding(self):
        def fake_runner(argv: list[str], **kwargs: Any) -> CommandResult:
            if argv[1] == "flake" and argv[2] == "metadata":
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=MAX_JSON_BYTES + 1,
                    stdout_sha256=hashlib.sha256(b"x").hexdigest(),
                    stdout_truncated=True,
                    stdout_text="x" * 1000,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=0,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT
        )
        self.assertIn("exceeded size limit", result["failure"]["message"])

    def test_truncated_json_fails_closed(self):
        def fake_runner(argv: list[str], **kwargs: Any) -> CommandResult:
            if argv[1] == "flake" and argv[2] == "metadata":
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=5000,
                    stdout_sha256=hashlib.sha256(b"x").hexdigest(),
                    stdout_truncated=True,
                    stdout_text='{"locks": {',
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=0,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=fake_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(
            result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT
        )

    def test_timeout_propagation_to_runner(self):
        captured_timeouts: list[float] = []

        def tracking_runner(argv: list[str], timeout: float = DEFAULT_TIMEOUT_SECONDS, **kwargs: Any) -> CommandResult:
            captured_timeouts.append(timeout)
            if argv[1] == "flake" and argv[2] == "metadata":
                out = make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                )
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            if "lib.promotion" in argv[-1]:
                out = json.dumps(make_valid_promotion_data())
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            if "drvPath" in argv[-1]:
                out = "/nix/store/11111111111111111111111111111111-fake-system.drv\n"
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(out.encode("utf-8")),
                    stdout_sha256=hashlib.sha256(out.encode("utf-8")).hexdigest(),
                    stdout_truncated=False,
                    stdout_text=out,
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                )
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=0,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=tracking_runner,
            timeout_seconds=123.4,
        )
        self.assertEqual(result["outcome"], "pass")
        self.assertEqual(result["qualification"]["timeoutSeconds"], 123.4)
        self.assertGreater(len(captured_timeouts), 0)
        for t in captured_timeouts:
            self.assertEqual(t, 123.4)

    def test_timeout_validation_rejections(self):
        for bad_timeout in (0, -5.0, float("nan"), float("inf"), 7201.0, "abc", True, None):
            with self.subTest(bad_timeout=bad_timeout):
                result = qualify_candidate(
                    ROOT,
                    "agentic-praxis-grimoire_dinas",
                    f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                    timeout_seconds=bad_timeout,  # type: ignore[arg-type]
                )
                self.assertEqual(result["outcome"], "fail")
                self.assertEqual(
                    result["failure"]["category"], CATEGORY_REQUEST_VALIDATION
                )

    def test_timed_out_command_recorded_in_commands(self):
        def timing_out_runner(argv: list[str], **kwargs: Any) -> CommandResult:
            return CommandResult(
                argv=argv,
                exit_code=-9,
                stdout_bytes=0,
                stdout_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=10,
                stderr_sha256=hashlib.sha256(b"timed out").hexdigest(),
                stderr_truncated=False,
                stderr_text="timed out",
                timed_out=True,
                timeout_seconds=10.0,
                cleanup_disposition="sigkill",
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=timing_out_runner,
            timeout_seconds=10.0,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_COMMAND_FAILURE)
        self.assertTrue(result["failure"]["details"]["timedOut"])
        self.assertGreaterEqual(len(result["commands"]), 1)
        self.assertTrue(result["commands"][-1]["timedOut"])
        self.assertEqual(result["commands"][-1]["timeoutSeconds"], 10.0)


class QualifyPromotionMutationTests(unittest.TestCase):
    """Mutation tests run strictly in isolated temporary directory copies."""

    def _create_temp_composition(self) -> tempfile.TemporaryDirectory[str]:
        tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp.name)
        for rel in ("flake.lock", "flake.nix", "registry.json", "lib", "config"):
            src = ROOT / rel
            dst = tmp_path / rel
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.is_file():
                shutil.copy2(src, dst)
        return tmp

    def test_flake_lock_mutation_during_check_fails(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def mutating_runner(argv: list[str]) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "application-claims" in argv[-1]:
                    # Mutate flake.lock during check
                    (tmp_path / "flake.lock").write_text("mutated", encoding="utf-8")
                    return 0, "", ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=mutating_runner,
            )

            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )
            self.assertFalse(result["lock"]["byteIdentical"])
            self.assertIn("flake.lock", result["failure"]["details"]["mutatedFiles"])

    def test_flake_nix_mutation_during_host_build_fails(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def mutating_runner(argv: list[str]) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "example-workstation" in argv[-1] and "build" in argv:
                    # Mutate flake.nix during host build
                    (tmp_path / "flake.nix").write_text("mutated", encoding="utf-8")
                    return 0, "", ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=mutating_runner,
            )

            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )
            self.assertFalse(result["tree"]["byteIdentical"])
            self.assertIn("flake.nix", result["failure"]["details"]["mutatedFiles"])

    def test_registry_json_mutation_during_eval_fails(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def mutating_runner(argv: list[str]) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    # Mutate registry.json during eval
                    (tmp_path / "registry.json").write_text("mutated", encoding="utf-8")
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=mutating_runner,
            )

            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )
            self.assertFalse(result["tree"]["byteIdentical"])
            self.assertIn(
                "registry.json", result["failure"]["details"]["mutatedFiles"]
            )

    def test_file_deletion_during_qualification_fails(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def deleting_runner(argv: list[str]) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    # Delete registry.json
                    (tmp_path / "registry.json").unlink()
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=deleting_runner,
            )

            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )
            self.assertFalse(result["tree"]["byteIdentical"])

    def test_mutation_after_check_failure_preserves_initiating_failure(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def mutating_runner(argv: list[str]) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "application-claims" in argv[-1]:
                    (tmp_path / "flake.lock").write_text("mutated", encoding="utf-8")
                    return 1, "", "check failed error"
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=mutating_runner,
            )

            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )
            self.assertIsNotNone(
                result["failure"]["details"].get("initiatingFailure")
            )
            self.assertEqual(
                result["failure"]["details"]["initiatingFailure"]["category"],
                CATEGORY_COMMAND_FAILURE,
            )

    def test_symlink_at_entry_rejected(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)
            target = tmp_path / "flake.lock.target"
            target.write_bytes((tmp_path / "flake.lock").read_bytes())
            (tmp_path / "flake.lock").unlink()
            (tmp_path / "flake.lock").symlink_to(target)

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_REQUEST_VALIDATION
            )
            self.assertIn("must be an ordinary regular file, not a symlink", result["failure"]["message"])

    def test_symlink_replacement_during_execution_detected(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def symlink_replacing_runner(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    target = tmp_path / "flake.lock.target"
                    target.write_bytes((tmp_path / "flake.lock").read_bytes())
                    (tmp_path / "flake.lock").unlink()
                    (tmp_path / "flake.lock").symlink_to(target)
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=symlink_replacing_runner,
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )

    def test_same_byte_inode_replacement_detected(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def inode_replacing_runner(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    content = (tmp_path / "flake.lock").read_bytes()
                    (tmp_path / "flake.lock").unlink()
                    (tmp_path / "flake.lock").write_bytes(content)
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=inode_replacing_runner,
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )

    def test_checkpoint_halts_further_commands(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)
            commands_after_mutation: list[str] = []

            def mutating_runner(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "application-claims" in argv[-1]:
                    (tmp_path / "flake.lock").write_text("mutated", encoding="utf-8")
                    return 0, "", ""
                commands_after_mutation.append(" ".join(argv))
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=mutating_runner,
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )
            self.assertEqual(len(commands_after_mutation), 0)
            self.assertEqual(result["checks"].get("contracts"), "not-run")

    def test_file_mode_mutation_detected(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def mode_mutating_runner(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    # Change file mode/permissions on protected file
                    (tmp_path / "flake.lock").chmod(0o600)
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=mode_mutating_runner,
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(
                result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION
            )


class QualifyPromotionCLITests(unittest.TestCase):
    def test_cli_does_not_generate_pycache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for rel in ("tools", "python", "flake.lock", "flake.nix", "registry.json", "lib", "config"):
                src = ROOT / rel
                dst = tmp_path / rel
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            clean_env = dict(os.environ)
            clean_env.pop("PYTHONDONTWRITEBYTECODE", None)

            before_files = set()
            for p in tmp_path.rglob("*"):
                before_files.add(str(p.relative_to(tmp_path)))

            cmd = [
                sys.executable,
                str(tmp_path / "tools/qualify-promotion"),
                "--composition",
                str(tmp_path),
                "--input",
                "invalid-input",
                "--candidate",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                "--json",
            ]
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=clean_env,
            )
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            self.assertEqual(data["outcome"], "fail")

            after_files = set()
            for p in tmp_path.rglob("*"):
                after_files.add(str(p.relative_to(tmp_path)))

            created_files = after_files - before_files
            pycache_files = [f for f in created_files if "__pycache__" in f or f.endswith(".pyc")]
            self.assertEqual(pycache_files, [], f"Found created bytecode files: {pycache_files}")
            self.assertEqual(created_files, set(), f"Found unexpected created files: {created_files}")

    def test_cli_rejects_output_option(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            "--output",
            "/tmp/should-be-rejected.json",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unrecognized arguments", proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")

    def test_cli_json_mode_emits_one_document_on_failure_and_exits_nonzero(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?ref=main",
            "--json",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["schema"], RESULT_SCHEMA)
        self.assertEqual(data["outcome"], "fail")
        self.assertEqual(
            data["failure"]["category"], CATEGORY_REQUEST_VALIDATION
        )

    def test_cli_missing_arguments_with_json(self):
        # Missing --input
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--candidate",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            "--json",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["outcome"], "fail")
        self.assertEqual(data["failure"]["category"], CATEGORY_REQUEST_VALIDATION)

        # Missing --candidate
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--json",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["outcome"], "fail")
        self.assertEqual(data["failure"]["category"], CATEGORY_REQUEST_VALIDATION)

    def test_cli_unknown_option_with_json(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            "--unknown-flag",
            "--json",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["outcome"], "fail")
        self.assertEqual(data["failure"]["category"], CATEGORY_REQUEST_VALIDATION)

    def test_cli_output_option_with_json(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            "--output",
            "/tmp/test-output.json",
            "--json",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["outcome"], "fail")
        self.assertEqual(data["failure"]["category"], CATEGORY_REQUEST_VALIDATION)

    def test_cli_invalid_timeout(self):
        # With --json
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            "--timeout-seconds",
            "-10",
            "--json",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(data["outcome"], "fail")
        self.assertEqual(data["failure"]["category"], CATEGORY_REQUEST_VALIDATION)

        # Without --json
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            "--timeout-seconds",
            "-10",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error: --timeout-seconds must be a finite number", proc.stderr)

    def test_cli_help_exits_zero(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--help",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Qualify a candidate public module promotion", proc.stdout)

    def test_cli_help_with_json_exits_zero(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--json",
            "--help",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Qualify a candidate public module promotion", proc.stdout)

    def test_cli_human_mode_emits_stderr_on_failure_and_exits_nonzero(self):
        cmd = [
            sys.executable,
            str(ROOT / "tools/qualify-promotion"),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            "git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?ref=main",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("composition-nd qualify-promotion: FAIL", proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")


class QualifyPromotionProcessContainmentTests(unittest.TestCase):
    def test_process_group_containment_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            pidfile = tmp_path / "grandchild.pid"
            fake_nix_source = f"""#!{sys.executable}
import os
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "build":
    pidfile_path = "{pidfile}"
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    f = open(pidfile_path, "w", encoding="utf-8")
    f.write(str(proc.pid))
    f.flush()
    os.fsync(f.fileno())
    f.close()
    time.sleep(60)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            try:
                res = default_runner([str(fake_nix), "build", "target"], timeout=2.0)
                self.assertTrue(res.timed_out)
                self.assertIsNotNone(res.cleanup_disposition)

                self.assertTrue(pidfile.exists(), f"pidfile was not created; stderr: {res.stderr_text}")
                grandchild_pid = int(pidfile.read_text(encoding="utf-8").strip())

                # Poll to verify grandchild process is terminated (reaped/unreachable)
                deadline = time.time() + 3.0
                dead = False
                while time.time() < deadline:
                    try:
                        os.kill(grandchild_pid, 0)
                        time.sleep(0.05)
                    except (ProcessLookupError, PermissionError):
                        dead = True
                        break
                self.assertTrue(
                    dead,
                    f"Grandchild process {grandchild_pid} was not terminated on timeout",
                )
            finally:
                pass

    def test_descendant_survives_when_leader_exits_on_sigterm(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            pidfile = tmp_path / "descendant.pid"
            readyfile = tmp_path / "ready.flag"
            fake_nix_source = f"""#!{sys.executable}
import os
import signal
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "build":
    pidfile_path = "{pidfile}"
    readyfile_path = "{readyfile}"
    desc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); open('{readyfile}', 'w').close(); time.sleep(60)"
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    f = open(pidfile_path, "w", encoding="utf-8")
    f.write(str(desc.pid))
    f.flush()
    os.fsync(f.fileno())
    f.close()
    while not os.path.exists(readyfile_path):
        time.sleep(0.01)
    time.sleep(60)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            try:
                res = default_runner([str(fake_nix), "build", "target"], timeout=2.0)
                self.assertTrue(res.timed_out)
                self.assertEqual(res.cleanup_disposition, "sigkill")

                self.assertTrue(pidfile.exists())
                desc_pid = int(pidfile.read_text(encoding="utf-8").strip())

                deadline = time.time() + 3.0
                dead = False
                while time.time() < deadline:
                    try:
                        os.kill(desc_pid, 0)
                        time.sleep(0.05)
                    except (ProcessLookupError, PermissionError):
                        dead = True
                        break
                self.assertTrue(
                    dead,
                    f"Descendant process {desc_pid} ignoring SIGTERM was not killed by escalated SIGKILL",
                )
            finally:
                pass

    def test_leader_exits_zero_descendant_holds_stdout_open(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            pidfile = tmp_path / "descendant.pid"
            fake_nix_source = f"""#!{sys.executable}
import os
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "build":
    pidfile_path = "{pidfile}"
    # Spawn descendant that inherits stdout and sleeps
    desc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    f = open(pidfile_path, "w", encoding="utf-8")
    f.write(str(desc.pid))
    f.flush()
    os.fsync(f.fileno())
    f.close()
    # Leader exits 0 immediately without waiting
    sys.exit(0)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            try:
                res = default_runner([str(fake_nix), "build", "target"], timeout=4.0)
                self.assertEqual(res.status, "fail")
                self.assertEqual(res.failure_reason, "drain-timeout")

                self.assertTrue(pidfile.exists())
                desc_pid = int(pidfile.read_text(encoding="utf-8").strip())

                deadline = time.time() + 3.0
                dead = False
                while time.time() < deadline:
                    try:
                        os.kill(desc_pid, 0)
                        time.sleep(0.05)
                    except (ProcessLookupError, PermissionError):
                        dead = True
                        break
                self.assertTrue(dead, f"Descendant holding pipe {desc_pid} was not terminated")
            finally:
                pass

    def test_leader_exits_zero_descendant_closes_pipes_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            pidfile = tmp_path / "descendant.pid"
            fake_nix_source = f"""#!{sys.executable}
import os
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "build":
    pidfile_path = "{pidfile}"
    # Spawn descendant that closes pipes and sleeps
    desc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    f = open(pidfile_path, "w", encoding="utf-8")
    f.write(str(desc.pid))
    f.flush()
    os.fsync(f.fileno())
    f.close()
    # Leader exits 0 immediately
    sys.exit(0)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            try:
                res = default_runner([str(fake_nix), "build", "target"], timeout=3.0)
                self.assertEqual(res.status, "fail")
                self.assertEqual(res.failure_reason, "surviving-descendants")

                self.assertTrue(pidfile.exists())
                desc_pid = int(pidfile.read_text(encoding="utf-8").strip())

                deadline = time.time() + 3.0
                dead = False
                while time.time() < deadline:
                    try:
                        os.kill(desc_pid, 0)
                        time.sleep(0.05)
                    except (ProcessLookupError, PermissionError):
                        dead = True
                        break
                self.assertTrue(dead, f"Surviving descendant {desc_pid} was not cleaned up")
            finally:
                pass

    def test_leader_exits_nonzero_descendant_remains(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            pidfile = tmp_path / "nonzero_descendant.pid"
            fake_nix_source = f"""#!{sys.executable}
import os
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "build":
    pidfile_path = "{pidfile}"
    desc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    f = open(pidfile_path, "w", encoding="utf-8")
    f.write(str(desc.pid))
    f.flush()
    os.fsync(f.fileno())
    f.close()
    sys.exit(1)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            try:
                res = default_runner([str(fake_nix), "build", "target"], timeout=3.0)
                self.assertEqual(res.status, "fail")
                self.assertNotEqual(res.exit_code, 0)

                self.assertTrue(pidfile.exists(), f"pidfile {pidfile} did not exist, stderr: {res.stderr_text}")
                desc_pid = int(pidfile.read_text(encoding="utf-8").strip())

                deadline = time.time() + 3.0
                dead = False
                while time.time() < deadline:
                    try:
                        os.kill(desc_pid, 0)
                        time.sleep(0.05)
                    except (ProcessLookupError, PermissionError):
                        dead = True
                        break
                self.assertTrue(dead, f"Descendant on nonzero exit {desc_pid} was not cleaned up")
            finally:
                pass

    def test_clean_process_succeeds_normally(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            fake_nix_source = f"""#!{sys.executable}
import sys
if len(sys.argv) > 1 and sys.argv[1] == "build":
    print("clean build complete")
    sys.exit(0)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            try:
                res = default_runner([str(fake_nix), "build", "target"], timeout=3.0)
                self.assertEqual(
                    res.status,
                    "pass",
                    f"status: {res.status}, reason: {res.failure_reason}, disp: {res.cleanup_disposition}, rc: {res.exit_code}, out: {res.stdout_text!r}, err: {res.stderr_text!r}",
                )
                self.assertEqual(res.exit_code, 0)
                self.assertFalse(res.timed_out)
                self.assertIsNone(res.failure_reason)
                self.assertIn("clean build complete", res.stdout_text)
                self.assertIsNotNone(res.stdout_sha256)
            finally:
                pass


class QualifyPromotionReaderTests(unittest.TestCase):
    def test_stream_reader_worker_records_exception_and_fails_eof(self):
        from promotion_qualifier import _StreamReaderState, _stream_reader_worker

        class FailingPipe:
            def read(self, _size: int) -> bytes:
                raise OSError("simulated read error")
            def close(self) -> None:
                pass

        state = _StreamReaderState("stdout", 1024)
        _stream_reader_worker(FailingPipe(), state)
        self.assertFalse(state.eof)
        self.assertIn("simulated read error", str(state.error))

    def test_reader_failure_causes_command_failure(self):
        result = CommandResult(
            argv=["nix", "flake", "metadata"],
            exit_code=0,
            stdout_bytes=0,
            stdout_sha256=None,
            stdout_truncated=False,
            stdout_text="",
            stderr_bytes=0,
            stderr_sha256=None,
            stderr_truncated=False,
            stderr_text="",
            failure_reason="reader-failure",
            stdout_reader_error="injected stdout read failure",
        )
        self.assertEqual(result.to_dict()["status"], "fail")
        self.assertEqual(result.to_dict()["failureReason"], "reader-failure")


class QualifyPromotionRunnerTests(unittest.TestCase):
    def test_runner_raising_type_error_invoked_exactly_once(self):
        calls: list[list[str]] = []

        def bug_runner(argv: list[str], **_kwargs: Any) -> Any:
            calls.append(argv)
            raise TypeError("internal bug inside runner implementation")

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=bug_runner,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_INTERNAL_ERROR)

    def test_runner_malformed_tuple_fails_closed(self):
        def bad_tuple_runner(_argv: list[str], **_kwargs: Any) -> Any:
            return (0, "stdout")  # 2-tuple instead of 3-tuple

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=bad_tuple_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_INTERNAL_ERROR)

    def test_runner_bool_exit_code_in_tuple_fails_closed(self):
        def bool_tuple_runner(_argv: list[str], **_kwargs: Any) -> Any:
            return (True, "stdout", "stderr")

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=bool_tuple_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_INTERNAL_ERROR)

    def test_runner_unsupported_type_fails_closed(self):
        def non_object_runner(_argv: list[str], **_kwargs: Any) -> Any:
            return 42

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=non_object_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_INTERNAL_ERROR)


class QualifyPromotionSnapshotTests(unittest.TestCase):
    def _create_temp_composition(self) -> tempfile.TemporaryDirectory[str]:
        tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp_dir.name)
        for rel in (
            "flake.lock",
            "flake.nix",
            "registry.json",
            "lib",
            "config",
        ):
            src = ROOT / rel
            dst = tmp_path / rel
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return tmp_dir

    def test_same_byte_mtime_touch_mutation_detected(self):
        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def touch_mutating_runner(argv: list[str], **_kwargs: Any) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    # Touch flake.lock without changing bytes
                    new_time = time.time() + 1000
                    os.utime(tmp_path / "flake.lock", (new_time, new_time))
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=touch_mutating_runner,
            )
            self.assertEqual(result["outcome"], "fail")
            self.assertEqual(result["failure"]["category"], CATEGORY_PROTECTED_FILE_MUTATION)
            self.assertIn("flake.lock", result["failure"]["details"]["mutatedFiles"])

    def test_pass_impossible_with_false_or_null_protected_identity(self):
        from promotion_qualifier import is_snapshot_valid

        with self._create_temp_composition() as tmp_dir:
            tmp_path = Path(tmp_dir)

            def clean_runner(argv: list[str], **_kwargs: Any) -> tuple[int, str, str]:
                if argv[1] == "flake" and argv[2] == "metadata":
                    return 0, make_valid_metadata_json(
                        "agentic-praxis-grimoire_dinas",
                        SYNTHETIC_REV,
                        "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                    ), ""
                if "lib.promotion" in argv[-1]:
                    return 0, json.dumps(make_valid_promotion_data()), ""
                if "drvPath" in argv[-1]:
                    return 0, "/nix/store/11111111111111111111111111111111-fake-system.drv\n", ""
                return 0, "", ""

            result = qualify_candidate(
                tmp_path,
                "agentic-praxis-grimoire_dinas",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                runner=clean_runner,
            )
            self.assertEqual(result["outcome"], "pass")
            self.assertTrue(result["lock"]["byteIdentical"])
            self.assertTrue(result["tree"]["byteIdentical"])
            self.assertIsNotNone(result["lock"]["preSha256"])
            self.assertIsNotNone(result["lock"]["postSha256"])
            self.assertIsNotNone(result["tree"]["flakeNixPreSha256"])
            self.assertIsNotNone(result["tree"]["flakeNixPostSha256"])
            self.assertIsNotNone(result["tree"]["registryJsonPreSha256"])
            self.assertIsNotNone(result["tree"]["registryJsonPostSha256"])


class QualifyPromotionEncodingTests(unittest.TestCase):
    def test_metadata_stdout_invalid_utf8_fails_closed(self):
        invalid_raw = b'{"locks": {"invalid": \xff\xfe}}'

        def invalid_utf8_runner(argv: list[str], **_kwargs: Any) -> CommandResult:
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=len(invalid_raw),
                stdout_sha256=hashlib.sha256(invalid_raw).hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
                stdout_raw=invalid_raw,
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=invalid_utf8_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT)
        self.assertIn("invalid UTF-8", result["failure"]["message"])

    def test_promotion_stdout_invalid_utf8_fails_closed(self):
        invalid_raw = b'{"schema": "composition-nd-public-promotion-v1", "invalid": \xff\xfe}'

        def invalid_utf8_runner(argv: list[str], **_kwargs: Any) -> CommandResult:
            if argv[1] == "flake" and argv[2] == "metadata":
                valid_meta = make_valid_metadata_json(
                    "agentic-praxis-grimoire_dinas",
                    SYNTHETIC_REV,
                    "ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git",
                ).encode("utf-8")
                return CommandResult(
                    argv=argv,
                    exit_code=0,
                    stdout_bytes=len(valid_meta),
                    stdout_sha256=hashlib.sha256(valid_meta).hexdigest(),
                    stdout_truncated=False,
                    stdout_text="",
                    stderr_bytes=0,
                    stderr_sha256=hashlib.sha256(b"").hexdigest(),
                    stderr_truncated=False,
                    stderr_text="",
                    stdout_raw=valid_meta,
                )
            return CommandResult(
                argv=argv,
                exit_code=0,
                stdout_bytes=len(invalid_raw),
                stdout_sha256=hashlib.sha256(invalid_raw).hexdigest(),
                stdout_truncated=False,
                stdout_text="",
                stderr_bytes=0,
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stderr_truncated=False,
                stderr_text="",
                stdout_raw=invalid_raw,
            )

        result = qualify_candidate(
            ROOT,
            "agentic-praxis-grimoire_dinas",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            runner=invalid_utf8_runner,
        )
        self.assertEqual(result["outcome"], "fail")
        self.assertEqual(result["failure"]["category"], CATEGORY_MALFORMED_PROVIDER_OUTPUT)
        self.assertIn("invalid UTF-8", result["failure"]["message"])

    def test_diagnostic_stderr_invalid_utf8_preview_replaced_digest_exact(self):
        raw_err = b"diagnostic with invalid \xff\xfe bytes"
        cmd = CommandResult(
            argv=["nix", "build"],
            exit_code=1,
            stdout_bytes=0,
            stdout_sha256=hashlib.sha256(b"").hexdigest(),
            stdout_truncated=False,
            stdout_text="",
            stderr_bytes=len(raw_err),
            stderr_sha256=hashlib.sha256(raw_err).hexdigest(),
            stderr_truncated=False,
            stderr_text="",
            stderr_raw=raw_err,
        )
        d = cmd.to_dict()
        self.assertEqual(d["stderr"]["bytes"], len(raw_err))
        self.assertEqual(d["stderr"]["sha256"], hashlib.sha256(raw_err).hexdigest())
        self.assertIn("\ufffd", d["stderr"]["preview"])


class QualifyPromotionInterruptionTests(unittest.TestCase):
    def test_sigint_interruption_terminates_process_group_and_reaps(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            pidfile = tmp_path / "descendant.pid"
            fake_nix_source = f"""#!{sys.executable}
import os
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "flake" and sys.argv[2] == "metadata":
    pidfile_path = "{pidfile}"
    desc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    with open(pidfile_path, "w", encoding="utf-8") as f:
        f.write(str(desc.pid))
        f.flush()
    time.sleep(60)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            clean_env = dict(os.environ)
            clean_env["PATH"] = f"{tmp_dir}:{clean_env.get('PATH', '')}"

            cmd = [
                sys.executable,
                str(ROOT / "tools/qualify-promotion"),
                "--composition",
                str(ROOT),
                "--input",
                "agentic-praxis-grimoire_dinas",
                "--candidate",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                "--json",
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=clean_env,
            )

            # Wait for descendant PID file
            deadline = time.time() + 5.0
            while time.time() < deadline and not pidfile.exists():
                time.sleep(0.05)
            self.assertTrue(pidfile.exists())
            desc_pid = int(pidfile.read_text(encoding="utf-8").strip())

            # Send SIGINT to the qualifier process
            proc.send_signal(signal.SIGINT)
            stdout_data, _ = proc.communicate(timeout=5.0)

            self.assertEqual(proc.returncode, 1)
            data = json.loads(stdout_data)
            self.assertEqual(data["outcome"], "fail")
            self.assertEqual(data["failure"]["category"], CATEGORY_COMMAND_FAILURE)

            # Verify descendant is dead
            deadline = time.time() + 3.0
            dead = False
            while time.time() < deadline:
                try:
                    os.kill(desc_pid, 0)
                    time.sleep(0.05)
                except (ProcessLookupError, PermissionError):
                    dead = True
                    break
            self.assertTrue(dead, f"Descendant {desc_pid} was not terminated after SIGINT")

    def test_sigterm_interruption_terminates_process_group_and_reaps(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_nix = tmp_path / "nix"
            pidfile = tmp_path / "descendant.pid"
            fake_nix_source = f"""#!{sys.executable}
import os
import subprocess
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "flake" and sys.argv[2] == "metadata":
    pidfile_path = "{pidfile}"
    desc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    with open(pidfile_path, "w", encoding="utf-8") as f:
        f.write(str(desc.pid))
        f.flush()
    time.sleep(60)
"""
            fake_nix.write_text(fake_nix_source, encoding="utf-8")
            fake_nix.chmod(0o755)

            clean_env = dict(os.environ)
            clean_env["PATH"] = f"{tmp_dir}:{clean_env.get('PATH', '')}"

            cmd = [
                sys.executable,
                str(ROOT / "tools/qualify-promotion"),
                "--composition",
                str(ROOT),
                "--input",
                "agentic-praxis-grimoire_dinas",
                "--candidate",
                f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
                "--json",
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=clean_env,
            )

            deadline = time.time() + 5.0
            while time.time() < deadline and not pidfile.exists():
                time.sleep(0.05)
            self.assertTrue(pidfile.exists())
            desc_pid = int(pidfile.read_text(encoding="utf-8").strip())

            # Send SIGTERM to the qualifier process
            proc.send_signal(signal.SIGTERM)
            stdout_data, _ = proc.communicate(timeout=5.0)

            self.assertEqual(proc.returncode, 1)
            data = json.loads(stdout_data)
            self.assertEqual(data["outcome"], "fail")
            self.assertEqual(data["failure"]["category"], CATEGORY_COMMAND_FAILURE)

            deadline = time.time() + 3.0
            dead = False
            while time.time() < deadline:
                try:
                    os.kill(desc_pid, 0)
                    time.sleep(0.05)
                except (ProcessLookupError, PermissionError):
                    dead = True
                    break
            self.assertTrue(dead, f"Descendant {desc_pid} was not terminated after SIGTERM")

    def test_top_level_sigterm_handling_emits_structured_json(self):
        clean_env = os.environ.copy()
        clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "qualify-promotion"),
            "--composition",
            str(ROOT),
            "--input",
            "agentic-praxis-grimoire_dinas",
            "--candidate",
            f"git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git?rev={SYNTHETIC_REV}",
            "--json",
        ]
        # A 0.1s sleep races with Python startup inside Nix builders.
        # Pass a private pipe FD and signal only after the real CLI handler
        # reaches its exception-to-JSON boundary. No fake signal handler.
        ready_read, ready_write = os.pipe()
        clean_env["COMPOSITION_QUALIFIER_SIGTERM_READY_FD"] = str(ready_write)
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=clean_env,
                pass_fds=(ready_write,),
            )
            os.close(ready_write)
            ready_write = -1
            ready, _, _ = select.select([ready_read], [], [], 10.0)
            self.assertTrue(ready, "qualifier never announced SIGTERM handler readiness")
            self.assertEqual(os.read(ready_read, 1), b"R")
            proc.send_signal(signal.SIGTERM)
            stdout_data, _ = proc.communicate(timeout=5.0)
            self.assertEqual(proc.returncode, 1)
            data = json.loads(stdout_data)
            self.assertEqual(data["outcome"], "fail")
            self.assertEqual(data["failure"]["category"], CATEGORY_COMMAND_FAILURE)
        finally:
            os.close(ready_read)
            if ready_write >= 0:
                os.close(ready_write)
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5.0)


class QualifyPromotionDrvPathAndVocabularyTests(unittest.TestCase):
    def test_drvpath_regex_validation(self):
        from promotion_qualifier import DRV_PATH_PATTERN

        valid_drv = "/nix/store/0c1n8x835k045lczhm4mpbbbw7p8nkq3-darwin-system-25.11.ebec37a.drv"
        self.assertTrue(bool(DRV_PATH_PATTERN.match(valid_drv)))

        invalid_drvs = [
            "/not/store/0c1n8x835k045lczhm4mpbbbw7p8nkq3-darwin-system.drv",
            "/nix/store/0c1n8x835k045lczhm4mpbbbw7p8nkq3-darwin-system",
            "/nix/store/0c1n8x835k045lczhm4mpbbbw7p8nkq-darwin-system.drv",  # 31 chars
            "/nix/store/0c1n8x835k045lczhm4mpbbbw7p8nkq33-darwin-system.drv", # 33 chars
            "/nix/store/0c1n8x835k045lczhm4mpbbbw7p8nkqE-darwin-system.drv", # uppercase in hash
            "/nix/store/0c1n8x835k045lczhm4mpbbbw7p8nkq3-darwin-system.drv\n",
            "something\n/nix/store/0c1n8x835k045lczhm4mpbbbw7p8nkq3-darwin-system.drv",
            "",
        ]
        for inv in invalid_drvs:
            with self.subTest(inv=inv):
                self.assertFalse(bool(DRV_PATH_PATTERN.match(inv)), f"Expected {inv!r} to be rejected")

    def test_failure_reasons_closed_vocabulary(self):
        from promotion_qualifier import (
            COMMAND_FAILURE_REASONS,
            REASON_DRAIN_TIMEOUT,
            REASON_EXECUTION_TIMEOUT,
            REASON_INCOMPLETE_CLEANUP,
            REASON_INTERRUPTED,
            REASON_READER_FAILURE,
            REASON_SURVIVING_DESCENDANTS,
        )

        expected = {
            "execution-timeout",
            "drain-timeout",
            "surviving-descendants",
            "reader-failure",
            "incomplete-cleanup",
            "interrupted",
        }
        self.assertEqual(COMMAND_FAILURE_REASONS, expected)
        self.assertEqual(REASON_EXECUTION_TIMEOUT, "execution-timeout")
        self.assertEqual(REASON_DRAIN_TIMEOUT, "drain-timeout")
        self.assertEqual(REASON_SURVIVING_DESCENDANTS, "surviving-descendants")
        self.assertEqual(REASON_READER_FAILURE, "reader-failure")
        self.assertEqual(REASON_INCOMPLETE_CLEANUP, "incomplete-cleanup")
        self.assertEqual(REASON_INTERRUPTED, "interrupted")


if __name__ == "__main__":
    unittest.main()
