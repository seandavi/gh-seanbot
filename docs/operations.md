# Operations and acceptance

## Scope and credential boundary

The local CLI supports repository REST calls via `gh api` and a one-commit HTTPS push to a named branch. It does not replace all of `gh`, provide GraphQL, configure commit identity, run arbitrary harness processes, or log a bot into a browser. Start each operation with a fresh token; launch another invocation to renew credentials. Commands are stopped before expiry rather than refreshed underneath a long-lived process.

For API calls, the launcher uses a disposable HOME/config directory, an allowlisted environment, fixed `github.com` host, and explicit repository-relative paths. Human tokens, gh debug settings, proxy/netrc settings, SSH agent access, and inherited git configuration environment variables are not passed through. This does not modify the human's global login.

For pushes, an isolated bare repository borrows only the source repository's object directory via git alternates. Source local/global credential helpers, remotes, URL rewrites, extra headers, and hooks are not copied into the network operation. A git helper reads the installation token from the child's environment, not its arguments or a persisted token file. The destination is always `https://github.com/OWNER/REPO.git`; no force push or deletion option is exposed.

Trust the installed `git`, `gh`, Python environment, PATH, temporary directory, source object store, and caller. The operator must secure policy, key, audit directories, and their ancestors. The launcher itself has access to the private key. A caller with the same OS permissions can still read human credentials or the key directly, invoke alternate binaries, manipulate audit files, or escape process supervision. Restrict those accesses through OS/service isolation before treating this as an enforceable broker. Browser, SSH, direct API, CI, and unrestricted shell write paths outside this CLI remain separate integration work.

The lifecycle audit contains identifiers, repository, capability, timestamps, exit code, and revocation outcome, not commands, request/response bodies, JWTs, keys, or tokens. It is not an immutable action ledger and does not retain session transcripts or prove footer authenticity. Run IDs must be linked to retained session records by the harness/operator. Do not use sensitive data as an identifier. API response output is intentionally passed through to the terminal and may itself contain private repository content; protect terminal/session logs accordingly.

A mint or initial audit failure prevents execution. If the granted repository or permissions differ from the request (apart from an implicit `metadata: read`), the launcher revokes the token and fails without running the command; the audit log records `failed` and the revocation outcome appears only in the error message. Token revocation runs after command success or failure; revocation failure produces a nonzero CLI result and an audit event. A process crash, SIGKILL, machine loss, or interruption during minting can prevent cleanup; token expiry remains the ultimate bound. Temporary directories and process groups are defense in depth, not containment of malicious processes.

## GitHub Actions: reuse the official token action

The repository uses:

- `actions/checkout`, with credential persistence disabled, for ordinary CI.
- `astral-sh/setup-uv` for locked Python test/lint/type-check jobs.
- GitHub's `actions/create-github-app-token` for the opt-in manual App smoke workflow. It handles minting, masking, and post-job revocation; we do not implement a competing Actions minting step.

Actions are pinned to commit SHAs resolved from their upstream tags during implementation. CI requires no App key and runs on pull requests without privileged credentials. Do not add `pull_request_target`, checkout arbitrary PR revisions with App credentials, or pass tokens to untrusted repository scripts.

The App smoke workflow does not check out or execute repository code. It runs only on `main`, after explicit opt-in, requests issues-read on the current repository only, and prints only the result count from a repository API call. A successful smoke run proves basic authenticated API access, **not** the complete bot actor/negative-permission acceptance suite. GitHub's post-job cleanup is best effort; tokens still expire if a job cannot clean up.

To enable, Sean must:

1. Install the App on this repository, or copy the workflow to the chosen disposable test repository.
2. Set repository variable `APP_CLIENT_ID`.
3. Choose and authorize key delivery. The included workflow expects Actions secret `APP_PRIVATE_KEY`, an explicitly provisioned deployment copy of the GCP Secret Manager source of truth. Never commit the key. Mirror and rotate the copy deliberately; this repository does not provision or sync it. If a stored Actions copy is undesirable, replace delivery with an approved GCP workload-identity/secret-fetch step before enabling.
4. Set `SEANBOT_APP_SMOKE_ENABLED=true` and dispatch **App authentication smoke test** on `main`.
5. Record the run URL and results in the live-acceptance issue; never paste credentials or private payloads.

Leave this workflow disabled until key delivery and installation policy are approved. A skipped job is not a successful authentication test.

## Remaining operator work

- [#3: App registration, installation, and key delivery](https://github.com/seandavi/gh-seanbot/issues/3).
- [#4: Architecture/attribution approval, merge policy, inventory, and retention](https://github.com/seandavi/gh-seanbot/issues/4).
- [#5: Live positive and negative acceptance, then retire bootstrap credentials](https://github.com/seandavi/gh-seanbot/issues/5).

All are assigned to `seandavi`. The tracking issue stays open until deployment and acceptance are complete. Proposed ADRs are not implicitly approved by implementing an offline testable slice.

## Acceptance evidence

Offline tests cover signed JWT claims, installation matching, explicit token request scope, redacted failures, granted-scope verification with revocation on mismatch, unsafe key/audit files, policy denials, footer round trips, child environment isolation, fixed API paths, subprocess deadlines, isolated push configuration, and revocation after command failure. These tests use generated keys, mocked HTTP, local git repositories, and stubbed network commands. They do not contact GitHub as an App.

Before rollout, run the full [acceptance checklist](rollout.md#5-verify-in-a-test-repository) on the actual installation. In particular, verify bot actor records for all intended writes, actual denied writes outside scope, renewal/expiry/revocation, behavior with existing human credentials, and repository approval/bypass rules. Test push behavior with the actual git/GitHub versions and confirm authorship separately. No live App verification has been performed by this implementation.

## References checked during implementation

- [GitHub installation-token documentation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app): explicit repository and permission narrowing; one-hour tokens.
- [GitHub JWT documentation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app): RS256, client ID issuer, clock-skew allowance, maximum ten-minute expiry.
- [GitHub CLI environment reference](https://cli.github.com/manual/gh_help_environment): token precedence, config directory, host/repository overrides, debug controls.
- [Official token Action](https://github.com/actions/create-github-app-token): client ID/key inputs, selected repositories and permissions, post-job revocation.

References establish API/tool contracts, not evidence that our installation is configured correctly.
