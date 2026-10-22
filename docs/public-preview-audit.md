# v0.0.1 preview review scope

The public preview preserves the complete registry and composition machinery.
The public Git history starts from a curated snapshot and excludes private
operational journals and personal machine identifiers. Only Core and Browser UX
are public dependencies at this release; the existing references to the remaining
unpublished adapters are intentionally retained with authorized-access
prerequisites. Their implementation and private history are not copied here.

Review includes file/history secret scanning with Betterleaks, local pinned
Semgrep OSS security rules with metrics disabled, local Malskanner classification,
manual privacy/governance review and source contract checks. Scanner output is
triaged rather than treated as a security certification. Operator subprocess
sinks in the bootstrap and qualifier use argument vectors and explicit authority;
a generic sink finding alone is not evidence of command injection. No credential
validation, external probing, scanner cloud upload or host activation is part of
this publication review.

Core's public source pin and inherited dependency closure must match the exact
Core preview. Other adapter revisions remain independently pinned. Local contract
tests are distinct from complete authenticated graph qualification. An
unauthenticated checkout cannot yet build the entire composition. See the
[README](../README.md) and [SECURITY.md](../SECURITY.md) for access requirements
and reporting. No package registry publication accompanies this preview.

The nested Core parent-relative configuration path lacks an independent NAR hash.
Direct ordinary source verification therefore fails closed on this unmaterialized
graph. Passing evaluation is not an ordinary deployment qualification; the
operator's separately materialized snapshot is the scope of the retained native
plan-only evidence. No guard is relaxed to bypass that distinction.
