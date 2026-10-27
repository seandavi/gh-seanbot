# Spec: verify minted token scope matches the request (#12)

Part of #1. Status: proposed (spec stage; no code written).

## Goal

`mint` (`src/gh_seanbot/auth.py`) requests an installation token for one repository and one permission profile, then accepts the 201 response without checking the scope GitHub granted. After this change `mint` compares the granted `permissions`, `repository_selection` and `repositories` in the response with the request. On any mismatch it revokes the token and raises `SeanbotError`, so `execute` never launches a command with an over-scoped token.

## Non-goals

- No change to the policy file, capability profiles, CLI arguments or exit codes.
- No new audit events or audit schema changes (see Risks).
- No live verification against GitHub. Live acceptance stays in #5.
- No change to the Actions path (`actions/create-github-app-token`, ADR 0003).
- No new dependencies. The code uses only `httpx`, `PyJWT` and the stdlib, as it does today.

## Design

### Policy file

Unchanged. No keys are added, and `load_policy` validation stays as it is. The expected scope comes from the existing `Policy` fields:

- `policy.permissions`: a copy of `PROFILES[capability]`, already sent as the request's `permissions`.
- `policy.repository`: `owner/name`. Its name part is already sent as the request's `repositories`.

### CLI

Unchanged arguments. A scope mismatch reaches `execute` as a `SeanbotError` raised by `mint`. The existing handling then applies:

- The audit log records `started` and then `failed`. `token` is still `None` in `execute`, so no `authenticated`, `finished` or `revoked` event is written, and `run_process`/`push` are never called.
- `main` prints `gh-seanbot: <message>` to stderr and returns exit code 1. Nothing is written to stdout.

Messages (exact strings; they never include the token, the response body or the granted values):

| Case | Message |
|---|---|
| Scope mismatch, revocation succeeded (DELETE returned 204) | `Minted token scope does not match the request; token revoked` |
| Scope mismatch, revocation failed | `Minted token scope does not match the request; revocation failed, token may remain valid until expiry` |
| Other validation failure after a token string was obtained (bad or past `expires_at`), revoked | `Could not validate the token response; token revoked` |
| Same, revocation failed | `Could not validate the token response; revocation failed, token may remain valid until expiry` |
| Any failure before a usable token string exists | unchanged: existing messages (`Repository does not belong to the configured installation`, `Installation token mint failed (HTTP n)`, `Could not authenticate the App or validate the token response`) |

### Response fields checked

These are the 201 response fields of `POST /app/installations/{installation_id}/access_tokens`. They were checked against the `installation-token` schema and example in GitHub's published OpenAPI description (`github/rest-api-description`, `api.github.com.json`). In that schema `permissions` is an object of `name -> "read" | "write" | "admin"` (`app-permissions`), `repository_selection` is `"all" | "selected"`, and `repositories` is an array of repository objects with `full_name`. Only `token` and `expires_at` are schema-required, so a missing `permissions` or `repositories` is treated as a mismatch, which fails closed.

A response matches the request only if all of the following hold:

1. `body["repository_selection"] == "selected"`.
2. `body["repositories"]` is a list with exactly one element, and that element's `full_name.lower() == policy.repository.lower()`. The comparison ignores case because GitHub repository names are case-insensitive and the policy may spell them differently from GitHub's canonical form. The installation lookup that runs before the mint already resolves the same `owner/name` with the same case-insensitivity. An extra repository, a different repository, an empty list, a missing key or a non-list all fail.
3. `body["permissions"]` is a dict equal to `policy.permissions`, or equal to `policy.permissions | {"metadata": "read"}`. Values are compared as exact strings, so `contents: "write"` granted for a `contents: "read"` request fails. Any other extra key fails, and so does any missing key or a `metadata` value other than `"read"`.

**Why `metadata: read` is allowed:** every GitHub App has repository metadata read access, and GitHub may report it in the granted permissions even when it was not requested. Without this one allowance, every live mint could fail. Whether GitHub actually adds it has **not been verified** live. #5 should record the observed response shape. If GitHub never reports it, the allowance stays harmless because metadata read is implicit for every installation token anyway.

Do not compare repository `id` or `owner.login`. `full_name` already carries the owner, and the policy has no repository id.

### Order of operations inside `mint`

1. Read and check the key, sign the JWT, verify the installation, then `POST` the token request. These steps are unchanged.
2. Non-201 status fails with the existing message. Nothing needs revoking.
3. `body = response.json()`, then `value = body["token"]`. If `value` is not a non-empty `str`, raise as today (the generic message). No revocation is attempted because there is no usable credential.
4. From this point on, every failure revokes `value` before raising. Checks:
   1. Parse `expires_at` into `InstallationToken(value, expires_at)` and require `remaining() > 0`. A failure here gives the "Could not validate the token response" message.
   2. Apply scope checks 1 to 3 above, in a private helper `_scope_matches(body, policy) -> bool`. A `False` result gives the "Minted token scope does not match the request" message. The helper returns `False` for wrong types and missing keys and never raises (for example, it uses `isinstance` checks and `.get`).
5. To revoke, call the existing `revoke(InstallationToken(value, datetime.now(UTC)), client)`, using the same client and the same `DELETE https://api.github.com/installation/token` with the token as Bearer. It returns `True` only on 204, and `revoke` already turns `httpx.HTTPError` into `False`. Raise `SeanbotError` with the suffix chosen by that result, `from None`.
6. Return the token only after every check passes.

Implementation constraint: the outer `except SeanbotError: raise` in `mint` must let these new `SeanbotError`s through unchanged. The generic outer handler must not wrap them, and no exception raised after step 3 may skip revocation. In step 4, catch `ValueError`, `KeyError`, `TypeError`, `AttributeError` and `OverflowError` around the expiry parse, then revoke. Do not let those exceptions reach the outer handler without revocation.

Leave `execute` in `cli.py` unchanged. Revocation for this failure lives in `mint`, which keeps the "never return an over-scoped token" guarantee inside the credential boundary instead of in each caller.

### Failure modes (all fail closed)

| Failure | Result |
|---|---|
| Extra permission, a higher permission level, a missing permission, or a missing or non-dict `permissions` | revoke; `SeanbotError`; exit 1; no launch |
| A different repository, an extra repository, an empty, missing or non-list `repositories`, an entry without a string `full_name`, or `repository_selection` other than `"selected"` | revoke; `SeanbotError`; exit 1; no launch |
| Expiry missing, unparseable, naive or already past, with a valid token string present | revoke; `SeanbotError`; exit 1; no launch |
| Revocation itself fails (non-204 or transport error) | `SeanbotError` saying the token may remain valid; exit 1; no launch. Expiry (at most one hour) is the remaining bound |
| Process killed between the 201 and the revocation | token stays valid until it expires. The existing operations.md caveat already covers this |

### Documentation changes

- `README.md` command reference (the paragraph beginning "Every invocation mints a fresh, narrowed token"): after "mints a fresh, narrowed token", add ", verifies that GitHub granted exactly the requested repository and permissions (revoking it otherwise)".
- `docs/operations.md`, "Scope and credential boundary", the paragraph beginning "A mint or initial audit failure prevents execution": add one sentence. "If the granted repository or permissions differ from the request (apart from an implicit `metadata: read`), the launcher revokes the token and fails without running the command; the audit log records `failed` and the revocation outcome appears only in the error message."
- `docs/operations.md`, "Offline tests cover …" sentence: add "granted-scope verification with revocation on mismatch" to the list.
- ADRs: no change. ADR 0002 already requires tokens "scoped to the intended repositories and required permissions" and failing closed. This change implements that requirement and does not change the decision. ADR status stays Proposed.

## Acceptance criteria

- [ ] With a mocked 201 response whose `permissions` contain an extra entry (for example `issues: write`), `mint` raises `SeanbotError` matching `scope does not match`, and the mock transport sees `DELETE /installation/token` with `Authorization: Bearer <minted token>`.
- [ ] Same for a response whose `repositories` names another repository (`owner/other`) or lists a second repository.
- [ ] Same for: a raised level (requested `contents: read`, granted `contents: write`), a missing permission, missing `permissions`, missing `repositories`, `repository_selection: "all"`, and `metadata: "write"`.
- [ ] A response granting exactly the requested permissions, or those plus `metadata: "read"`, for `full_name` `owner/test` or `Owner/Test`, returns the token and makes no DELETE request.
- [ ] If the DELETE returns non-204 after a mismatch, the error message contains `may remain valid`.
- [ ] A past or unparseable `expires_at` with a token string present also triggers the DELETE.
- [ ] No error message contains the token value. The existing redaction test still passes.
- [ ] An end-to-end `execute` call with a mismatched mocked response raises `SeanbotError`, never calls `run_process`, sends the DELETE, and leaves audit events `["started", "failed"]`.
- [ ] README and `docs/operations.md` are updated as described above. No ADR changes.
- [ ] `just check` passes (ruff check, ruff format --check, mypy, pytest).

## Test plan

All tests are offline and use `httpx.MockTransport` and the existing `policy` fixture (`tests/conftest.py`: repository `owner/test`, permissions `{"contents": "write"}`, installation 123). They do not contact GitHub.

`tests/test_auth.py`:

- Change `response_body()` to include `"permissions": {"contents": "write"}`, `"repository_selection": "selected"` and `"repositories": [{"full_name": "owner/test"}]`, so existing success tests still describe a valid grant.
- `test_mint_scopes_and_signs`: unchanged in intent. It now also asserts that no DELETE is sent (the request count stays 2).
- New `test_mint_accepts_implicit_metadata_read_and_case`: grant `{"contents": "write", "metadata": "read"}` with `full_name` `"Owner/Test"` and check that a token is returned.
- New `test_scope_mismatch_revokes` (parametrized over the mismatch cases in the acceptance criteria): the handler answers GET with `{"id": 123}`, POST with the modified body and DELETE with 204, and records requests. Assert `SeanbotError` matching `token revoked`, exactly one DELETE to `/installation/token` with the minted Bearer token, and that `SECRET` does not appear in the message. Use `"contents": "read"` in the request for the raised-level case by building the policy with `dataclasses.replace(policy, permissions={"contents": "read"})`.
- New `test_scope_mismatch_revocation_failure`: DELETE returns 500. Assert the message matches `may remain valid`.
- `test_bad_responses_are_redacted`: make the handler return 204 for DELETE. For the past-expiry and bad-date cases (token string present), also assert that one DELETE was sent. For `{}` and the 403 case, assert that none was sent.

`tests/test_cli.py`:

- New `test_scope_mismatch_never_launches`: monkeypatch `gh_seanbot.cli.load_policy` to return the fixture and `gh_seanbot.cli.run_process` to call `pytest.fail("launched")`. Capture `real = httpx.Client` and monkeypatch `httpx.Client` to `lambda **kw: real(transport=httpx.MockTransport(handler), **kw)`. The handler returns a body granting an extra `issues: write`. Assert `SeanbotError` matching `token revoked`, that a DELETE was seen, and that the audit events are `["started", "failed"]`. Use the real `mint` and `revoke`.

Run `just check`.

## Risks

- **`metadata: read` allowance is unverified.** If GitHub reports another implicit permission, live mints fail closed with the scope-mismatch message. That breaks commands but does not widen access. #5 should record the real response and revisit this list.
- **Case-insensitive repository match** relies on GitHub repository names being unique regardless of case. That holds on github.com, and the policy is limited to github.com.
- **Revocation outcome is not in the audit log for this path.** It appears only in stderr, because `execute` never receives the token. Adding a dedicated audit event would mean passing audit into `mint` or returning the token for the caller to revoke. Both widen scope. Revisit if the audit log needs to prove revocation of scope-mismatched tokens.
- **Mocked tests only.** They show the guard logic, not GitHub's real response shape or whether revocation works live.
