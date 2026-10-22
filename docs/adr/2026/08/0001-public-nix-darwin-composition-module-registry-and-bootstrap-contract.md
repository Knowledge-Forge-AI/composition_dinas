# ADR 0001 — Public Nix-Darwin Composition, Module Registry, and Bootstrap Contract

Status: Accepted

Date: 2026-08-10

Supersedes: none.

## Context

The nix-darwin core flake is being reduced to a framework: a generic
configuration constructor, a machine-local control plane, a selection launcher,
a shell composition seam, bootstrap, and a publication-safe base workstation.
Project-specific integrations move out of core into `*-nd` module repositories
owned by the teams that own those projects.

Something has to combine them. A module repository exports modules, an overlay,
an allowlist and its own tests; it does not produce a Darwin configuration, and
it should not, because a configuration is a statement about one machine's whole
set of modules rather than about any one module.

Core cannot be that combiner either. If core registered optional modules it
would stop being independently buildable, would regain the coupling the
extraction exists to remove, and would need to know about every module that
might ever exist.

This repository is that combiner for modules that are safe to publish.

Two constraints shape it. First, it must have **no non-public dependency of any
kind** — lock graphs are transitive, so a composition inherits its inputs'
inputs, and one non-public module registered here would make the whole layer
unpublishable. Second, it must be able to sit underneath a further composition
without knowing that composition exists.

## Decision

### This repository is a complete top-level flake that requires core

`composition-nd` takes core as an input, registers public-capable `*-nd`
modules, and exports `darwinConfigurations.<host>` through core's constructor.
It does not reimplement the constructor, the host table, the control plane, the
projection, the launcher, or the shell seam. Those are core's, and a second
implementation of any of them would be a second source of truth.

It is selected as the top-level flake for a rebuild in exactly the same way core
is: through the machine-local control plane, or an explicit flake argument. The
layers are a dependency chain, not an activation stack — selecting this
repository does not activate core separately, and there is never more than one
top-level flake per rebuild.

### It aggregates; it does not implement

This repository owns:

- the registry of public-capable modules, as tracked flake inputs and a tracked
  registry;
- the composed Darwin module list;
- the ordered overlay list passed to core's constructor;
- the aggregated configuration allowlist;
- package-set identity assertions across every layer;
- aggregated generated shell contributions;
- host and public module configuration;
- checks that the composition itself is coherent.

It does not own package definitions, module implementations, module
configuration files, module shell fragments, or module tests. Those belong to
the module repositories, and a module's test must not live here as the only
proof that the module works.

The rule is deliberately blunt, because the failure mode is gradual: one
convenience wrapper, one shared helper, one "temporary" package definition, and
the composition becomes the monolith the extraction was meant to dissolve. If
something needs implementing, it needs a module repository.

### Registration is explicit and tracked

A module is registered by a tracked flake input plus a tracked registry entry.

Nothing is discovered. This repository does not glob sibling directories, scan a
checkout root, probe for an optional path, or infer a module from the presence
of a file. Filesystem discovery makes the composed system a function of what
happens to be on disk, which cannot be tested or reproduced — and it is
incompatible with concealment, because a public layer that searches for optional
neighbours has already disclosed that neighbours exist.

`vscode-nd` is the first registered module.

Other public module teams register after their own migration qualifies: their
repository implements the applicable `*-nd` exports, follows core's package set,
publishes its package-set identity, passes its own checks, and has proved parity
against the configuration that currently provides it. Registering an unfinished
module makes every consumer's build a function of that team's work in progress,
so this ordering is a requirement rather than a courtesy.

### One package set, and ordering is stated rather than inferred

Every registered module follows core's `nixpkgs`. Each publishes the package-set
identity it resolved against, and this repository asserts those identities are
equal across core, itself, and every module. A forgotten `follows` fails a check
instead of silently instantiating a second package set — which would otherwise
surface much later as a duplicated dependency or a version that nobody
selected.

Overlays are passed to core's constructor as an explicit ordered list, in the
order this repository declares. Overlay order is load-bearing whenever an
overlay reads its predecessor, so it is stated here rather than left as a
consequence of module evaluation order.

Module configuration reaches modules through extra modules, not through core's
host table. Core's table holds only facts core understands; a module-specific
key there would be a switch any layer could reach and that core could not
validate.

### It re-exports the extension surface an upper layer needs

This repository exports a constructor of the same extended shape core exports —
`{ hostName, hostConfig, extraModules, extraOverlays }` — which prepends its own
registered modules and overlays to whatever its caller passed and delegates
**once** to core. It does not invoke `darwinSystem` itself.

That re-export is not a convenience. Without it, a layer above this one has no
documented way to add modules and overlays while keeping a single invocation of
core's constructor, and would be forced into something the architecture
forbids: reaching into an input's internals, taking core as a second parallel
input, copying this registry, or writing a second constructor. Exporting the
surface is what lets an upper layer exist without this repository knowing
anything about it.

The resulting overlay order is total and outward: core's overlay, then this
repository's registered overlays in its declared order, then whatever its caller
passed. Later overlays may read earlier ones and never the reverse.

Also exported, for the layer above and for the selected top-level flake:
`darwinConfigurations.<host>` for this level, the aggregated `configAllowlist`,
this repository's `packageSetIdentity`, and its own `checks`. Registered module
names are unique across the composed set, allowlist keys are namespaced by
module, and a duplicate in either is an evaluation error rather than a silent
override — otherwise an upper layer could shadow a module here without either
side declaring it.

The normative contract is in the core repository's ADR 0001. This repository
implements it and does not restate it.

### No non-public dependency, and no knowledge of what sits above

This repository has no non-public input, registry entry, override, configuration
key, documentation reference, or test fixture. Its lock graph is fully fetchable
by anyone who can fetch its declared public inputs.

A generic higher-level composition may require this repository and register
modules an operator has designated non-public. That is a supported direction of
dependency, and it is the only one: information flows upward. This repository
does not name such a layer, does not depend on it, does not search for it, does
not test for its path, and does not document its identity. Every ADR, status
record, README, and code comment here refers to it generically or not at all.

### Bootstrap is explicit, idempotent, and stops short of activation

Core's bootstrap may clone this repository. It does not run this repository's
bootstrap — a lower layer running an upper layer's installer inverts the
dependency direction and makes core's behaviour depend on what is above it.

This repository's bootstrap, when implemented:

- installs the module checkouts it has explicitly registered;
- creates missing module configuration without overwriting existing
  configuration;
- renders public evaluation and shell contributions;
- validates package-set identity across the composed layers;
- validates the composition;
- stops.

It does not activate. Activation is separately authorized, always. It performs
no discovery: every checkout it installs is one it has registered. It is
idempotent, because a bootstrap that is only safe to run once is a bootstrap
nobody dares run.

### A valid composition is not a publication certificate

While core still carries grandfathered footprints belonging to teams that have
not yet migrated, this repository transitively inherits them. That makes it a
valid, buildable composition. It does not make it publishable.

Publication additionally requires that every remaining footprint violating the
publication policy has been migrated by its owner, and that the publication
gates pass: no non-public identity in any publication-capable tracked head, no
operator or machine identity in a repository about to become public, and a
published history containing no prohibited disclosure.

These two properties are tracked separately and must never be reported as one.

## Consequences

Public module teams get one clear registration point and one contract, and they
can migrate in any order without coordinating with each other.

This repository stays small by design. Every request to add implementation here
is answered with a module repository, which is more setup for the requester and
the reason the composition does not become a second monolith.

Its publishability is a function of core's remaining grandfathered footprints,
which it does not control. It will be buildable long before it is publishable,
and the gap is somebody else's migration backlog rather than a defect here.

Explicit registration means adding a module is a tracked change to this
repository, reviewed here. That is more friction than dropping a checkout into a
directory, and it is the friction that makes the composed system reproducible.

Nothing here reveals whether a further composition layer exists. That property
must survive every future change to this repository, including test fixtures,
error messages, and documentation examples, where it is easiest to lose.

## References

Normative sources in the core repository, at these repository-relative paths:

- `docs/adr/2026/08/0001-nix-darwin-core-subflake-extension-contract-and-owner-driven-migration.md`
  — the core boundary, the `*-nd` module output contract, and the layer
  extension contract this repository implements.
- `docs/architecture/subflake-migration-guide.md` — the protocol a team follows
  to migrate its footprint and register here.
