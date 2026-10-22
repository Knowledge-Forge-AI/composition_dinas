from __future__ import annotations
import sys
sys.dont_write_bytecode = True

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOADER = SourceFileLoader("composition_nd_bootstrap", str(ROOT / "tools/bootstrap"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BOOTSTRAP
LOADER.exec_module(BOOTSTRAP)


def module_entry(name: str = "sample-one", url: str | None = None) -> dict[str, object]:
    return {
        "name": name,
        "input": name,
        "url": url or f"git@example.invalid:modules/{name}.git",
        "releaseRef": "refs/heads/main",
        "checkout": name,
        "bootstrap": {
            "strategy": "repo-tool",
            "executable": "tools/init-config",
            "config": f"config/{name}.toml",
        },
    }


def registry(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "nix-darwin-composition-registry-v2",
        "modules": list(entries),
    }


def copy_default_entry(name: str = "sample-copy") -> dict[str, object]:
    return {
        "name": name,
        "input": name,
        "url": f"git@example.invalid:modules/{name}.git",
        "releaseRef": "refs/heads/main",
        "checkout": name,
        "bootstrap": {
            "strategy": "copy-default",
            "source": f"config/default/{name}.toml",
            "config": f"config/{name}.toml",
        },
    }


def none_entry(name: str = "sample-none") -> dict[str, object]:
    return {
        "name": name,
        "input": name,
        "url": f"git@example.invalid:modules/{name}.git",
        "releaseRef": "refs/heads/main",
        "checkout": name,
        "bootstrap": {"strategy": "none"},
    }


class BootstrapContractTests(unittest.TestCase):
    def load(self, document: dict[str, object]):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return BOOTSTRAP.load_registry(path)

    def test_closed_schema_accepts_authored_order_and_multiple_none_entries(self):
        entries = self.load(
            registry(
                module_entry(),
                copy_default_entry(),
                none_entry(),
                none_entry("sample-none-two"),
            )
        )
        self.assertEqual(
            [entry["name"] for entry in entries],
            ["sample-one", "sample-copy", "sample-none", "sample-none-two"],
        )

    def test_none_bootstrap_rejects_every_extra_key(self):
        for field, value in (
            ("config", "config/sample-none.toml"),
            ("source", "config/default/sample-none.toml"),
            ("executable", "tools/init-config"),
            ("unknown", True),
        ):
            entry = none_entry()
            entry["bootstrap"] = {"strategy": "none", field: value}
            with self.subTest(field=field):
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    self.load(registry(entry))

    def test_unknown_keys_and_unsupported_strategies_reject(self):
        document = registry(module_entry())
        document["unknown"] = True
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.load(document)

        document = registry(module_entry())
        del document["schema"]
        with self.assertRaisesRegex(
            BOOTSTRAP.BootstrapError,
            "missing required key: schema",
        ):
            self.load(document)

        entry = module_entry()
        entry["unknown"] = True
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.load(registry(entry))

    def test_release_ref_is_required_full_and_strict(self):
        entry = module_entry()
        del entry["releaseRef"]
        with self.assertRaisesRegex(BOOTSTRAP.BootstrapError, "releaseRef"):
            self.load(registry(entry))

        for invalid in (
            "main",
            "refs/tags/v1",
            "refs/remotes/origin/main",
            "refs/heads/HEAD",
            "refs/heads/feature/HEAD",
            "refs/heads/refs/heads/main",
            "refs/heads/feature..one",
            "refs/heads/feature//one",
            "refs/heads/feature.lock",
            "refs/heads/feature@{1}",
            "refs/heads/*",
        ):
            with self.subTest(release_ref=invalid):
                entry = module_entry()
                entry["releaseRef"] = invalid
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    self.load(registry(entry))

        entry = module_entry()
        entry["bootstrap"] = {"strategy": "shell", "command": "true"}
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.load(registry(entry))

    def test_duplicate_names_inputs_checkouts_and_normalized_urls_reject(self):
        variants = (
            (module_entry(), module_entry()),
            (module_entry(), {**module_entry("sample-two"), "input": "sample-one"}),
            (module_entry(), {**module_entry("sample-two"), "checkout": "sample-one"}),
            (
                module_entry(url="git@github.com:owner/repository.git"),
                module_entry("sample-two", "ssh://git@github.com/owner/repository"),
            ),
            (
                none_entry(),
                {**none_entry("sample-two"), "input": "sample-none"},
            ),
        )
        for entries in variants:
            with self.subTest(entries=entries):
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    self.load(registry(*entries))

    def test_nix_git_https_url_normalizes_to_clone_url(self):
        self.assertEqual(
            BOOTSTRAP._normalized_url(
                "git+https://example.invalid/owner/repository.git",
                "registry URL",
            ),
            "https://example.invalid/owner/repository",
        )

    def test_establish_entry_clones_with_normalized_https_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            composition = root / "composition"
            local_root = root / "local"
            (composition / "config/default").mkdir(parents=True)
            (composition / "config/default/sample-copy.toml").write_text(
                "[sample-copy]\n",
                encoding="utf-8",
            )
            entry = copy_default_entry()
            entry["url"] = "git+https://example.invalid/owner/repository.git"
            commands: list[list[str]] = []

            def record_run(command: list[str]) -> None:
                commands.append(command)
                checkout = Path(command[-1])
                (checkout / ".git").mkdir(parents=True)

            with mock.patch.object(BOOTSTRAP, "run", side_effect=record_run), mock.patch.object(
                BOOTSTRAP,
                "run_output",
                return_value="https://example.invalid/owner/repository",
            ):
                BOOTSTRAP.establish_entry(composition, local_root, entry)

            self.assertEqual(
                commands[0],
                [
                    "git",
                    "clone",
                    "--",
                    "https://example.invalid/owner/repository",
                    str((local_root / "sample-copy").resolve()),
                ],
            )

    def test_multiple_none_entries_establish_checkouts_without_config_surface(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            composition = root / "composition"
            local_root = root / "local"
            composition.mkdir()
            entries = [none_entry(), none_entry("sample-none-two")]
            for entry in entries:
                entry["url"] = (
                    f"git+https://example.invalid/owner/{entry['name']}.git"
                )
            commands: list[list[str]] = []

            def record_run(command: list[str]) -> None:
                commands.append(command)
                checkout = Path(command[-1])
                (checkout / ".git").mkdir(parents=True)

            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=record_run),
                mock.patch.object(
                    BOOTSTRAP,
                    "run_output",
                    side_effect=[
                        "https://example.invalid/owner/sample-none",
                        "https://example.invalid/owner/sample-none-two",
                    ],
                ) as run_output,
                mock.patch.object(
                    BOOTSTRAP,
                    "resolve_repo_tool",
                    side_effect=AssertionError("repo tool must not be resolved"),
                ),
                mock.patch.object(
                    BOOTSTRAP,
                    "resolve_default_source",
                    side_effect=AssertionError("default source must not be resolved"),
                ),
            ):
                checkouts = [
                    BOOTSTRAP.establish_entry(composition, local_root, entry)
                    for entry in entries
                ]

            self.assertEqual(
                checkouts,
                [
                    (local_root / "sample-none").resolve(),
                    (local_root / "sample-none-two").resolve(),
                ],
            )
            self.assertEqual(
                [command[:3] for command in commands],
                [["git", "clone", "--"], ["git", "clone", "--"]],
            )
            self.assertEqual(
                run_output.call_args_list,
                [
                    mock.call(
                        ["git", "-C", str(checkout), "remote", "get-url", "origin"]
                    )
                    for checkout in checkouts
                ],
            )
            self.assertFalse((local_root / "config").exists())

    def test_paths_reject_absolute_parent_and_wrong_config_subtree(self):
        for field, value in (
            ("checkout", "/absolute"),
            ("checkout", "../escape"),
        ):
            entry = module_entry()
            entry[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    self.load(registry(entry))

        entry = module_entry()
        entry["bootstrap"] = {
            "strategy": "copy-default",
            "source": "../secret",
            "config": "outside.toml",
        }
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.load(registry(entry))

    def test_bootstrap_metadata_must_match_registered_module_name(self):
        entry = copy_default_entry()
        entry["bootstrap"] = {
            **entry["bootstrap"],
            "config": "config/another-module.toml",
        }
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.load(registry(entry))

        entry = copy_default_entry()
        entry["bootstrap"] = {
            **entry["bootstrap"],
            "source": "config/default/another-module.toml",
        }
        with self.assertRaises(BOOTSTRAP.BootstrapError):
            self.load(registry(entry))

    def test_repo_tool_symlink_escape_rejects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            outside = root / "outside"
            (checkout / "tools").mkdir(parents=True)
            outside.write_text("tool", encoding="utf-8")
            (checkout / "tools/init-config").symlink_to(outside)
            with self.assertRaises(BOOTSTRAP.BootstrapError):
                BOOTSTRAP.resolve_repo_tool(checkout, module_entry())

    def test_local_config_root_symlink_escape_refuses_without_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local_root = root / "local"
            external = root / "external"
            local_root.mkdir()
            external.mkdir()
            (local_root / "config").symlink_to(external, target_is_directory=True)

            with self.assertRaises(BOOTSTRAP.BootstrapError):
                BOOTSTRAP.resolve_config(local_root, copy_default_entry())

            self.assertEqual(list(external.iterdir()), [])

    def test_symlinked_local_root_and_config_destination_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external"
            external.mkdir()
            local_root = root / "local"
            local_root.symlink_to(external, target_is_directory=True)
            with self.assertRaises(BOOTSTRAP.BootstrapError):
                BOOTSTRAP.resolve_config(local_root, copy_default_entry())
            with self.assertRaises(BOOTSTRAP.BootstrapError):
                BOOTSTRAP.checkout_for(local_root, copy_default_entry())

            local_root.unlink()
            (local_root / "config").mkdir(parents=True)
            destination = local_root / "config/sample-copy.toml"
            destination.symlink_to(external / "config.toml")
            with self.assertRaises(BOOTSTRAP.BootstrapError):
                BOOTSTRAP.resolve_config(local_root, copy_default_entry())

    def test_config_shape_validation_accepts_table_and_rejects_flat_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "sample-copy.toml"
            entry = copy_default_entry()
            config.write_text("[sample-copy]\n", encoding="utf-8")
            BOOTSTRAP.validate_config_document(config, entry)

            config.write_text('extensions = ["example"]\n', encoding="utf-8")
            with self.assertRaisesRegex(
                BOOTSTRAP.BootstrapError,
                r"must contain exactly the \[sample-copy\] table",
            ) as caught:
                BOOTSTRAP.validate_config_document(config, entry)
            self.assertNotIn("example", str(caught.exception))

    def _setup_bootstrap_environment(
        self,
        temporary_root: Path,
        entries: list[dict[str, object]],
    ) -> tuple[Path, Path, Path]:
        core = temporary_root / "core"
        core.mkdir()
        (core / "flake.nix").touch()

        composition = temporary_root / "composition"
        composition.mkdir()
        (composition / "registry.json").write_text(
            json.dumps(registry(*entries)),
            encoding="utf-8",
        )

        for entry in entries:
            bootstrap = entry["bootstrap"]
            assert isinstance(bootstrap, dict)
            if bootstrap["strategy"] == "copy-default":
                source = composition / str(bootstrap["source"])
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"[{entry['name']}]\n", encoding="utf-8")

        local_root = temporary_root / "local"
        local_root.mkdir()
        return core, composition, local_root

    @staticmethod
    def _extract_git_subcommand(command: list[str]) -> str | None:
        if not command or command[0] != "git":
            return None
        idx = 1
        while idx < len(command):
            arg = command[idx]
            if arg in ("-C", "-c"):
                idx += 2
            elif arg.startswith("-C") or arg.startswith("-c"):
                idx += 1
            elif arg.startswith("--git-dir=") or arg.startswith("--work-tree="):
                idx += 1
            elif arg == "--":
                idx += 1
                if idx < len(command):
                    return command[idx]
            elif arg.startswith("-"):
                idx += 1
            else:
                return arg
        return None

    def test_main_emits_overrides_only_for_core_and_repo_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool_entry = module_entry("sample-tool")
            copy_entry = copy_default_entry("sample-copy")
            none_mod_entry = none_entry("sample-none")
            entries = [tool_entry, copy_entry, none_mod_entry]

            core, composition, local_root = self._setup_bootstrap_environment(root, entries)

            commands: list[list[str]] = []
            repo_tool_runs: list[list[str]] = []

            def fake_run(command: list[str]) -> None:
                commands.append(command)
                if command[:3] == ["git", "clone", "--"]:
                    checkout = Path(command[-1])
                    (checkout / ".git").mkdir(parents=True, exist_ok=True)
                    if checkout.name == "sample-tool":
                        tool = checkout / "tools/init-config"
                        tool.parent.mkdir(parents=True, exist_ok=True)
                        tool.write_text("#!/bin/sh\n", encoding="utf-8")
                        tool.chmod(0o755)
                elif len(command) >= 2 and command[1] == "--config":
                    repo_tool_runs.append(command)
                    config_path = Path(command[command.index("--config") + 1])
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    config_path.write_text("[sample-tool]\n", encoding="utf-8")

            def fake_run_output(command: list[str]) -> str:
                commands.append(command)
                checkout_name = Path(command[2]).name
                for e in entries:
                    if e["name"] == checkout_name:
                        return BOOTSTRAP._normalized_url(e["url"], "registry URL")
                raise AssertionError(f"unexpected checkout name: {checkout_name}")

            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=fake_run),
                mock.patch.object(BOOTSTRAP, "run_output", side_effect=fake_run_output),
                mock.patch(
                    "sys.argv",
                    [
                        "bootstrap",
                        "--core",
                        str(core),
                        "--composition",
                        str(composition),
                        "--local-root",
                        str(local_root),
                    ],
                ),
            ):
                result = BOOTSTRAP.main()

            self.assertEqual(result, 0)
            self.assertEqual(len(repo_tool_runs), 1)

            # Verify all checkouts were cloned and established
            for e in entries:
                self.assertTrue((local_root / str(e["checkout"])).is_dir())

            # Find nix flake check command
            nix_cmds = [c for c in commands if len(c) >= 3 and c[:3] == ["nix", "flake", "check"]]
            self.assertEqual(len(nix_cmds), 1)
            nix_cmd = nix_cmds[0]

            # Core override must be present
            self.assertIn("--override-input", nix_cmd)
            core_idx = nix_cmd.index("core")
            self.assertEqual(nix_cmd[core_idx - 1], "--override-input")
            self.assertEqual(nix_cmd[core_idx + 1], f"path:{core.resolve()}")

            # repo-tool override must be present
            tool_idx = nix_cmd.index("sample-tool")
            self.assertEqual(nix_cmd[tool_idx - 1], "--override-input")
            self.assertEqual(
                nix_cmd[tool_idx + 1],
                f"path:{(local_root / 'sample-tool').resolve()}",
            )

            # copy-default and none must NOT be in overrides
            self.assertNotIn("sample-copy", nix_cmd)
            self.assertNotIn("sample-none", nix_cmd)
            self.assertNotIn(f"path:{(local_root / 'sample-copy').resolve()}", nix_cmd)
            self.assertNotIn(f"path:{(local_root / 'sample-none').resolve()}", nix_cmd)

    def test_divergent_or_dirty_leaf_checkouts_do_not_override_nix_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_entry = copy_default_entry("sample-copy")
            none_mod_entry = none_entry("sample-none")
            entries = [copy_entry, none_mod_entry]

            core, composition, local_root = self._setup_bootstrap_environment(root, entries)

            # Pre-create checkouts as dirty/divergent
            for e in entries:
                checkout = local_root / str(e["checkout"])
                (checkout / ".git").mkdir(parents=True)
                (checkout / "dirty_uncommitted_file.txt").write_text("divergence\n")

            commands: list[list[str]] = []

            def fake_run(command: list[str]) -> None:
                commands.append(command)

            def fake_run_output(command: list[str]) -> str:
                commands.append(command)
                checkout_name = Path(command[2]).name
                for e in entries:
                    if e["name"] == checkout_name:
                        return BOOTSTRAP._normalized_url(e["url"], "registry URL")
                raise AssertionError(f"unexpected checkout name: {checkout_name}")

            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=fake_run),
                mock.patch.object(BOOTSTRAP, "run_output", side_effect=fake_run_output),
                mock.patch(
                    "sys.argv",
                    [
                        "bootstrap",
                        "--core",
                        str(core),
                        "--composition",
                        str(composition),
                        "--local-root",
                        str(local_root),
                    ],
                ),
            ):
                result = BOOTSTRAP.main()

            self.assertEqual(result, 0)
            nix_cmds = [c for c in commands if len(c) >= 3 and c[:3] == ["nix", "flake", "check"]]
            self.assertEqual(len(nix_cmds), 1)
            nix_cmd = nix_cmds[0]

            self.assertNotIn("sample-copy", nix_cmd)
            self.assertNotIn("sample-none", nix_cmd)
            self.assertNotIn(f"path:{(local_root / 'sample-copy').resolve()}", nix_cmd)
            self.assertNotIn(f"path:{(local_root / 'sample-none').resolve()}", nix_cmd)

    def test_repo_tool_executable_trust_boundary_and_config_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool_entry = module_entry("sample-tool")
            entries = [tool_entry]

            # Case 1: Pre-existing config must be preserved and executable skipped,
            # but override-input must still point to local checkout.
            core, composition, local_root = self._setup_bootstrap_environment(root, entries)
            checkout = local_root / "sample-tool"
            (checkout / ".git").mkdir(parents=True)
            tool = checkout / "tools/init-config"
            tool.parent.mkdir(parents=True)
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            tool.chmod(0o755)

            config_path = local_root / "config/sample-tool.toml"
            config_path.parent.mkdir(parents=True)
            initial_content = '[sample-tool]\npreserved_custom_value = "secret"\n'
            config_path.write_text(initial_content, encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(command: list[str]) -> None:
                commands.append(command)

            def fake_run_output(command: list[str]) -> str:
                commands.append(command)
                return BOOTSTRAP._normalized_url(tool_entry["url"], "registry URL")

            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=fake_run),
                mock.patch.object(BOOTSTRAP, "run_output", side_effect=fake_run_output),
                mock.patch(
                    "sys.argv",
                    [
                        "bootstrap",
                        "--core",
                        str(core),
                        "--composition",
                        str(composition),
                        "--local-root",
                        str(local_root),
                    ],
                ),
            ):
                result = BOOTSTRAP.main()

            self.assertEqual(result, 0)
            self.assertEqual(config_path.read_text(encoding="utf-8"), initial_content)

            # Executable was not run because config already existed
            tool_runs = [c for c in commands if str(tool) in c[0]]
            self.assertEqual(len(tool_runs), 0)

            # nix flake check received override-input for repo-tool pointing to the checkout
            nix_cmds = [c for c in commands if len(c) >= 3 and c[:3] == ["nix", "flake", "check"]]
            self.assertEqual(len(nix_cmds), 1)
            nix_cmd = nix_cmds[0]
            self.assertIn("sample-tool", nix_cmd)
            self.assertEqual(
                nix_cmd[nix_cmd.index("sample-tool") + 1],
                f"path:{checkout.resolve()}",
            )

    def test_origin_mismatch_fails_closed_before_nix_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_entry = copy_default_entry("sample-copy")
            entries = [copy_entry]

            core, composition, local_root = self._setup_bootstrap_environment(root, entries)
            checkout = local_root / "sample-copy"
            (checkout / ".git").mkdir(parents=True)

            commands: list[list[str]] = []

            def fake_run(command: list[str]) -> None:
                commands.append(command)

            # Return spoofed origin
            def fake_run_output(command: list[str]) -> str:
                commands.append(command)
                return "https://attacker.invalid/malicious/repo"

            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=fake_run),
                mock.patch.object(BOOTSTRAP, "run_output", side_effect=fake_run_output),
                mock.patch(
                    "sys.argv",
                    [
                        "bootstrap",
                        "--core",
                        str(core),
                        "--composition",
                        str(composition),
                        "--local-root",
                        str(local_root),
                    ],
                ),
            ):
                result = BOOTSTRAP.main()

            self.assertEqual(result, 1)
            # Nix flake check was never executed
            nix_cmds = [c for c in commands if len(c) >= 3 and c[:3] == ["nix", "flake", "check"]]
            self.assertEqual(len(nix_cmds), 0)

    def test_copy_default_creation_preservation_and_invalid_toml_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            composition = root / "composition"
            local_root = root / "local"
            default_dir = composition / "config/default"
            default_dir.mkdir(parents=True)
            default_src = default_dir / "sample-copy.toml"
            default_src.write_text("[sample-copy]\nmode = 'default'\n", encoding="utf-8")

            entry = copy_default_entry("sample-copy")
            checkout = local_root / "sample-copy"
            (checkout / ".git").mkdir(parents=True)

            with mock.patch.object(
                BOOTSTRAP,
                "run_output",
                return_value=BOOTSTRAP._normalized_url(entry["url"], "registry URL"),
            ):
                # 1. Missing config is copied from default source
                cfg = local_root / "config/sample-copy.toml"
                self.assertFalse(cfg.exists())
                BOOTSTRAP.establish_entry(composition, local_root, entry)
                self.assertTrue(cfg.is_file())
                self.assertIn("mode = 'default'", cfg.read_text(encoding="utf-8"))

                # 2. Existing valid config is preserved
                cfg.write_text("[sample-copy]\ncustom = 42\n", encoding="utf-8")
                BOOTSTRAP.establish_entry(composition, local_root, entry)
                self.assertIn("custom = 42", cfg.read_text(encoding="utf-8"))

                # 3. Existing invalid TOML fails closed
                cfg.write_text("corrupted toml {{{", encoding="utf-8")
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    BOOTSTRAP.establish_entry(composition, local_root, entry)

                # 4. Wrong table name fails closed
                cfg.write_text("[unrelated]\nx = 1\n", encoding="utf-8")
                with self.assertRaises(BOOTSTRAP.BootstrapError):
                    BOOTSTRAP.establish_entry(composition, local_root, entry)

    def test_checkout_parent_escape_fails_closed_before_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core = root / "core"
            core.mkdir()
            (core / "flake.nix").touch()

            # Attempt path escape in checkout
            escaping_entry = copy_default_entry("sample-copy")
            escaping_entry["checkout"] = "../escaped_checkout"
            composition = root / "composition"
            composition.mkdir()
            (composition / "registry.json").write_text(
                json.dumps(registry(escaping_entry)),
                encoding="utf-8",
            )
            local_root = root / "local"
            local_root.mkdir()

            commands: list[list[str]] = []
            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=commands.append),
                mock.patch(
                    "sys.argv",
                    [
                        "bootstrap",
                        "--core",
                        str(core),
                        "--composition",
                        str(composition),
                        "--local-root",
                        str(local_root),
                    ],
                ),
            ):
                result = BOOTSTRAP.main()

            self.assertEqual(result, 1)
            nix_cmds = [c for c in commands if len(c) >= 3 and c[:3] == ["nix", "flake", "check"]]
            self.assertEqual(len(nix_cmds), 0)

    def test_no_destructive_git_operations_are_performed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool_entry = module_entry("sample-tool")
            copy_entry = copy_default_entry("sample-copy")
            none_mod_entry = none_entry("sample-none")
            entries = [tool_entry, copy_entry, none_mod_entry]

            core, composition, local_root = self._setup_bootstrap_environment(root, entries)

            commands: list[list[str]] = []

            def fake_run(command: list[str]) -> None:
                commands.append(command)
                if command[:3] == ["git", "clone", "--"]:
                    checkout = Path(command[-1])
                    (checkout / ".git").mkdir(parents=True, exist_ok=True)
                    if checkout.name == "sample-tool":
                        tool = checkout / "tools/init-config"
                        tool.parent.mkdir(parents=True, exist_ok=True)
                        tool.write_text("#!/bin/sh\n", encoding="utf-8")
                        tool.chmod(0o755)
                elif len(command) >= 2 and command[1] == "--config":
                    config_path = Path(command[command.index("--config") + 1])
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    config_path.write_text("[sample-tool]\n", encoding="utf-8")

            def fake_run_output(command: list[str]) -> str:
                commands.append(command)
                checkout_name = Path(command[2]).name
                for e in entries:
                    if e["name"] == checkout_name:
                        return BOOTSTRAP._normalized_url(e["url"], "registry URL")
                raise AssertionError(f"unexpected checkout name: {checkout_name}")

            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=fake_run),
                mock.patch.object(BOOTSTRAP, "run_output", side_effect=fake_run_output),
                mock.patch(
                    "sys.argv",
                    [
                        "bootstrap",
                        "--core",
                        str(core),
                        "--composition",
                        str(composition),
                        "--local-root",
                        str(local_root),
                    ],
                ),
            ):
                result = BOOTSTRAP.main()

            self.assertEqual(result, 0)

            destructive_subcommands = frozenset({
                "reset",
                "clean",
                "stash",
                "checkout",
                "switch",
                "pull",
                "rebase",
                "branch",
                "merge",
            })

            # Check every git command executed via run or run_output
            for cmd in commands:
                subcmd = self._extract_git_subcommand(cmd)
                if subcmd is not None:
                    self.assertNotIn(
                        subcmd,
                        destructive_subcommands,
                        f"destructive git subcommand invoked: {cmd}",
                    )
                    # Subcommand must strictly be an allowed non-destructive operation
                    self.assertIn(subcmd, ("clone", "remote"))

    def test_virtual_machines_copy_default_is_repeatable_and_preserves_existing(self):
        entries = BOOTSTRAP.load_registry(ROOT / "registry.json")
        entry = next(e for e in entries if e["name"] == "virtual-machines")
        self.assertEqual(entry, {
            "name": "virtual-machines", "input": "virtual-machines_dinas",
            "checkout": "virtual-machines_dinas",
            "url": "git+ssh://git@github.com/lair001/virtual-machines_dinas.git",
            "releaseRef": "refs/heads/main",
            "bootstrap": {"strategy": "copy-default",
                          "source": "config/default/virtual-machines.toml",
                          "config": "config/virtual-machines.toml"},
        })
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / entry["checkout"] / ".git").mkdir(parents=True)
            with (mock.patch.object(BOOTSTRAP, "run") as run,
                  mock.patch.object(BOOTSTRAP, "run_output", return_value=entry["url"])):
                destination = root / entry["bootstrap"]["config"]
                BOOTSTRAP.establish_entry(ROOT, root, entry)
                expected = (ROOT / entry["bootstrap"]["source"]).read_bytes()
                self.assertEqual(destination.read_bytes(), expected)
                before = destination.stat().st_mtime_ns
                BOOTSTRAP.establish_entry(ROOT, root, entry)
                self.assertEqual(destination.read_bytes(), expected)
                self.assertEqual(destination.stat().st_mtime_ns, before)
                custom = b"[virtual-machines]\nschema = 1\nenable = true\ntooling = false\n"
                destination.write_bytes(custom)
                for _ in range(2):
                    BOOTSTRAP.establish_entry(ROOT, root, entry)
                    self.assertEqual(destination.read_bytes(), custom)
                run.assert_not_called()

    def test_browser_ux_bootstrap_contract(self):
        # 1. load_registry accepts real registry.json and finds browser-ux entry
        entries = BOOTSTRAP.load_registry(ROOT / "registry.json")
        bux_entries = [e for e in entries if e["name"] == "browser-ux"]
        self.assertEqual(len(bux_entries), 1)
        entry = bux_entries[0]
        self.assertEqual(entry["input"], "browser-ux_dinas")
        self.assertEqual(entry["releaseRef"], "refs/heads/main")
        self.assertEqual(entry["checkout"], "browser-ux_dinas")
        self.assertEqual(
            entry["bootstrap"],
            {
                "strategy": "copy-default",
                "source": "config/default/browser-ux.toml",
                "config": "config/browser-ux.toml",
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            local_root = Path(temporary)
            checkout = local_root / "browser-ux_dinas"
            (checkout / ".git").mkdir(parents=True)
            norm_url = BOOTSTRAP._normalized_url(entry["url"], "registry URL")

            commands: list[list[str]] = []
            def record_run(command: list[str]) -> None:
                commands.append(command)

            with (
                mock.patch.object(BOOTSTRAP, "run", side_effect=record_run),
                mock.patch.object(BOOTSTRAP, "run_output", return_value=norm_url),
            ):
                # 2. copy-default copies config/default/browser-ux.toml to config/browser-ux.toml
                cfg = local_root / "config/browser-ux.toml"
                self.assertFalse(cfg.exists())
                BOOTSTRAP.establish_entry(ROOT, local_root, entry)
                self.assertTrue(cfg.is_file())
                self.assertEqual(
                    cfg.read_text(encoding="utf-8"),
                    (ROOT / "config/default/browser-ux.toml").read_text(encoding="utf-8"),
                )

                # 3. Existing valid destination is preserved and not overwritten
                cfg.write_text("[browser-ux]\nschema = 1\n[browser-ux.keepassxc]\nenable = true\n", encoding="utf-8")
                BOOTSTRAP.establish_entry(ROOT, local_root, entry)
                self.assertIn("enable = true", cfg.read_text(encoding="utf-8"))

                # 4. Nothing is activated
                for cmd in commands:
                    cmd_str = " ".join(cmd)
                    self.assertNotIn("switch", cmd_str)
                    self.assertNotIn("darwin-rebuild", cmd_str)
                    self.assertNotIn("nix-update", cmd_str)

                # 5. Checkout origin URL mismatch fails closed
                with mock.patch.object(
                    BOOTSTRAP, "run_output", return_value="git@attacker.invalid:bad/repo.git"
                ):
                    with self.assertRaises(BOOTSTRAP.BootstrapError):
                        BOOTSTRAP.establish_entry(ROOT, local_root, entry)


if __name__ == "__main__":
    unittest.main()
