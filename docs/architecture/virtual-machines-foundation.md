# Virtual machines source foundation

The suffix-free `virtual-machines` owner uses direct input and checkout
`virtual-machines_dinas`, canonical repository
`git+ssh://git@github.com/lair001/virtual-machines_dinas.git`, and release channel
`refs/heads/main`. Its appended registry entry uses the existing `copy-default`
mechanism: `config/default/virtual-machines.toml` becomes
`config/virtual-machines.toml` only when the destination is absent.
Existing configuration remains unchanged on repeated bootstrap.

The portable default is copied byte-for-byte from the selected leaf. It keeps
`enable = false`, `tooling = true`, the base profile at 2 CPUs / 2048 MiB,
and the separate KDE profile at 4 CPUs / 6144 MiB. Composition imports the
ordinary DINAS module and its package identity, empty application claims, and
owner-table allowlist through the generic registry interface. Host dependencies
follow `core/nixpkgs` and `core/nix-darwin`; the separately pinned `linux` input
does not follow host packages. Guest modules and Vagrant implementation remain
leaf-owned.

## Qualification boundary

`checks.aarch64-darwin.virtual-machines` is included in the promotion contract.
It compares dormant host state against core without the leaf, verifies composed
defaults, package identity, owner projection, release mapping and guest/host
separation, and builds/runs command help with disposable enabled settings and
`tooling = false`. It checks that only the command package is added and that
casks and launchd remain unchanged. The generated aggregate activation script
can change when the system package set changes; it is never executed here.

Native projection tests override only a temporary copy of portable defaults.
They test partial owner-table defaults, custom resources, invalid tables and
unknown options. The bundled-tooling case observes the actual core admission
policy: denial must produce the leaf's specific Vagrant BSL-1.1 prerequisite
error; admission must evaluate successfully. Denial is an expected negative
test, not enabled bundled-tooling success. `tooling = false` supplies commands
without supplying a compatible external Vagrant wrapper.

The Nix check uses `builtins.tryEval` for the bundled-tooling result; it cannot
distinguish the prerequisite diagnostic from another evaluation failure. The
specific error text is asserted by `tests/test_virtual_machines.py` with native
Nix available, run via `python3 -B -m unittest discover -s tests -p
'test_virtual_machines.py' -v`. That test is skipped inside the Nix tests
derivation, which has no Nix executable. Aggregate success alone therefore does
not qualify the diagnostic. The empty leaf application-claims assertion is a
dormant source-foundation requirement to revisit when promoting a Fusion claim.

The source foundation claims no Fusion application and does not create VM
instances during host activation. Mutable projection is explicit through
`dinas-vm project`; its portable destination is `~/virtual-machines`.
Source acceptance does not certify image construction, VM operation, or box
redistribution. No VM/security service prerequisite is added.

## Separate next work

1. **Core:** if still required, admit only `vagrant` while preserving other
   nonfree restrictions. Package admission does not activate a host.
2. **First image:** explicitly choose a local ARM64 Linux build route and test
   whether it provides the pinned image helper's KVM requirement. Do not assume
   Fusion nested KVM, an external server, a cloud purchase, or reuse of an
   existing VM. If KVM is unavailable, investigate and test a non-KVM image
   generation route. No builder is established by this composition change.
3. **Attended integration:** run the current `tools/bootstrap --core <core-checkout>
   --composition <composition-checkout> --local-root <control-root>` after
   authorization. Promote through the appropriate higher-level composition,
   explicitly enable the owner configuration, and separately authorize host
   activation. Derive promotion and deployment commands from the selected tools
   at that time. The raw control directory must never become a Nix input.
4. **Live qualification (not run):** build and retain a box, create two
   independently named canaries, test base boot, KDE, audio/graphics, mutable SSH
   bootstrap key replacement across reboot, and recreation of one canary without
   changing the other. Existing quarantine profiles remain outside this phase.
