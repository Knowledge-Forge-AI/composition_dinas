# Consolidated editor source qualification

DINAS-DEVUX-MERGE2B produces the public ownership cut for the published
Developer UX release `9dfb1072d0f4bb131373ab60babc473b6f8cc662`. The publication
receipt and remote `refs/heads/deploy` identified that same revision before
selection. This report describes source qualification; dispatcher review and
publication remain separate from live deployment.

## Source and lock

The registry removes `vscode-nd` and changes `developer-ux` to `copy-default`.
The portable `developer-ux.toml` is byte-identical to the selected leaf default;
the former default is absent. Ordinary registry aggregation consumes the
leaf's default module, typed configuration, top-level claims and allowlist.
The synthetic duplicate test uses its existing VS Code component metadata.
Generic promotion adverse cases now use a remaining registered subject. The
old Petdex/release-channel transition retains an explicit historical registry.

`nix flake update developer-ux-nd localConfig --flake path:.` exited 0 with
Nix 2.31.5 and wrote the actual public `flake.lock`. Semantic comparison found:

- Developer UX advances to the receipt revision on the unchanged deploy ref.
- The obsolete donor node and its root/follows edges are removed.
- Every other node and retained follows edge is unchanged, including core,
  nixpkgs/nix-darwin, APGR, agent and entertainment selections.
- `localConfig` retains its relative path and parent record. This lock format
  has no separate content hash for that node. Resolved-source checks confirm
  the new default contents and old default absence within the containing
  source; no graph identity was hand-edited.

## Observed verification

The working source and a disposable exact Git materialization both use the
real public lock and portable defaults. Public qualification supplies no input
overrides. Nix qualification uses `--no-write-lock-file --no-update-lock-file`;
builds additionally use `--no-link`.

| Command or observation | Result |
| --- | --- |
| `tests/run` | Exit 0; 111 tests, no skips |
| `nix build <flags> <public>#checks.aarch64-darwin.{contracts,application-claims,tests,lock-provenance,registered-input-checks}` (five explicit installables) | Exit 0 for working source and exact Git source |
| `nix flake metadata <public> --json <flags>` | Exit 0; resolved public graph equals the checked-in lock, selects the receipt revision, and contains no live donor input |
| `nix eval --json <flags> <public>#applicationClaims` and `#darwinConfigurations --apply <topology-probe>` | Exit 0; truthful leaf claims and both hosts have one VS Code owner/cask/settings writer/extension writer, no Petdex, APGR 0.8.1 and `agent-clis` |
| `nix eval --raw <flags> <public>#darwinConfigurations.<declared-host>.system.drvPath` | Exit 0 for both declared hosts in both public materializations |
| `nix build <flags> <public>#darwinConfigurations.<declared-host>.system` | Exit 0 for both declared hosts in both public materializations |
| Resolved default/allowlist comparison | Exact leaf default; leaf allows `developer-ux.schema` and `developer-ux.vscode`; composition projects only the owner table |
| Disposable successor `contracts`, `application-claims`, `public-checks` | Exit 0 with exact public Git source and disposable portable configuration |
| Disposable successor claims and both-host topology | Exit 0; one VS Code owner/cask/settings writer/extension writer; accepted APGR and agent namespaces preserved |

`<public>` denotes the working `path:` reference or the disposable immutable
`git+file:` reference. Host identifiers and full logs remain local. The successor
uses the accepted default counterpart and non-writing overrides only for its
public source and its own portable configuration. It does not replace the
public Developer UX input. Its source lock remains provenance; this is not
qualification of the real successor production lock.

Nix reused valid store outputs. These check results do not claim every child
derivation rebuilt or repeat job A's host suites and parity totals. The leaf's
published qualification remains its own evidence, including its documented
sandbox omissions. The 111 public host-side tests ran without skips. Two
scratch-harness issues were corrected: the topology probe needed the existing
package `pname or name` fallback, and JSON parsing needed stdout separate from
Nix override warnings. Neither required a product change or concealed a
failing child check.

## Disposition and next boundary

The initial public worktree was clean; no retained product edits required
adoption. Product edits remain unstaged for dispatcher review/finalization.
The donor, real higher-level source/lock and operator control files are not
modified. There is no activation, release-ref movement, private publication,
or live editor/styling mutation in this job.

After dispatcher publication, pass its actual public commit to the successor
owner. That owner must apply the matched default/contract counterpart,
generate and qualify its real lock and hosts, and separately perform the
authorized operator file transition before attended deployment. Do not keep
the old and new control files together permanently.

## Terminal review disposition

Disposition: amend

Rationale: Adopt all fourteen producer paths in the stage ledger, including
the new portable default and this report. Both new files are required in
dispatcher finalization; they remain intentionally unstaged here. Clarify the
README disclosure permission to distinguish the preserved public donor from
current input registration. Retain the historical lock-transition fixture and
tool unchanged; their historical revision is not the current selection.

Qualification evidence: The closer reran `tests/run`: exit 0, 111 tests, no
skips. Fresh working-source Nix metadata, all five public checks, claims,
both-host topology, both host derivation evaluations and both host builds
exited 0 with lock writing and lock updates disabled. Final
`nix flake check path:. --no-write-lock-file --no-update-lock-file` also
exited 0. The resolved graph
equals the real lock; the resolved source contains the new default and lacks
the old one. The remote deploy ref matches the publication receipt, and the
portable default matches the selected leaf. All generic qualifier cases are
preserved by exact subject substitution.

The closer inspected and reused the producer's successful exact-Git and
disposable-successor command records and structured logs, reconfirming their
single-owner and both-host topology assertions. The retained public Git
materialization is clean and bound to its recorded reference. Its executable
source, defaults, lock and tests match the final candidate; only the closer's
README clarification and this disposition section differ. Public qualification
has no input overrides. The successor overrides only its public source and
portable configuration. Those runs are reused evidence, not fresh closer
execution or qualification of the real successor lock.

Unresolved concerns: No blocking source concern remains. Dispatcher inclusion
of both new files and publication are pending externally. Historical migration
debt is retained as out of scope. There is no second independent review after
these revisions. The real successor selection, operator file transition and
live deployment remain with their designated owners.
