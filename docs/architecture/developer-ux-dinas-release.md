# Developer UX DINAS release handoff

The canonical repository is `lair001/developer-ux_dinas`. Registry schema
`nix-darwin-composition-registry-v2` binds input and checkout
`developer-ux_dinas`, logical owner `developer-ux`, release ref
`refs/heads/deploy`, and control file `config/developer-ux.toml`.
The lock selects the immutable release; configuration remains schema 1 with
the existing `developer-ux.vscode` table and literal dotted setting keys.

This structural transition selects substantive Antigravity functionality:
the host-configured `antigravity-styling` operator provides application/package
identity, per-control readiness, remembered artwork selections and authenticated
asset restoration. Artwork restoration leaves watermark visibility unchanged;
explicit unit clear forgets that unit, and vendor restore clears all selections.
It is not an intermediate rename-only release.

Repository identity is separate from persistent ownership. Preserve
`/var/db/developer-ux-nd`, CSS marker
`developer-ux-nd antigravity-ide-styling:v2`, journals and consumed plans.
The new operator reads old state and writes visual state v3 on explicit mutation.
An older operator cannot read v3: retain the 0.2.0 operator for inspection,
recovery and restore after any future v3 mutation. A host rollback does not
downgrade visual state.

Host installation does not invoke styling reconstruction, adoption, asset
import, visual trials or CSS mutation. Those remain explicit operator actions.
Dormant recovery and forensics outputs are not installed in ordinary PATH.
When needed, select them through
`lib.mkAntigravityOperatorTools { config; pkgs; }` so they bind the exact
consumer-selected styling package.

The consolidated VS Code component remains the sole editor owner. No standalone
`vscode-nd` input is restored. The historical remote `lair001/vscode-nd` remains
intact; local retirement is pending D. Its preparation record requires fresh
identity/ref/consumer checks and a verified private backup of the complete donor,
including ignored metadata, before any local removal.

Use the selected leaf's `docs/developer-ux-dinas-migration.json` for exact
resource mappings. Change only authenticated default or managed checkout
references. Preserve custom paths, generated output locations and immutable
store resources; normal matched deployment replaces the latter. Do not rewrite
historical references or perform global link reconciliation.

The successor owner must integrate the dispatcher-published public revision
through its own structural contract and qualification. The attended deployment
and donor retirement remain separate campaign steps. No upward dependency or
consumer-specific identity is introduced here.

At attended deployment, D must re-derive the host package and activation delta
from its exact selected graph. The recorded version change, zero package
additions/removals and store-hash-only activation differences are retained
producer evidence, not fresh host-build evidence from the terminal revision.

The inherited `repo-map-src` lock node contains two operator-specific absolute
path fields. It is unchanged by this release and is outside the current privacy
guard's surface list. Core owns replacing that source with a portable selection;
the public owner must then regenerate the inherited lock through the maintained
source workflow and extend guard coverage. This disclosure does not resolve the
existing exposure. Do not hand-edit lock identities or copy the private values
into remediation records.
