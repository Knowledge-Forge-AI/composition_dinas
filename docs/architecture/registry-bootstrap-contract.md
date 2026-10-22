# Composition registry and bootstrap contract

`nix-darwin-composition-registry-v2` defines the structured public composition
registry. The registry is an object with exact `schema` and `modules` keys, and
every authored-order module entry has exact `name`, `input`, `url`, `releaseRef`,
`checkout`, and `bootstrap` keys.

Names, input bindings, checkout names, and normalized repository URLs are
unique. Authored array order defines module and overlay order. Unknown keys,
unsupported strategies, absolute paths, parent traversal, and path or symlink
escape fail closed.

Each `releaseRef` is one normalized full `refs/heads/...` branch ref. Tags,
symbolic refs, wildcards, refspecs, remote-tracking refs, `HEAD`, reflog syntax,
parent traversal, control characters, `.lock` suffixes, and ambiguous separators
fail closed. Registry order remains module order; release-channel maps are keyed
by registered direct input name and are derived from this field rather than a
second hand-maintained map.

Main is development by default. The contract declares the release branch, and
the lock pins the exact release commit. `nix-update` does not advance the release
branch.

## Lifecycle and bootstrap boundaries

Bootstrap follows registry and configuration ownership changes, not ordinary
lock-only revision promotions.

- **Routine compatible release**: An existing registered module qualifies a new
  immutable revision, which is qualified non-mutatively via `tools/qualify-promotion`
  and selected into `flake.lock`. No bootstrap invocation or configuration changes
  are required.
- **Registry / interface / configuration ownership change**: Adding or removing a
  module, changing bootstrap strategies, or introducing new machine-local
  configuration requirements invokes `tools/bootstrap` to establish checkouts and
  configuration templates.
- **Core / shared-contract changes**: Upstream contract modifications in core or
  shared libraries may require matched composition and bootstrap work.

## Bootstrap strategies

The three bootstrap strategies are:

- `repo-tool`: execute one reviewed executable below the registered checkout
  with a fixed `--config PATH` argument vector;
- `copy-default`: copy one regular source below the composition-owned
  `config/default` tree;
- `none`: establish and validate the checkout and origin without creating or
  validating machine-local configuration.

The `none` bootstrap object has exactly one key, `strategy`. It has no
`config`, `source`, or `executable`, contributes no configuration module or
allowlist projection, and does not require the module input to export a
`configAllowlist`. This exemption is configuration-only: registry uniqueness,
module import, overlay import when exported, package-set identity,
application-claim owner attachment, and whole-set validation still apply.

Destination paths remain below `$NIX_DARWIN_LOCAL/config`. Existing
destinations are preserved. Bootstrap performs no filesystem discovery, glob,
shell-string interpretation, overwrite, update/reset, switch, or activation.
It validates the composition with `nix flake check --no-write-lock-file` and
an explicit local core override. Only `repo-tool` entries also override their
module input with the authenticated local checkout, because their executable is
checkout-owned. `copy-default` and `none` entries retain locked module inputs:
bootstrap still authenticates their checkout origins and preserves existing
configuration, but mutable leaf work does not replace the selected release in
the composition check.

The tracked `config/default` tree is the operator-neutral `localConfig` input
when this flake is evaluated by itself. Bootstrap checks that neutral input; it
does not pass the raw operator control directory to Nix because doing so would
copy unrelated private control files into the store. A selected higher-level
composition or the rebuild launcher overrides `localConfig` with a constrained
evaluation projection under `$NIX_DARWIN_LOCAL/eval/generations`. The
composition never scans the machine-local config directory directly.
Run bootstrap after a registry change and before the first rebuild that selects
the changed composition; this establishes every newly allowlisted config file
before the launcher renders its evaluation projection.

The control directory is shared by the selected top-level composition. A file
materialized for this registry is valid only while the selected top level
registers it; switching to a different registry requires an explicit bounded
control-file transition. Bootstrap does not remove obsolete files.

Bootstrap validates the exact top-level module-table shape without reporting
private values, and Nix evaluation names the module and filename when a
projection contract fails.

For each configuration-owning registered module, composition verifies that the
module-declared allowlist owns exactly its registry-derived TOML filename and
that every declared leaf remains below the module-name table. The launcher then
projects that single table as a unit. This preserves optional module values and
defaults without permitting cross-module configuration; Nix's typed module
remains the closed-schema validator for values inside the table.

## Consolidated Developer UX ownership

`developer-ux` owns VS Code through the ordinary default module of
`developer-ux_dinas`, selected on its existing `refs/heads/deploy` release channel.
The former `vscode-nd` input and registry row are retired. Its repository is
not deleted or modified by the composition.

Developer UX uses `copy-default`: `config/default/developer-ux.toml` becomes
`config/developer-ux.toml`. The portable default is copied from the selected
leaf and uses `[developer-ux]`, schema 1, and nested `developer-ux.vscode`.
The leaf's typed configuration and exported allowlist remain authoritative;
the composition projects the owner table using its generic validation above.

This is a source/registry/default transition followed by native Nix lock
generation, not a compatible lock-only promotion. Once the declared deploy
branch identifies the qualified release, the targeted source-maintenance
command is `nix flake update developer-ux_dinas localConfig --flake path:.`.
Review the generated graph for the canonical input and intended leaf selection,
preserving all unrelated selections and follows. Relative `localConfig` nodes
may retain their path/parent record while their contents change with the
containing source; qualification must inspect the resolved default as well as
the lock record.

The in-place Developer UX repository rename preserves `refs/heads/deploy`, the
suffix-free owner and configuration schema. Current repository, input and
checkout references use `developer-ux_dinas`; runtime state and historical
migration fixtures retain their original identities. The matched release and
rollback boundaries are documented in the [release handoff](developer-ux-dinas-release.md).

Bootstrap preserves existing destination files and does not remove obsolete
control files. A separately authorized operator transition must convert the
old owner file, keep its backup outside the enumerated control directory, and
leave only the new owner file before projection and deployment. Public checks
use portable defaults, never the raw shared control directory.

## Canonical APGR DINAS repository and owner migration

The public APGR module has migrated from `lair001/agentic-praxis-grimoire-nd` to
the canonical DINAS repository `lair001/agentic-praxis-grimoire_dinas`.

Logical ownership is suffix-free:
- Registry name: `agentic-praxis-grimoire`
- Flake input: `agentic-praxis-grimoire_dinas`
- Canonical URL: `git+ssh://git@github.com/lair001/agentic-praxis-grimoire_dinas.git`
- Release ref: `refs/heads/main`
- Checkout: `agentic-praxis-grimoire_dinas`
- Configuration: `config/agentic-praxis-grimoire.toml`
- Default source: `config/default/agentic-praxis-grimoire.toml`

The adapter's closed allowlist owns `agentic-praxis-grimoire.toml` with keys
prefixed by `agentic-praxis-grimoire.`. The portable default is updated to
`[agentic-praxis-grimoire]`.

This structural migration updates the direct input name, follows, registry
record, and locked resolution via targeted Nix lock update:
`nix flake update agentic-praxis-grimoire_dinas localConfig --flake path:.`.
Separately pinned higher-level compositions that pin historical adapter URLs
rely on GitHub repository rename redirects until they execute their matched
structural migration.

## Browser UX registration

`browser-ux` is registered in `registry.json` using `copy-default`:
- Logical owner: `browser-ux`
- Flake input: `browser-ux_dinas`
- Canonical URL: `git+ssh://git@github.com/Knowledge-Forge-AI/browser-ux_dinas.git`
- Public preview release ref: `refs/heads/main`
- Checkout: `browser-ux_dinas`
- Configuration: `config/browser-ux.toml`
- Default source: `config/default/browser-ux.toml`
- Bootstrap strategy: `copy-default`

The initial BUX1 registration exposed only the schema and KeePassXC fields.
The public Browser UX input now owns a larger closed typed configuration seam,
including extension policy, an Apple/Proton trial and browser replacements. The
composition's portable default leaves KeePassXC, trials, replacements and mail
integration disabled. Its contract tests assert the complete inactive defaults
and reject unknown keys and invalid types. Source availability does not qualify
host delivery or authorize a browser-role cutover; those source gates remain held.

Bootstrap copies `config/default/browser-ux.toml` to `config/browser-ux.toml` if the
destination does not exist, and preserves existing destinations without overwrite
or activation. Configuration defaults and allowed keys are owned by the selected
adapter; the composition does not reimplement its option schema.

## Virtual machines dormant registration

The `virtual-machines` owner uses the same generic registration and copy-default
contract; see [Virtual machines source foundation](virtual-machines-foundation.md).
