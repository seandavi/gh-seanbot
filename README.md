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

Copy [`examples/policy.example.toml`](examples/policy.example.toml) to gitignored `policy.toml`. Supply the App client ID, installation ID, absolute key path, private audit path, and repository/capability allowlist. Deliver the key from your secret manager; require mode `0600` and a private parent directory. Create the audit parent directory before use.

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

Every invocation mints a fresh, narrowed token, launches a single foreground operation, and attempts revocation afterward. The command deadline is the earlier of `--max-seconds` (default 900) and token expiry minus 60 seconds. There is no long-lived token export, in-process background refresh, or unrestricted agent launcher. This is credential hygiene for trusted callers, **not an OS sandbox or a deployed broker**.

## CI and rollout

Ordinary [CI](.github/workflows/ci.yml) is secretless. The opt-in [App smoke workflow](.github/workflows/app-smoke.yml) adopts GitHub's official `actions/create-github-app-token` rather than custom Actions authentication. See [operations and limitations](docs/operations.md) before enabling it.

[Tracking issue #1](https://github.com/seandavi/gh-seanbot/issues/1) links implementation and remaining operator work. Operator-owned issues are assigned directly to `seandavi`, not marked with a human-needed label.

## Architecture decisions

The [ADR index](docs/adr/README.md) records architectural choices and trade-offs:

- [0001: Use one shared GitHub App for agent activity](docs/adr/0001-shared-github-app-identity.md) — proposed.
- [0002: Isolate agent credentials and fail closed](docs/adr/0002-scoped-credentials-without-human-fallback.md) — proposed.
- [0003: Reuse official Actions authentication and bound local operations](docs/adr/0003-reuse-actions-auth-and-bound-local-operations.md) — proposed.

This document remains the setup and rollout plan; the ADRs record why the proposed architecture takes this shape.

## Goal

GitHub's actor should distinguish human activity from agent activity. Commit trailers and text metadata supplement that identity; they do not replace it.

Use one App across harnesses, with harness, model, and run identifiers recorded separately. The App name is provisional: GitHub assigns its bot identity when the App is registered.

## Identity boundaries

| Action | Proposed identity | Additional provenance |
| --- | --- | --- |
| PR creation, comments, issue/label edits, reviews | Shared App installation token | Structured footer where a body is available |
| Agent-initiated merge | Shared App installation token | Run-linked audit event |
| Human-initiated merge | Human's own authenticated session | GitHub's merge record |
| Git commits | Explicit Git author/committer configuration | Approved attribution trailer |
| Git pushes | Shared App installation token | Push actor plus commit metadata |

Table legend: identity is the account GitHub attributes an action to; provenance describes the execution that produced it.

App API authentication does **not** automatically set local commit authorship, commit signing, or web UI actions. Verify each separately. A human approval followed by an automated merge still produces an automated merge action; it should not be described as a human-performed merge.

## Proposed conventions

These are local conventions, not asserted ecosystem standards.

### Commits

Candidate trailer:

```text
Assisted-by: pi/<exact-model-id>
Agent-Run: <run-id>
```

Keep the responsible author's identity intentional. Never manufacture a human `Signed-off-by` certification. Decide separately whether a bot should be author or committer for unattended work.

The existing shared rule uses `Co-Authored-By` for code deliverables. Keep that rule until an explicit decision replaces it. Preserve repository-specific prose/manuscript disclosure policies.

Before adoption, verify any claimed external requirements—especially Linux kernel and DCO requirements—against current primary documentation. This plan does not rely on those claims being correct.

### PRs and comments

Append metadata at creation, preserving it when editing:

```html
<!-- agent: pi; model: provider/model-id; run: 73a5c183 -->
```

Use an opaque run ID, not a credential, private prompt, or local session path. Validate or escape metadata before interpolation into HTML comments. Start with PR bodies and agent-authored comments; define representation for review bodies and action types without editable text as needed.

A footer is provenance metadata, not proof: users can edit or copy it. Keep an independent execution record keyed by run ID. Do not rewrite human-authored content merely to attach attribution.

## Setup and rollout

### 1. Decide policy

- Choose the App owner, display name, and installation targets.
- Inventory harnesses and all write paths: `gh`, REST/GraphQL clients, git HTTPS/SSH, browser automation, CI, and merge tooling.
- Choose commit author/committer defaults and approve the trailer convention.
- Decide which repositories permit agent merges and which require independent human approval.
- Define run-log ownership, retention, and access controls; a run ID is only useful if its record survives.

### 2. Register and install the App

- Register the App under the chosen owner; disable unneeded webhook delivery and interactive authorization features.
- Install initially on one disposable or low-risk repository, using selected repositories rather than all repositories.
- Start with only necessary repository permissions: contents write for pushes/merges; pull requests write for PRs/reviews; issues write for issue comments and label operations. Confirm endpoint-specific permissions, including PR comments and labels, against GitHub's current documentation.
- Add workflow-file write permission only if required for agent-authored workflow changes. Do not request administration, organization, secrets, or other broad permissions by default.
- Record the App ID and installation ID as configuration. Store the private key in GCP Secret Manager, never in this repository or run logs. Choose runtime delivery and access policy during implementation.

### 3. Implement a narrow credential seam

Provide a shared launcher/token service that:

1. Identifies the approved installation and repository.
2. Uses the App private key to authenticate as the App and mint an installation token, narrowed to the required repositories and permissions where supported.
3. Delivers the token only to the intended child process; never prints it in normal output or puts it in command-line arguments.
4. Refreshes tokens before expiry and fails closed if minting or refresh fails.
5. Records run ID, harness, exact model ID, repository, requested capability, and outcome without credentials or sensitive payloads.

Installation tokens are short-lived, but installation scope can cover multiple repositories: narrowing is an explicit implementation responsibility, not an automatic per-repository guarantee.

Keep the private key out of ordinary agent processes where feasible. A broker with an enforceable repository/capability allowlist is a stronger boundary than a helper that hands every agent the key. Pick the deployment shape after inventorying local and remote harnesses.

### 4. Integrate each harness

- Supply the installation token to `gh` and API clients in a process-scoped environment; avoid replacing the human's global `gh` login.
- Configure git HTTPS authentication explicitly for the scoped process. Git SSH keys and existing credential helpers can otherwise bypass the App identity.
- Isolate inherited human tokens and credential fallback paths. Test that authentication failure cannot silently fall back to the human's account.
- Attach run metadata through a common helper rather than relying solely on prompts.
- Treat browser-driven GitHub writes as a separate path: prohibit them in the human session or supply an explicitly isolated supported identity workflow. App tokens do not log a bot into the browser UI.
- Preserve a separate, explicit human workflow for approvals and manual merges.

This rollout improves attribution; it is not a security sandbox unless agent access to human credentials and alternate write paths is actually restricted.

### 5. Verify in a test repository

Acceptance checks:

- Agent creates a branch, pushes a commit, opens a PR, comments, changes a label, submits a permitted review, and merges an authorized test PR.
- UI and API records identify the App for supported agent actions; commit author, committer, signature status, and trailers are checked independently.
- Human actions remain attributed to the human account.
- PR and comment metadata round-trips without duplication or corruption; run IDs resolve to retained records.
- A token cannot write outside its intended repository or perform ungranted operations.
- Expired/revoked tokens fail closed; renewal works without exposing secrets.
- Existing human git credentials and `gh` login cannot be used as fallback by the scoped agent process.
- Repository rules enforce the chosen approval policy. Verify App bypass lists and whether automated approvals can satisfy rules; never assume “required approval” means “required human approval.”
- Branch protection, rulesets, and merge-method behavior are tested with the actual repository configuration and GitHub plan.

### 6. Roll out incrementally

- Enable one harness and repository first; inspect actor records and run linkage.
- Extend to other harnesses only after their API, git, and browser write paths are covered.
- Update shared attribution memory only after the policy is approved and adopted.
- Join action records to run records in lookout later. Define metrics from verified fields rather than inferring assistance solely from actor names or trailers.

## Rollback

Revoke outstanding tokens as supported, suspend/uninstall the App if needed, and remove launcher integrations. Rotate the private key if exposure is suspected; do not assume key rotation alone revokes already-issued installation tokens. Keep agent writes disabled until identity routing is repaired rather than silently reverting to the human's token. Human work can continue through the separate human workflow.

## Primary references to verify during implementation

- [GitHub App authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app)
- [Generating installation access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
- [Choosing App permissions](https://docs.github.com/en/apps/creating-github-apps/setting-up-a-github-app/choosing-permissions-for-a-github-app)
- [GitHub CLI environment](https://cli.github.com/manual/gh_help_environment)
- [Protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

The installation-token, JWT, GitHub CLI environment, and official token Action documentation were checked during implementation; see [operations](docs/operations.md). Endpoint-specific permission behavior and repository rules still require live verification. The original rollout plan above remains the acceptance target, not a claim that deployment is complete.
