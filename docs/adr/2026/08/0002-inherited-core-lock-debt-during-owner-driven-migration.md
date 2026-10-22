# ADR 0002 — Inherited Core Lock Debt During Owner-Driven Migration

Status: Accepted

Date: 2026-08-10

Clarifies: [ADR 0001](0001-public-nix-darwin-composition-module-registry-and-bootstrap-contract.md),
only for transitive lock debt inherited from the accepted core during the
owner-driven migration program.

## Context

ADR 0001 requires this repository to register only public-capable modules and
to introduce no operator-designated non-public dependency. It also records that
a top-level flake lock contains the transitive graph of its inputs and that a
valid composition is not automatically a publication certificate while core
still contains grandfathered project footprints.

Those statements need a narrower transitional interpretation. A top-level
composition that depends on the accepted core necessarily inherits the exact
transitive nodes still required by that core. Some owner migrations may remain
unfinished even though this composition introduces no such dependency itself.
Requiring the composition's whole transitive graph to be publication-clean at
every individual owner-migration gate would sequence independent migrations
behind one another and contradict the accepted owner-driven program.

The distinction is between dependency ownership and eventual publication
cleanliness. It does not relax the information-flow boundary, make inherited
debt portable, or certify this repository for publication.

## Decision

### Direct and composition-owned dependencies remain public-capable

This repository may directly declare and register only public-capable
dependencies. Its `flake.nix`, registry, module inputs, configuration, tests,
fixtures, documentation, bootstrap, and error messages must not introduce an
operator-designated non-public or machine-specific dependency.

Every registered module must satisfy the same rule. `vscode-nd`, as the first
registered module, has no grandfathered exception in its own source or lock
graph.

Registration remains explicit and tracked. Nothing in this decision permits
filesystem discovery, a placeholder canonical URL, an inaccessible direct
input, or a machine-local direct input.

### Exact core-inherited lock debt is temporarily permitted

During the owner-driven migration program, `composition-nd/flake.lock` may
contain transitive nodes inherited solely through the accepted core input even
when those nodes are not yet publication-clean.

An inherited node is permitted only when all of the following are proved:

1. it is reachable beneath the exact accepted core input in the actual lock
   graph;
2. it was already present in the canonical graph of that accepted core base;
3. neither this repository nor a registered module introduced or altered it;
4. it has no second reachability path through a composition-owned or
   module-owned input; and
5. it is recorded privately as owner-migration-pending debt.

The qualification compares graph structure, not guessed names or textual
similarity. Composition-owned, module-owned, and core-inherited nodes are
classified from their actual input paths. The inherited portion is compared
with the exact core graph used by the candidate; an added node, changed locked
identity, or additional path is an unexplained delta and fails the gate.

### Inherited status cannot launder a direct dependency

A direct or registered dependency owned by this composition cannot be labeled
inherited merely because the same identity also appears below core. Provenance
is structural: a tolerated node must be reachable solely beneath the accepted
core input and must match that core graph.

No exception identity list is committed here. Operator-specific classification
and stricter disclosure needles remain in private operational configuration.

### Package-set and composition contracts are unchanged

This clarification does not change the one-package-set universe, package-set
identity equality, explicit module registry, allowlist collision rules, shell
contribution collision rules, overlay ordering, outward constructor
delegation, or the rule that only core invokes `darwinSystem`.

It does not permit an upper layer to flow information downward or allow this
repository to name, search for, or configure an operator-designated non-public
composition.

### Publication still requires full transitive cleanliness

Before any actual publication of this repository, its entire canonical
transitive lock graph must satisfy the publication policy. Unless a later
explicit publication decision changes that policy, the graph must contain:

- no machine-local absolute path;
- no prohibited non-public identity; and
- no dependency inaccessible to the intended public consumer.

Therefore:

```text
valid and build-qualified composition != publication-ready composition
```

Migration/build qualification proves that this composition introduces no new
dependency debt and correctly composes its public-capable modules. It does not
claim that accepted debt inherited from core is portable or publishable.

### Scope of clarification

For the owner-driven migration period, this decision supersedes only an
overbroad reading of ADR 0001 phrases such as "no non-public dependency of any
kind" or "fully fetchable" when those phrases are applied to exact transitive
nodes inherited solely from accepted core.

The binding interpretation is:

- no non-public or machine-specific dependency owned, registered, or directly
  introduced by this repository or its registered modules; and
- full transitive cleanliness remains a later publication-readiness gate while
  accepted core still carries grandfathered debt.

ADR 0001's direct dependency boundary and all other architectural decisions
remain unchanged.

## Consequences

Owner migrations may proceed independently while core still contains exact
grandfathered dependencies belonging to other owners. A composition lock can
truthfully represent that accepted core without converting inherited debt into
composition ownership.

Qualification becomes more explicit. It must classify direct and transitive
lock nodes structurally, compare inherited nodes with the exact core graph, and
fail on a new node, changed identity, or additional path. This is more work
than a flat string scan and prevents the transitional exception from becoming
an unbounded allowlist.

This repository can be build-qualified before it is publication-ready. Status
and operational evidence must state both properties separately. Eventual
publication remains blocked until owner migrations remove the inherited debt
and the full-transitive publication gate passes.
