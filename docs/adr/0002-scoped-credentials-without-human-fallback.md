---
status: proposed
---

# Isolate agent credentials and fail closed

A separate App identity is ineffective if an agent can silently fall back to the human's token, git credential helper, SSH key, or browser session. Route agent authentication through a common credential boundary that issues short-lived installation tokens scoped to the intended repositories and required permissions, passes them only to the intended processes, and fails closed when authentication or renewal fails. Preserve the human's independent login rather than replacing global credentials.

## Considered options

- **Prompt-only routing:** simple, but cannot enforce identity when tools inherit human credentials or use alternate write paths.
- **Replace the global GitHub login:** reduces some configuration work but disrupts human workflows and does not cover independent git or browser credentials.
- **Isolated process credentials with a common minting boundary:** requires integration for every harness and write path, but makes intended identity explicit and testable.

## Consequences

Store the App private key in GCP Secret Manager; keep it out of ordinary agent processes where feasible. Installation tokens must be narrowed explicitly because an installation may span multiple repositories. Never log tokens or deliver them as command-line arguments.

A broker with an enforceable capability allowlist provides a stronger boundary than distributing the key to harnesses. Whether this requires a persistent broker or another isolated launcher/service remains undecided pending the harness inventory. Process-scoped environment variables alone are not a sandbox: access to human credentials and alternate write paths must also be restricted before claiming enforcement.

Acceptance requires testing denied cross-repository writes, ungranted operations, expiry and renewal, and absence of human-credential fallback across API, git, and browser paths. Browser writes in a human session remain outside this boundary and must be prohibited or handled through a separately verified identity workflow.
