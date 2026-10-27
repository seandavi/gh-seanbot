---
status: proposed
---

# Reuse official Actions authentication and bound local operations

GitHub Actions and local harnesses have different credential lifecycles. Use GitHub's maintained `actions/create-github-app-token` for Actions, rather than a custom minting step; keep ordinary CI secretless and limit App smoke checks to explicitly enabled trusted manual execution. For local use, implement a small one-operation CLI that mints a fresh token per invocation, stops before expiry, and attempts revocation afterward.

## Considered options

- **Custom token minting in both environments:** duplicates the official Action's masking and job-cleanup responsibilities without a clear benefit.
- **Persistent broker with background renewal immediately:** a stronger eventual deployment boundary, but its policy and isolation requirements depend on the unresolved harness inventory and runtime credential-delivery decisions.
- **Export tokens or replace the global gh login:** simple but exposes credentials to additional processes and risks confusing human and agent identity.

## Consequences

The implemented local slice is not a persistent broker, unrestricted harness launcher, or automatic token refresher. It supports fixed-host repository REST calls and isolated HTTPS pushes; each subsequent invocation renews credentials. Long-running sessions, alternate write paths, and OS isolation require follow-up after operator inventory and policy approval.

The Actions example expects an explicitly provisioned private-key deployment copy; its GCP-to-Actions delivery policy remains an operator decision. No CI test needs that key. Implementing this slice does not accept the broader rollout or attribution policy, nor prove live App enforcement.
