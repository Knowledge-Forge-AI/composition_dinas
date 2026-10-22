# Security policy

DINAS Public Composition v0.0.1 is a public preview. Build-time tests and scanner results
have bounded coverage and do not establish a hardened deployed workstation.

Report suspected vulnerabilities privately to sam.lair@knowledge-forge.ai.
Include the affected version, relevant source locations and expected impact.
Do not send credentials, private configuration, browser profiles, vaults,
keys or raw operational logs. Use harmless local reproductions when useful.

Some unit tests intentionally contain synthetic secret-shaped strings to verify
redaction. Scanner findings in those fixtures require source-based triage; they
are not a reason to suppress a whole file or rule.

Installation and host activation are separate operations. Review the selected
source, local configuration, generated plan and privileged changes before
activating any configuration. Third-party packages retain their own policies.
