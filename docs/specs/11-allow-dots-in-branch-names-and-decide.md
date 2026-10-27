# Spec for #11: allow dots in branch names and decide endpoint dot handling

Issue: [#11](https://github.com/seandavi/gh-seanbot/issues/11), part of tracking issue #1.

## Goal

1. `gh-seanbot push --branch` accepts ordinary dotted branch names such as `release/1.2`, `agent/fix-v0.1` and `v1.0`. It still rejects every name that git would reject as `refs/heads/<branch>` within the allowed character set: traversal, a leading `.`, a `.lock` suffix, and similar cases.
2. `gh-seanbot api --endpoint` accepts dotted path segments such as `contents/README.md`, `contents/.github/workflows/ci.yml` and `compare/v1.0...v1.1`. It still rejects `.` and `..` path segments and every other path that is rejected today.

**Endpoint decision: allow dots.** `gh api` does not normalize dot segments. Checked with `gh` 2.100.0 and `GH_DEBUG=api` against an unreachable proxy: `repos/o/r/contents/a/../b` was sent as `GET /repos/o/r/contents/a/../b`. Any normalization would therefore happen on the server. Rejecting every segment made only of dots means no request path can climb out of `repos/OWNER/REPO/`. Within that prefix, the token's repository and permission narrowing (ADR 0002) bounds what a dotted path can reach. A dot-free rule therefore adds no safety, and it blocks the common `contents/<file>` endpoints.

## Non-goals

- Restricting push destinations by policy (#9). That issue may later add policy checks next to the branch check. This spec only changes the syntax check.
- Widening the character set. `@`, `~`, `^`, `:`, `?`, `*`, `[`, `\`, space, `%`, `{`, `}` and non-ASCII stay rejected in both branches and endpoints. Because `%` is excluded, the encoded form `%2e%2e` cannot be used to sneak in a traversal segment.
- Query strings, GraphQL, absolute URLs and changes to the HTTP methods.
- Rejecting git-valid but confusing branch names such as `HEAD` or `main.lock.x`. Today's behavior is kept: they are valid refs under `refs/heads/`.
- Calling `git check-ref-format` at runtime. It is not used for these reasons:
  - The `--branch` mode resolves `@{-N}` against the caller's current repository.
  - It would add a subprocess, and its environment, before the token is used.
  - It would duplicate a check that a pure function can do exactly for this character set.
- Any change to ADRs. No architectural decision changes.

## Design

All changes are in `src/gh_seanbot/runner.py`. No new modules, dependencies, CLI arguments, policy keys or exit codes.

### Policy file

Unchanged. No keys are added or changed.

### Branch validation (`push`)

This replaces the check at `runner.py:90-93`. It stays inline in `push()`, in the same place: after the SHA check and before any subprocess runs. The error message stays `"Invalid destination branch"`.

The branch is valid if and only if all of these hold:

1. `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", branch)` matches. The first character must be alphanumeric, so names cannot start with `-`, `.` or `/`.
2. `".." not in branch`.
3. `not branch.endswith(".")`.
4. Every component of `branch.split("/")` is non-empty, does not start with `"."`, and does not end with `".lock"`.

Rule 4 covers empty components (`foo//bar`, `foo/`) and the components `.` and `..`, so the existing `part in {"", ".", ".."}` test is replaced. It is not added alongside.

Rule 1 is deliberately stricter than git. For example, git accepts `refs/heads/--all`, but rule 1 rejects it so that no branch name looks like an option. Rules 2-4 are exactly git's `check-ref-format` rules for `refs/heads/<branch>` within rule 1's character set (from `refs.c`: `check_refname_component` and the trailing-`.` check). The test plan checks that agreement against the installed git for every name that passes rule 1.

Behavior with this rule:

| Input | Result | Reason |
| --- | --- | --- |
| `release/1.2`, `agent/fix-v0.1`, `v1.0`, `1.2`, `a.b.c`, `foo./bar`, `foo.lock.x` | accepted | git accepts all of them |
| `foo..bar`, `a/../b` | rejected | rule 2 (`..`) |
| `foo.` | rejected | rule 3 (ends with `.`) |
| `.foo` | rejected | rule 1 (first character) |
| `foo/.bar`, `a/./b` | rejected | rule 4 (component starts with `.`) |
| `foo.lock`, `foo/bar.lock`, `foo.lock/bar` | rejected | rule 4 (`.lock` suffix) |
| `foo//bar`, `foo/` | rejected | rule 4 (empty component) |
| `--all`, `foo bar`, `foo@{1}` | rejected | rule 1 (charset or first character) |

Table legend: "accepted" means `push()` passes validation and goes on to the isolated push. "Rejected" means `SeanbotError("Invalid destination branch")` is raised before any subprocess runs.

### Endpoint validation (`api_command`)

This replaces the regex check at `runner.py:45-48`. It stays inline in `api_command()`, before the method check, and the error message is unchanged.

The endpoint is valid if and only if both hold:

1. `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)*", endpoint)` matches. This is today's pattern with `.` added to both segment classes.
2. No segment in `endpoint.split("/")` consists only of dots: `not any(set(segment) == {"."} for segment in endpoint.split("/"))`.

Rule 2 rejects `.`, `..` and `...` as whole segments. All-dot segments have no legitimate use in a repository REST path. Rejecting all of them, not only `.` and `..`, avoids depending on how a server or proxy treats unusual dot runs. Dots inside a segment, including `...` in `v1.0...v1.1`, are allowed.

The command layout is unchanged: `gh api --hostname github.com --method M repos/OWNER/REPO/<endpoint> [--input FILE]`.

Behavior with this rule:

| Input | Result |
| --- | --- |
| `contents/README.md`, `contents/.github/workflows/ci.yml`, `compare/v1.0...v1.1`, `git/ref/heads/release/1.2`, `issues/1/comments` | accepted |
| `contents/a/../b`, `contents/./x`, `issues/..`, `contents/...` | rejected (rule 2) |
| `../installation/token`, `.github`, `/issues`, `issues?x=1`, `https://evil.example`, `--hostname`, `contents//x`, `contents/a%2e%2e` | rejected (rule 1) |

Table legend: "accepted" means `api_command` returns the fixed command list. "Rejected" means `SeanbotError("Endpoint must be a relative repository API path without query parameters")` is raised.

### CLI arguments, exit codes and output

Unchanged. A rejected branch or endpoint is a `SeanbotError`, so the CLI prints `gh-seanbot: <message>` to stderr and exits 1.

### Order: mint, check, request, revoke

The order is unchanged, and validation still happens before any token is minted:

- **Endpoint:** `execute()` calls `api_command()` before `audit.record(... "started")` and before `mint()`. An invalid endpoint therefore mints nothing and writes no audit event.
- **Branch:** `push()` validates the branch after `mint()`, because `execute()` calls it inside the token block. An invalid branch raises before any subprocess runs. The existing `except`/`finally` path records `failed`, revokes the token and records `revoked`.

Moving branch validation ahead of minting is #9/#13 territory and out of scope here. The token is never used for an invalid branch, and it is revoked.

### Failure modes (all fail closed)

- Invalid branch or endpoint: `SeanbotError`, exit 1, and no network operation with the token.
- A branch git rejects that our rule would accept: the ref-format agreement test is there to prevent this. If it did happen, the remote `git push` fails with a nonzero exit code that is passed through as the CLI result, and the token is revoked. Nothing falls back.
- Server-side normalization of dotted paths: no segment can be `.` or `..`, so normalization cannot leave `repos/OWNER/REPO/`.

### Documentation changes

- `README.md`, the paragraph beginning "API endpoints are relative to `repos/OWNER/REPO/`": add that segments may contain dots (for example `contents/README.md`), but `.`/`..`-style segments are rejected. After the `push` example, add one sentence saying branch names follow git ref-format rules within letters, digits, `_`, `.`, `-` and `/`, and must start with a letter or digit.
- `docs/operations.md`, "Scope and credential boundary", in the push paragraph after "The destination is always …": add one sentence saying the destination branch name is validated against git's ref-format rules within a restricted character set before any git process runs.
- `docs/operations.md`, "Acceptance evidence": change "fixed API paths" to "fixed API paths (including dot-segment rejection), branch ref-format validation".
- ADRs: no change.

## Acceptance criteria

- [ ] `push()` accepts `release/1.2`, `agent/fix-v0.1`, `v1.0`, `1.2`, `a.b.c`, `foo./bar` and `foo.lock.x`, and reaches the isolated push step with refspec `<sha>:refs/heads/<branch>`.
- [ ] `push()` raises `SeanbotError` for `foo..bar`, `a/../b`, `foo.`, `.foo`, `foo/.bar`, `a/./b`, `foo.lock`, `foo/bar.lock`, `foo.lock/bar`, `foo//bar`, `foo/`, `--all`, `foo bar` and `foo@{1}`, without running any subprocess.
- [ ] For every branch name in the two lists above that passes rule 1 (all of them except `--all`, `foo bar` and `foo@{1}`), the validator's verdict matches `git check-ref-format refs/heads/<branch>` on the installed git.
- [ ] `api_command()` accepts `contents/README.md`, `contents/.github/workflows/ci.yml`, `compare/v1.0...v1.1` and `git/ref/heads/release/1.2`, and puts each under `repos/owner/test/`.
- [ ] `api_command()` raises `SeanbotError` for `contents/a/../b`, `contents/./x`, `issues/..`, `contents/...`, `.github`, `contents/a%2e%2e` and `contents//x`, plus every endpoint that `test_api_path_rejected` rejects today.
- [ ] README and `docs/operations.md` have the changes listed above. No ADR changes.
- [ ] `just check` passes (ruff check, ruff format --check, mypy, pytest).

## Test plan

All tests go in `tests/test_runner.py`, using the existing pytest parametrize style. They run offline.

- Extend `test_api_path_rejected` with `contents/a/../b`, `contents/./x`, `issues/..`, `contents/...`, `.github`, `contents/a%2e%2e` and `contents//x`.
- Add `test_api_path_allows_dotted_segments`, parametrized over the accepted endpoints. It asserts `command[6] == f"repos/owner/test/{endpoint}"`.
- Extend `test_push_rejects_ambiguous_inputs` with the rejected branch list, all paired with a valid 40-hex SHA and `tmp_path` as the source. The validator raises before the source repository is touched.
- Add `test_push_accepts_dotted_branch`, parametrized over the accepted branch list:
  - Reuse the `test_push_ignores_source_network_config` setup: a real `git init` source, plus `monkeypatch` of `gh_seanbot.runner.run_process` with a stub that returns 0.
  - Assert that the stub saw `command[-1] == f"{'a' * 40}:refs/heads/{branch}"`.
- Add `test_branch_rule_matches_git_ref_format`, parametrized over both lists with the expected verdict, minus the rule-1-only rejections (`--all`, `foo bar`, `foo@{1}`):
  - Run `subprocess.run(["git", "check-ref-format", f"refs/heads/{branch}"])` and assert `(returncode == 0) == expected`.
  - Assert that `push()` agrees with the same verdict.
  - This test pins the claim that the rule matches git's. Git 2.52.0 was checked while writing this spec and matches every entry in the tables except `--all`, which git accepts and rule 1 rejects on purpose.

## Risks

- **Future git ref-format changes.** A newer git could reject a name the rule accepts. The agreement test catches that in CI. Failing late at `git push` is still fail-closed.
- **Wider API surface.** Dotted endpoints now reach `contents/<file>`, `compare/...` and similar endpoints. This is still bounded by the repository prefix, the per-invocation token narrowing, and the operator's capability allowlist. Write endpoints such as `PUT contents/<file>` can now be reached with a `push` or `contribute` capability token. That is consistent with what those capabilities already allow through `push`.
- **Validator overlap with #9.** #9 changes the same function. Whichever lands second must keep these syntax rules and add its policy check after them.
- **Unverified live behavior.** Branch creation and dotted API calls have not been run against a live GitHub App installation. Live acceptance remains #5.
