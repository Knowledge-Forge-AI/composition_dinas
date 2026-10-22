# NIX-DARWIN-PROMOTE1 Private Successor Handoff

## Overview

This handoff document provides architectural guidance for the separately pinned
higher-level private composition layer to consume the generic public promotion
contract established in `composition-nd` (`NIX-DARWIN-PROMOTE1`).

## Public promotion contract consumption

The public composition exports a machine-readable, versioned promotion contract:

```nix
lib.promotion
```

Schema: `composition-nd-public-promotion-v1`, Version: `2`.

The private layer should consume `lib.promotion` to discover:
- `modules`: ordered list of registered public modules, their canonical URLs, checkouts,
  and configuration ownership;
- `packageSetIdentity`: the canonical package-set identity shared across core and composition;
- `hosts`: public host names to compose into operator host configurations;
- `checks`: authoritative public check names (`application-claims`, `contracts`,
  `lock-provenance`, `registered-input-checks`, `tests`);
- `qualification`: non-mutating qualification entry point (`tools/qualify-promotion`).
- `releaseChannels`: exact registered-input release branches under policy
  `explicit-head-refs-v1`.

## Elimination of duplicated public product coupling

The private composition layer should:
1. Stop carrying duplicated assertions of public domain release versions, Git hashes,
   or per-leaf product package/cask inventories.
2. Delegate public module qualification to the public candidate qualifier
   (`tools/qualify-promotion`) rather than repeating leaf checks in the private layer.
3. Use the public `applicationClaims` validator (`lib.applicationClaims.attachOwner`
   and `lib.applicationClaims.assertValid`) to validate private claims combined
   with public aggregate claims exactly once.
4. Derive public host definitions through `lib.mkDarwinConfiguration` rather than
   re-implementing public module and overlay aggregation.

## Non-mutating candidate qualification in private layer

To qualify a proposed `composition-nd` revision without lock mutation:
1. Provide a private-layer qualification entry point that accepts an exact candidate
   `composition-nd` reference (`?rev=<40-hex>`).
2. Run candidate qualification with `--override-input composition-nd <candidate-ref>`
   and `--no-write-lock-file`.
3. Verify that private claims, package-set identity, and operator-host coherence
   are preserved before authorizing a lock update.

The next phase must select the exact published public migration commit, update
its structural contract to version 2, recursively accept the embedded public
version-2 contract, and declare `composition-nd -> refs/heads/main`. It must
preserve every private domain revision and private aggregate invariant, prove
candidate private host qualification, and perform no host activation.

## Execution and activation boundaries

For the consolidated Developer UX release, consume the actual public commit
published after the source cut, replace the former editor portable default
with the selected leaf's `developer-ux.toml`, and generate the higher-level
lock through its normal source-maintenance surface. Preserve the public
composition's checked-in Developer UX selection and release channel. The
public registry already supplies the new configuration ownership; do not
restore the donor or introduce a second claim owner or renderer.

Disposable successor qualification is evidence for that counterpart, not a
change to the real higher-level selection or operator files. The successor
owner must qualify its real lock and hosts before the separately authorized
operator configuration transition and attended deployment.

- The public layer and candidate qualifiers never activate host systems.
- Lock updates remain strictly decoupled from host activation.
- The private successor layer remains separately pinned and must stop short of
  activation during qualification.
