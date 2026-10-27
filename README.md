# gh-seanbot

Separate human and agent activity on GitHub using one shared GitHub App.

**Status:** local CLI and CI integration are implemented; live App setup and acceptance remain blocked on operator provisioning. The architecture and attribution policy still await approval. No App has been created by this project and no standing attribution rules have changed.

## Install and use

Requires Python 3.11+, `uv`, `gh`, and git on a POSIX host. Install from the live repository:

```sh
uv tool install git+https://github.com/seandavi/gh-seanbot.git
# Development checkout:
uv sync --locked
just check
```

Copy [`examples/policy.example.toml`](examples/policy.example.toml) to gitignored `policy.toml`. Run `chmod 600 policy.toml`: the CLI refuses a policy that is a symlink, not a regular file, or accessible to group/world (mode `0600` or stricter is required). Supply the App client ID, installation ID, absolute key path, private audit path, and repository/capability allowlist. `push` destination branches must start with a prefix from the optional `push_branch_prefixes` table (default `agent/`; entries end with `/`); this is checked before a token is minted. Deliver the key from your secret manager; require mode `0600` and a private parent directory. Create the audit parent directory before use.

```sh
gh-seanbot api --policy policy.toml --repo OWNER/REPO \
  --harness pi --model PROVIDER/MODEL --run RUN_ID \
  --capability read --endpoint issues

gh-seanbot footer --harness pi --model PROVIDER/MODEL --run RUN_ID \
  --body comment.md > attributed-comment.md
# Encode the attributed text as JSON yourself, then POST it:
gh-seanbot api --policy policy.toml --repo OWNER/REPO \
  --harness pi --model PROVIDER/MODEL --run RUN_ID \
  --capability issues --method POST --endpoint issues/123/comments --input payload.json

gh-seanbot push --policy policy.toml --repo OWNER/REPO \
  --harness pi --model PROVIDER/MODEL --run RUN_ID \
  --capability push --sha FULL_COMMIT_SHA --branch agent/example
```

Branch names follow git ref-format rules within letters, digits, `_`, `.`, `-` and `/`, and must start with a letter or digit.

API endpoints are relative to `repos/OWNER/REPO/`, without host overrides or query parameters. Segments may contain dots (for example `contents/README.md`), but segments made only of dots (`.`, `..`) are rejected. JSON request bodies come from files. The footer helper is explicit: API payloads are not silently rewritten, and human-authored content must not be reattributed.

Capabilities: `read` (contents, PRs, issues read); `issues` (issues write); `pull-requests` (contents read, PRs write); `push` (contents write); `merge` (contents and PRs write); `contribute` (contents, PRs, issues write). Choose the narrowest profile; permission grants do not constitute approval to merge.

Every invocation mints a fresh, narrowed token, verifies that GitHub granted exactly the requested repository and permissions (revoking it otherwise), launches a single foreground operation, and attempts revocation afterward. The command deadline is the earlier of `--max-seconds` (default 900) and token expiry minus 60 seconds. There is no long-lived token export, in-process background refresh, or unrestricted agent launcher. This is credential hygiene for trusted callers, **not an OS sandbox or a deployed broker**.

## CI, rollout, and acceptance

The [rollout and acceptance plan](docs/rollout.md) covers identity boundaries, proposed commit and footer conventions, setup steps, the live acceptance checklist, and rollback.

Ordinary [CI](.github/workflows/ci.yml) is secretless. The opt-in [App smoke workflow](.github/workflows/app-smoke.yml) adopts GitHub's official `actions/create-github-app-token` rather than custom Actions authentication. See [operations and limitations](docs/operations.md) before enabling it.

[Tracking issue #1](https://github.com/seandavi/gh-seanbot/issues/1) links implementation and remaining operator work. Operator-owned issues are assigned directly to `seandavi`, not marked with a human-needed label.

## Architecture decisions

The [ADR index](docs/adr/README.md) records architectural choices and trade-offs:

- [0001: Use one shared GitHub App for agent activity](docs/adr/0001-shared-github-app-identity.md) — proposed.
- [0002: Isolate agent credentials and fail closed](docs/adr/0002-scoped-credentials-without-human-fallback.md) — proposed.
- [0003: Reuse official Actions authentication and bound local operations](docs/adr/0003-reuse-actions-auth-and-bound-local-operations.md) — proposed.

The ADRs record why the proposed architecture takes this shape; the [rollout plan](docs/rollout.md) tracks setup and verification.
