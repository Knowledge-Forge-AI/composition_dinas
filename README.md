# composition_dinas

**v0.0.1 public preview.** The full DINAS composition combines Core with an
explicit adapter registry. It aggregates modules, overlays, configuration
allowlists, command namespaces, application ownership claims and qualification
checks. Adapter implementations remain in the repositories that own them.

## Dependency availability

This publication intentionally retains the **full composition**, not a reduced
public-only variant. Core and Browser UX are public under Knowledge-Forge-AI.
The remaining registered repositories are being published separately over time.
Their existing SSH URLs and exact source pins are retained so the composition
continues to describe the complete graph. **An unauthenticated clone cannot yet
evaluate or build the entire graph.** It requires authorized read access to the
unpublished inputs, an SSH setup for the declared URLs, and any prerequisites of
those adapters. No unpublished adapter implementation is included in this repo.

| Logical owner | Input | Availability at this preview |
| --- | --- | --- |
| Core | `core` | Public |
| Browser UX | `browser-ux_dinas` | Public; public `main` channel |
| Agentic Praxis Grimoire | `agentic-praxis-grimoire_dinas` | Authorized access required |
| Entertainment | `entertainment-nd` | Authorized access required |
| Developer UX | `developer-ux_dinas` | Authorized access required |
| Agent | `agent_dinas` | Authorized access required |
| Virtual machines | `virtual-machines_dinas` | Authorized access required |

The registry remains the source of module names, input names, release channels,
checkout names and bootstrap strategies. Publishing an adapter later does not
silently update this lock. Select and qualify its exact revision deliberately.
Example host names match Core: `example-workstation` and `example-primary`.
No local machine control files or private development history are published.

## Qualification and operation

On a Mac with all declared input access and the required tools:

```sh
python3 -m unittest discover -s tests
nix flake check --no-build
./tools/qualify-promotion --help
```

The local Python contract tests can run without fetching the complete input
graph; tests that explicitly evaluate Nix need those inputs. Full composed host
qualification remains separate from source preview publication. Read the
[registry/bootstrap contract](docs/architecture/registry-bootstrap-contract.md)
and [promotion seam](docs/architecture/public-promotion-seam.md) before use.

The qualifier resolves an exact candidate, checks its contract and runs bounded
checks/builds without activating a host. Core owns attended promotion and
activation transactions. `tools/bootstrap` initializes absent configuration
files according to the registry; inspect its plan and choose a disposable root
before using it. This source preview does not activate or reconfigure a machine.

## License and contributions

Original project code is **AGPL-3.0-or-later**, with separate commercial terms
available from Samuel Leighton Lair at
[sam.lair@knowledge-forge.ai](mailto:sam.lair@knowledge-forge.ai).
Dependencies retain their own licenses.

Read [LICENSE](LICENSE), [NOTICE](NOTICE),
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md),
[CONTRIBUTING.md](CONTRIBUTING.md), [CLA.md](CLA.md) and [SECURITY.md](SECURITY.md).
Outside contributors use the pull request template and explicitly accept the
contribution terms. There is no package registry publication for this preview.
