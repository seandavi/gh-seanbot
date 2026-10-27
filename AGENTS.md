# Agent instructions

## Workflow

- Track work in GitHub issues. Start implementation from an issue, work on a branch/worktree, and open a PR with closing references. Merge when checks are green; stop on real blockers.
- Maintain a tracking issue linking implementation and operator tasks. Assign every issue requiring Sean's action directly to `seandavi`; do not use a human-needed label or close it merely because tooling exists.
- Record consequential architectural choices in `docs/adr/`. Distinguish proposed, accepted, implemented, and verified. Revisit ADRs when evidence changes.
- Do not claim live App identity, permissions, or repository-rule enforcement based only on mocked tests. Record exactly what was tested.

## Tooling

- Use Python with `uv`, `just`, pytest, ruff, and a type checker. Keep the credential boundary small and testable.
- Keep secrets, private keys, local configuration, and execution logs out of git. Never print credentials, place them in command arguments, or include them in issues or PRs.
- Fail closed on credential errors. Never fall back to the human's credentials or modify the global human `gh` login.
- Treat process credential hygiene as defense in depth, not an OS sandbox. Document unsupported write paths and unverified security properties.
- Preserve existing commit attribution rules until a replacement is explicitly approved. For code deliverables use a truthful `Co-Authored-By` trailer; do not invent human sign-offs.

## Bootstrap identity

Until the App is provisioned and live acceptance passes, repository setup and development use the existing human-authenticated GitHub tooling under the user's authorization. This is an explicit bootstrap exception, not evidence that App routing works. Keep its retirement tracked as an operator task.

## Documentation

- Keep `README.md` usable as the installation and command reference; keep deployment and acceptance steps in `docs/`.
- Use `AGENTS.md` (this file) as the canonical agent instruction file.
- Provide examples with placeholders, never deployment credentials. Public docs should avoid unnecessary host-specific details.
