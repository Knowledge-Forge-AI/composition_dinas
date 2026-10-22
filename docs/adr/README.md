# Architecture Decision Records

`composition-nd` ADRs are organized by the local calendar year and month in
which the logical document was first introduced to the repository.

```text
docs/adr/YYYY/MM/NNNN-<slug>.md
```

Use the introducing commit's committer timestamp and preserve the calendar date
encoded in that timestamp's numeric offset. Do not convert the timestamp to UTC
or to the reviewing machine's timezone. Later edits or moves do not change the
assigned introduction month.

ADR numbers form an independent, repository-global four-digit sequence. Keep
assigned numbers and basenames stable, never reuse a number, allocate the next
number from accepted `main`, and update active repository links during an
authorized move.

An ADR uses the Status / Context / Decision / Consequences shape. Status is
`Accepted` only when the decision's gates and its review are complete;
otherwise `Proposed`.

## Decision index

- [ADR 0001 — Public Nix-Darwin Composition, Module Registry, and Bootstrap Contract](2026/08/0001-public-nix-darwin-composition-module-registry-and-bootstrap-contract.md)
- [ADR 0002 — Inherited Core Lock Debt During Owner-Driven Migration](2026/08/0002-inherited-core-lock-debt-during-owner-driven-migration.md)
  clarifies the temporary treatment of exact lock debt inherited from accepted
  core while preserving the full-transitive publication gate.

## Disclosure boundary

`composition-nd` is publication-capable. An ADR here may name the core flake,
this repository, `vscode-nd`, and generic `*-nd` module repositories. It must
not name any operator-designated non-public repository, module, Git URL, flake
input, checkout path, option name, registry entry, or configuration identity.
Refer to non-public layers generically, as "an operator-designated non-public
composition".
