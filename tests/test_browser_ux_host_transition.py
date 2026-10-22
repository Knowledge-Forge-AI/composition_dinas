"""Exercise committed Browser UX schemas, without flake/daemon writes.

Run directly with --output to retain a bounded old/new matrix. The historical
revision is a test fixture, not a prerequisite for the live checkout HEAD.
"""
import argparse
import functools
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest

sys.dont_write_bytecode = True


def command(argv):
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=True)


def extract_commit(checkout, revision, target):
    raw = command(["git", "-C", str(checkout), "archive", revision]).stdout
    target.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(target, filter="data")


@functools.lru_cache(maxsize=4)
def find_library(expected_hash, configured=None):
    candidates = [Path(configured)] if configured else sorted(Path("/nix/store").glob("*-source"))
    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if not (candidate / "lib/modules.nix").is_file():
            continue
        try:
            observed = command(["nix", "hash", "path", str(candidate)]).stdout.decode().strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        if observed == expected_hash:
            return candidate
    raise unittest.SkipTest("The selected Nixpkgs source is not available locally")


def expression(matrix, library, old, new, selector, original, attribute="", core_args=None, shared=None, legacy=None, probe=None):
    # Encode paths as JSON strings converted by Nix; no shell interpolation.
    args = {"oldBrowser": old, "newBrowser": new, "selector": selector,
            "originalSelector": original}
    args.update(core_args or {})
    values = " ".join(name + " = builtins.toPath " + json.dumps(str(path)) + ";" for name, path in args.items())
    if shared is not None:
        values += " sharedConfiguration = (builtins.fromTOML (builtins.readFile " + json.dumps(str(shared)) + ")).browser-ux;"
    if legacy is not None:
        values += " legacyConfiguration = (builtins.fromTOML (builtins.readFile " + json.dumps(str(legacy)) + ")).browser-ux;"
    if probe is not None:
        values += " failureProbe = " + json.dumps(probe) + ";"
    return "(import " + json.dumps(str(matrix)) + " { lib = import " + json.dumps(str(library / "lib")) + "; " + values + " })" + attribute


def locked_input(lock, route):
    def resolve(edge):
        if isinstance(edge, str):
            return edge
        node = lock["root"]
        for name in edge:
            node = resolve(lock["nodes"][node]["inputs"][name])
        return node
    node = lock["root"]
    for name in route:
        node = resolve(lock["nodes"][node]["inputs"][name])
    return lock["nodes"][node]["locked"]


def require_binding(actual, expected):
    if actual != expected:
        raise ValueError("RETAINED_MODULE_SOURCE_BINDING_CHANGED")


def evaluate_matrix(composition, browser, work, matrix=None, selector=None, original=None, core=None, control=None, bindings=None):
    if not shutil.which("nix") or not browser.is_dir():
        raise unittest.SkipTest("Nix and the Browser UX source checkout are required")
    bound = json.loads(bindings.read_text()) if bindings else None
    history = command(["git", "-C", str(composition), "log", "--reverse", "--format=%H", "--",
                       "lib/browser-ux-host-selection.nix"]).stdout.decode().splitlines()
    if not history:
        raise unittest.SkipTest("The adopted selector history is required")
    public_revision = bound["failedBaseline"]["publicOwnerCommit"] if bound else history[0]
    public_lock_raw = command(["git", "-C", str(composition), "show", public_revision + ":flake.lock"]).stdout
    public_lock = json.loads(public_lock_raw)
    old_locked = locked_input(public_lock, ["browser-ux_dinas"])
    old_revision = old_locked["rev"]
    new_revision = (bound["retainedSourceCommits"]["browser-ux"] if bound else
                    command(["git", "-C", str(browser), "rev-parse", "refs/heads/deploy"]).stdout.decode().strip())
    if bound:
        require_binding(hashlib.sha256(public_lock_raw).hexdigest(), bound["failedBaseline"]["publicPortableLockSha256"])
        require_binding(old_locked["rev"], bound["failedBaseline"]["selectedPublicLockedInputs"]["browser-ux_dinas"]["rev"])
    old, new = work / "old-browser", work / "new-browser"
    extract_commit(browser, old_revision, old)
    extract_commit(browser, new_revision, new)
    lock = json.loads((new / "flake.lock").read_text())
    nixpkgs = lock["nodes"]["nixpkgs"]["locked"]
    library = find_library(nixpkgs["narHash"], os.environ.get("BUX_TRANSITION_NIXPKGS"))
    if original is None:
        original = work / "original-selector.nix"
        original.write_bytes(command(["git", "-C", str(composition), "show",
                                      public_revision + ":lib/browser-ux-host-selection.nix"]).stdout)
    matrix = matrix or composition / "tests/browser-ux-host-transition.nix"
    selector = selector or composition / "lib/browser-ux-host-selection.nix"
    core_args, core_sources, shared = {}, {}, None
    if core is not None:
        old_core_rev = locked_input(public_lock, ["core"])["rev"]
        new_core_rev = (bound["retainedSourceCommits"]["core"] if bound else
                        command(["git", "-C", str(core), "rev-parse", "HEAD"]).stdout.decode().strip())
        if bound:
            require_binding(old_core_rev, bound["failedBaseline"]["selectedPublicLockedInputs"]["core"]["rev"])
        for name, rev in (("oldCore", old_core_rev), ("newCore", new_core_rev)):
            core_args[name] = work / name
            extract_commit(core, rev, core_args[name])
            core_sources[name] = rev
        if control is not None:
            config = tomllib.loads(control.read_text())
            active = Path(os.path.expandvars(config["flake"]["active"])).expanduser()
            selected_key = next(k for k, v in config["sources"].items()
                                if Path(os.path.expandvars(v["checkout"])).expanduser().resolve() == active.resolve())
            selected_rev = config["snapshot"]["sources"][selected_key]
            selected_lock_raw = command(["git", "-C", str(active), "show", selected_rev + ":flake.lock"]).stdout
            selected_lock = json.loads(selected_lock_raw)
            selected_core_rev = locked_input(selected_lock, ["composition-nd", "core"])["rev"]
            selected_browser_locked = locked_input(selected_lock, ["composition-nd", "browser-ux_dinas"])
            # Bind both selected dependencies, rather than borrowing the public
            # Browser UX revision on the strength of an incidental match.
            require_binding(selected_browser_locked["rev"], old_revision)
            if bound:
                historical = bound["selectedHistoricalGraph"]
                require_binding(selected_rev, historical["selectedSnapshotRevision"])
                require_binding(hashlib.sha256(selected_lock_raw).hexdigest(), historical["portableLockSha256"])
                require_binding(selected_core_rev, historical["selectedLockedInputs"]["core"]["rev"])
                require_binding(selected_browser_locked["rev"], historical["selectedLockedInputs"]["browser-ux_dinas"]["rev"])
            core_args["selectedOldCore"] = work / "selectedOldCore"
            extract_commit(core, selected_core_rev, core_args["selectedOldCore"])
            core_sources["selectedOldCore"] = selected_core_rev
            core_sources["selectedSnapshotRevision"] = selected_rev
            core_sources["selectedOldBrowser"] = selected_browser_locked["rev"]
            core_sources["selectedPortableLockSha256"] = hashlib.sha256(selected_lock_raw).hexdigest()
            shared = control.parent / "browser-ux.toml"
    legacy = work / "legacy-browser-ux.toml"
    legacy.write_bytes(command(["git", "-C", str(composition), "show",
                                public_revision + ":config/default/browser-ux.toml"]).stdout)
    expr = expression(matrix, library, old, new, selector, original, core_args=core_args, shared=shared,
                      legacy=legacy if legacy.is_file() else None)
    prefix = ["nix", "eval", "--offline", "--impure", "--json", "--expr"]
    # Reproduce the error separately to prove the closed-schema failure family.
    broken = expression(matrix, library, old, new, original, original, ".actualOldModuleAccepts")
    failure = subprocess.run([*prefix, broken], capture_output=True, timeout=120)
    reproduced = failure.returncode != 0 and b"browser-ux.configuration.replacements" in failure.stderr
    rows = json.loads(command([*prefix, expr]).stdout)
    rows["reportedFailureReproduced"] = reproduced
    failures = {}
    if core_args:
        for name, expected_option in {
            "coreFirstPrivateCurrentRejects": "browser-ux.configuration.replacements",
            "browserFirstPublicCurrentRejects": "programs.browserBaseline.replacementPolicyOwnership",
            "browserFirstPrivateCurrentRejects": "programs.browserBaseline.floorp",
        }.items():
            probe = expression(matrix, library, old, new, selector, original, core_args=core_args,
                               shared=shared, legacy=legacy, probe=name)
            result = subprocess.run([*prefix, probe], capture_output=True, timeout=120)
            missing = re.findall(rb"The option [`']([^`']+)[`'] does not exist", result.stderr)
            observed = sorted({value.decode() for value in missing})
            rows[name] = result.returncode != 0 and observed == [expected_option]
            failures[name] = {"returnCode": result.returncode, "missingOptions": observed,
                              "stderrSha256": hashlib.sha256(result.stderr).hexdigest()}
    return {"schema": "browser-ux-old-new-module-matrix-v1", "status": "PASS" if all(rows.values()) else "FAIL",
            "actualSources": {"oldBrowser": old_revision, "newBrowser": new_revision, **core_sources,
                              "publicBaselineCommit": public_revision,
                              "publicPortableLockSha256": hashlib.sha256(public_lock_raw).hexdigest(),
                              "nixpkgs": nixpkgs["rev"], "nixpkgsNarHash": nixpkgs["narHash"]},
            "selectorSha256": hashlib.sha256(selector.read_bytes()).hexdigest(),
            "originalSelectorSha256": hashlib.sha256(original.read_bytes()).hexdigest(),
            "failure": "The option `browser-ux.configuration.replacements' does not exist" if reproduced else None,
            "configurationSha256": hashlib.sha256(shared.read_bytes()).hexdigest() if shared else None,
            "publicConfigurationSha256": hashlib.sha256(legacy.read_bytes()).hexdigest() if legacy.is_file() else None,
            "scope": "Actual committed closed Browser UX modules and selected core option declarations; no composed-host derivation claim",
            "transitionFailures": failures, "bindingsSha256": hashlib.sha256(bindings.read_bytes()).hexdigest() if bindings else None,
            "tests": rows}


class ActualBrowserModuleTests(unittest.TestCase):
    def test_actual_old_and_new_schemas(self):
        composition = Path(__file__).resolve().parents[1]
        browser = composition.parent / "browser-ux_dinas"
        scratch = os.environ.get("BUX_TRANSITION_SCRATCH")
        with tempfile.TemporaryDirectory(prefix="browser-ux-modules-", dir=scratch) as directory:
            core = composition.parent / "darwin-is-not-a-snowflake"
            result = evaluate_matrix(composition, browser, Path(directory), core=core if core.is_dir() else None)
        self.assertEqual(result["status"], "PASS", result["tests"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--composition", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--selector", type=Path)
    parser.add_argument("--original-selector", type=Path)
    parser.add_argument("--core", type=Path)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--bindings", type=Path, help="Retained failed-baseline and adopted-source binding document")
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=False)
    try:
        result = evaluate_matrix(args.composition, args.browser or args.composition.parent / "browser-ux_dinas",
                                 args.scratch, args.matrix, args.selector, args.original_selector, args.core, args.control, args.bindings)
    except unittest.SkipTest as error:
        result = {"status": "NOT_RUN", "reason": str(error)}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "tests": len(result.get("tests", {}))}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
