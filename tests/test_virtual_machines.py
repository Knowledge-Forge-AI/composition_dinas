"""Exercise the real composition projection using only portable fixtures."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VirtualMachinesLockTests(unittest.TestCase):
    def test_guest_linux_is_pinned_separately_from_host_follows(self):
        lock = json.loads((ROOT / "flake.lock").read_text())
        nodes = lock["nodes"]
        root = nodes[lock["root"]]["inputs"]
        leaf = nodes[root["virtual-machines_dinas"]]
        self.assertEqual(leaf["inputs"]["nixpkgs"], ["core", "nixpkgs"])
        self.assertEqual(leaf["inputs"]["nix-darwin"], ["core", "nix-darwin"])
        self.assertIsInstance(leaf["inputs"]["linux"], str)
        guest = nodes[leaf["inputs"]["linux"]]
        host = nodes[nodes[root["core"]]["inputs"]["nixpkgs"]]
        self.assertEqual(guest["original"]["ref"], "nixos-26.05")
        self.assertNotEqual(guest["locked"]["rev"], host["locked"]["rev"])
        self.assertEqual(leaf["locked"]["type"], "git")
        self.assertEqual(leaf["original"]["ref"], "main")


@unittest.skipUnless(shutil.which("nix"), "nix executable not available")
class VirtualMachinesProjectionTests(unittest.TestCase):
    def test_projected_policy_cases_and_closed_owner_table(self):
        hosts = json.loads(subprocess.check_output(
            ["nix", "eval", "path:" + str(ROOT) + "#lib.hosts", "--apply",
             "builtins.attrNames", "--json", "--no-write-lock-file",
             "--no-update-lock-file"], text=True, timeout=120))
        host = hosts[0]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary).resolve() / "config"
            shutil.copytree(ROOT / "config/default", fixture)
            config = fixture / "virtual-machines.toml"

            def evaluate(attribute, apply=None):
                command = ["nix", "eval", "path:" + str(ROOT) + "#" + attribute,
                           "--json", "--no-write-lock-file", "--override-input",
                           "localConfig", "path:" + str(fixture)]
                if apply:
                    command += ["--apply", apply]
                return subprocess.run(command, capture_output=True, text=True, timeout=120)

            attribute = f"darwinConfigurations.{host}.config.virtual-machines.configuration"
            config.write_text('[virtual-machines]\nenable = true\ntooling = false\n'
                              '[virtual-machines.base]\ncpus = 3\n')
            result = evaluate(attribute)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = json.loads(result.stdout)
            self.assertTrue(values["enable"])
            self.assertFalse(values["tooling"])
            self.assertEqual(values["base"], {"cpus": 3, "memory": 2048})
            self.assertEqual(values["kde"], {"cpus": 4, "memory": 6144})
            packages = f"darwinConfigurations.{host}.config.environment.systemPackages"
            result = evaluate(packages, "ps: map (p: p.drvPath) ps")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sum("dinas-vm" in p for p in json.loads(result.stdout)), 1)

            policy = evaluate(f"darwinConfigurations.{host}.pkgs.config.allowUnfreePredicate",
                              'p: p { pname = "vagrant"; }')
            self.assertEqual(policy.returncode, 0, policy.stderr)
            config.write_text('[virtual-machines]\nenable = true\ntooling = true\n')
            result = evaluate(packages, "ps: map (p: p.drvPath) ps")
            if json.loads(policy.stdout):
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("virtual-machines: enabled tooling requires the core-owned "
                              "nixpkgs policy to permit vagrant (BSL-1.1)", result.stderr)

            for invalid in ('[other]\nenable = false\n',
                            '[virtual-machines]\n[other]\n',
                            '[virtual-machines]\nunknown = true\n'):
                with self.subTest(invalid=invalid):
                    config.write_text(invalid)
                    result = evaluate(attribute)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("virtual-machines", result.stderr)
