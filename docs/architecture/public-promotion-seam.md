# Public Promotion Seam Architecture

## Overview

The public promotion seam in `composition-nd` enables routine promotions of
registered public-capable `*-nd` modules without requiring steady-state source
edits to `flake.nix`, `tests/`, or documentation when a leaf module's release
identities (version, Git revision, release hash, skill count, or corpus fingerprint)
change.

## Workflow separation

```text
Routine compatible release:
  Leaf domain qualifies immutable revision
  -> Public non-mutating candidate qualification (tools/qualify-promotion)
  -> Targeted public lock selection (separate core-owned transaction)
  -> Public aggregate qualification (nix flake check & host builds)
  -> Private successor promotion
  -> Separately authorized host activation

Registry / interface / configuration ownership change:
  Composition source / registry.json / bootstrap work required

Core / shared-contract / ownership-cut change:
  Core flake and matched composition work required
```

## Public promotion contract (`lib.promotion`)

The public promotion contract is a versioned, machine-readable output exported
by the composition flake:

- `schema`: `composition-nd-public-promotion-v1`
- `version`: `2`
- `composition`: Public composition metadata (`name`, `repositoryUrl`)
- `system`: Target architecture (e.g. `aarch64-darwin`)
- `modules`: Authoritative list of registered module records derived from `registry.json`
- `releaseChannels`: Exact `{ policy = "explicit-head-refs-v1"; refs = ...; }`
  mapping derived from registry-owned `releaseRef` fields and keyed by every
  registered direct input name
- `packageSetIdentity`: Authoritative package-set identity from `core.lib.packageSetIdentity`
- `hosts`: List of exported Darwin host configuration names
- `checks`: Stable names of public flake checks (`application-claims`, `contracts`,
  `agent-namespace`, `lock-provenance`, `registered-input-checks`, `tests`,
  `virtual-machines`)
- `qualification`: Entry point declaration (`tools/qualify-promotion`, mode `exact-candidate`)
- `promotableInputs`: Direct registered inputs eligible for promotion

## Candidate qualification engine (`tools/qualify-promotion`)

The candidate qualification tool tests candidate input revisions non-mutatively:

1. **Strict validation**:
   - Accepts only registered direct inputs in `registry.json`.
   - Rejects unregistered, transitive, or core inputs.
   - Requires exact grammar: `<canonical-repository-url>?rev=<40-lowercase-hex>`.
   - Rejects fragments, moving refs, extra query parameters, duplicate parameters, whitespace, or subdirectories (`dir`).
   - Verifies candidate repository URL matches the registered canonical repository URL.
   - Validates per-command timeout via `--timeout-seconds` (finite float in `[1.0, 7200.0]`, default `900.0` seconds).
2. **Bytecode suppression invariant**:
   - Sets `sys.dont_write_bytecode = True` in all direct Python entry points (`tools/qualify-promotion`, `tools/bootstrap`, `tools/check-inherited-lock-provenance`, `python/promotion_qualifier.py`).
   - Ensures zero `__pycache__` directories or `*.pyc` files are generated during execution even when `PYTHONDONTWRITEBYTECODE` is unset in the environment.
3. **Nix-observed candidate resolution**:
   - Executes `nix flake metadata <root> --override-input <input> <candidate-ref> --json --no-write-lock-file`.
   - Strictly extracts locked Git revision and repository URL from the locked dependency graph.
   - Verifies observed revision and URL match requested candidate identities.
4. **Strict promotion contract validation**:
   - Evaluates `lib.promotion` with input override and strictly decodes `composition-nd-public-promotion-v1`.
- Enforces exact allowed keys at all levels, ordering, and registry agreement before executing any build.
   - Requires contract version 2, exact release-channel policy and key parity,
     and closed short/full head-ref normalization across registry, source, and lock.
5. **Process tree containment and bounded stream capture**:
   - Executes all subprocesses in independent process groups (`start_new_session=True`).
   - Signal handlers installed before subprocess spawn on the main thread intercept `SIGINT` / `SIGTERM`, terminate the process group, reap direct children, and restore signal handlers.
   - Enforces an authoritative monotonic execution deadline plus a bounded cleanup budget (grace termination, reap polling, and reader join).
   - Dedicated supervised streaming reader workers (`_stream_reader_worker`) continuously hash streams and record terminal state (`eof`, `error`, `bytes_read`, `truncated`). Clean EOF is required from both stdout and stderr.
   - Staged process group containment: if direct child exits on `SIGTERM`, polls process group liveness via `os.killpg(pgid, 0)` combined with `proc.poll()`, escalates to `SIGKILL`, verifies group disappearance over bounded interval, and fails closed with `cleanup_disposition = "incomplete"` if disappearance cannot be proven.
   - Non-zero leader exit or leader exit with open pipes or surviving descendants is always treated as command failure (`surviving-descendants`, `drain-timeout`).
   - Retains raw byte payloads (`stdout_raw`, `stderr_raw`) for authoritative decoding and strict hash verification.
6. **Strict UTF-8 machine decoding boundary**:
   - Diagnostic previews decode with `errors="replace"` to preserve operational visibility without crashing.
   - Machine JSON parsing (`nix flake metadata ... --json`, `lib.promotion`) operates strictly on raw bytes with `errors="strict"` after size and truncation validation, failing closed under `malformed-provider-output` on invalid UTF-8.
7. **Override evaluation without worktree mutation**:
   - Passes `--no-write-lock-file` and `--override-input <input> <candidate-ref>` on all Nix subprocesses.
   - Builds every declared check and every declared host system in sequence, stopping immediately on any failure.
   - Snapshotting protected files (`flake.lock`, `flake.nix`, `registry.json`) requires non-symlink regular files, descriptor open with `O_RDONLY | O_NOFOLLOW`, descriptor identity matching path identity (`st_ino`, `st_dev`), loop read to EOF, stable post-read re-`fstat` size and mtime, and SHA-256 digest computation.
   - Strict validity predicate `is_snapshot_valid` ensures all three protected files exist, are ordinary regular files, and have valid digests.
   - Strict snapshot comparison `check_snapshot_identical` compares mode, inode, device, size, bytes, mtimeNs, and SHA-256 digest.
   - Verifies snapshot integrity at entry, immediately after each subprocess checkpoint, and at closeout; fails closed under `protected-file-mutation` if any mutation occurs.
8. **Machine-readable result and zero persistent writes**:
   - The qualification tool performs zero persistent writes into the composition worktree.
   - Emits exactly one valid JSON document to `stdout` with `--json` adhering to `composition-nd-promotion-result-v1` for every outcome (including CLI argument validation errors and signal interruptions).
   - Classifies failures into closed vocabulary: `request-validation`, `registry-contract`, `promotion-contract`, `candidate-resolution`, `command-failure`, `protected-file-mutation`, `malformed-provider-output`, `internal-error`.
   - Returns exit code 0 if and only if `outcome == "pass"`, returning nonzero (1) for all failure outcomes.
   - Structural pass invariant: `outcome == "pass"` is impossible if any protected identity boolean (`lock.byteIdentical`, `tree.byteIdentical`) is false or null, or if any snapshot is invalid.

## Core layout transition exception (DINAS-ROOT2)

Ordinary native promotion (`tools/qualify-promotion`) intentionally accepts only
registered domain leaf inputs from `registry.json`, evaluates candidate inputs
non-mutatively via Nix input overrides, and strictly preserves source declarations
in `flake.nix`. It does not authorize core input promotions or layout mutations.

Core layout transitions (such as the DINAS-ROOT2 migration removing the historical
`?dir=nix-darwin` subflake selector to target the repository root) use the accepted
structural authorization helper:
`authorize_core_lock_transition(..., allow_layout_transition=True)`.
This helper emits and validates the `nix-update-core-layout-transition-v1` schema,
verifying that:
- The direct `core` node moves alone while preserving logical Git transport URL.
- Historical `dir = "nix-darwin"` is removed from both `original` and `locked` records.
- Core advances strictly to the accepted post-flatten revision, with dynamic lock
  identities generated by Nix.
- Core's owned child closure (`localConfig`, `nix-darwin`, `nixpkgs`,
  `nixpkgs-unstable`, `repo-map-src`) and root input topology remain byte-identical.
- All nodes outside the core closure remain unchanged.

Ordinary promotion rules are not weakened; the structural helper serves as the
bounded, owner-authorized exception for matched structural transitions.

## Invariants and security boundaries

- `composition-nd` does not activate host systems.
- Selection into `flake.lock` remains explicit, immutable, and exact.
- Private composition names, operator secrets, and machine-local configurations
  remain outside public composition contracts and source code.

## Release-channel lifecycle

Main is development by default. The contract declares the release branch. The
lock pins the exact release commit. `nix-update` does not advance the release
branch. This source-plus-lock migration is one-time; later compatible releases
are lock-only. Promotion and activation remain separate.
