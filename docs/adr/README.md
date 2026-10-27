# Architecture decision records

ADRs capture architectural choices and their trade-offs; the [rollout plan](../rollout.md) tracks setup and verification.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-shared-github-app-identity.md) | Use one shared GitHub App for agent activity | Proposed |
| [0002](0002-scoped-credentials-without-human-fallback.md) | Isolate agent credentials and fail closed | Proposed |
| [0003](0003-reuse-actions-auth-and-bound-local-operations.md) | Reuse official Actions authentication and bound local operations | Proposed |

Table legend: **Proposed** means recorded for discussion, not approved or implemented.

Use sequential `NNNN-short-title.md` filenames. Keep each record focused on the context, choice, and meaningful alternatives or consequences. Mark decisions accepted only when agreed; record superseding decisions with links when direction changes. Revisit decisions when constraints or evidence change—ADRs explain reasoning, not immutable constraints.

Commit trailers and text-footer syntax remain [proposed conventions](../rollout.md#proposed-conventions) in the rollout plan, not accepted architectural decisions.
