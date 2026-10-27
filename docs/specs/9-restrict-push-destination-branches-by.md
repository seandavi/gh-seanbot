# Spec: restrict push destination branches by policy (#9)

Part of #1. Interacts with #4 (merge/branch policy), #5 (live repository-rule verification) and #11 (dots in branch names).

## Goal

`gh-seanbot push` accepts any syntactically valid branch, including `main`. The `push` and `contribute` profiles grant `contents: write` on the whole repository, so branch protection is the only guard against direct writes to the default branch, and that protection has not been verified live (#5).

Add an optional per-repository list of allowed branch prefixes to the policy file. It defaults to `agent/`, the prefix the README example already uses. `push` refuses any destination branch outside the list **before** writing an audit record or minting a token.

## Non-goals

- **Token-level enforcement.** GitHub installation tokens take repository-level permissions only; you can't scope them to branches. The check runs in the CLI. The minted token keeps `contents: write` on the whole repository.
- **Restricting `api` calls.** `gh-seanbot api` with `push`, `merge` or `contribute` can still move any ref, for example `PATCH git/refs/heads/main`. Repository rules remain the enforcement point for API writes (#4, #5). See Risks.
- **Exact branch names or globs.** Entries are prefixes only, so you can't allowlist `main` or `gh-pages`. Add exact names when a real need appears.
- **Branch syntax changes**, such as allowing dots. That is #11. This spec keeps the current syntax rule and moves it so #11 changes it in one place.
- **Moving the SHA check before mint.** `push()` still validates `--sha` after minting, as it does today. Out of scope.
- **Auditing policy denials.** Repository and capability denials happen before the `started` audit record today. Branch denials do the same.
- **Policy file permission checks** (#10) and token-scope verification (#12).

## Design

### Policy file

Add one optional top-level table, `push_branch_prefixes`, keyed by `owner/name`:

```toml
[repositories]
"OWNER/TEST_REPOSITORY" = ["read", "issues", "pull-requests", "push"]

# Optional. Pushes to a repository without an entry may target only "agent/..." branches.
[push_branch_prefixes]
"OWNER/TEST_REPOSITORY" = ["agent/"]
```

This is a separate table rather than a change to the `[repositories]` value shape, so existing policy files still load without edits.

Validation in `load_policy` (`src/gh_seanbot/config.py`) uses only `tomllib` from the standard library, which is already in use. TOML tables parse to `dict` and arrays to `list`; I checked this with the locked interpreter.

1. Top-level keys. Replace the exact-set check `set(data) != {"app", "repositories", "audit_log"}` with: `app`, `repositories` and `audit_log` are required, and `push_branch_prefixes` is the only other key allowed. Any other key raises `ValueError`, which is reported as `Invalid or unreadable policy file`, as today.
2. `prefixes_table = data.get("push_branch_prefixes", {})` must be a `dict`. Otherwise raise `ValueError`.
3. `entry = prefixes_table.get(repository)`. If it is `None`, use `DEFAULT_PUSH_BRANCH_PREFIXES = ("agent/",)`. Otherwise `entry` must be a **non-empty** `list` in which every item is a `str` that:
   - ends with `/`, and
   - is a valid branch name once the trailing `/` is removed (`valid_branch(item[:-1])`, below).

   Anything else raises `ValueError`. Examples that fail: `[]`, `"agent/"` (a string, not a list), `["agent"]`, `["/"]`, `["../"]`, `["agent//"]`, `[1]`.
4. Only the requested repository's entry is validated. This matches how `[repositories]` is validated today. An entry for an unlisted or misspelled repository is ignored, and that repository falls back to the default `agent/`. That fallback is the more restrictive outcome.
5. Prefixes are loaded whatever the requested capability is. They are used only by `push`.

Requiring the trailing `/` makes matching component-wise. A prefix `agent/` can't match `agentx/main`, and no prefix can match `main`.

### Code changes

`src/gh_seanbot/config.py`:

- Move the branch syntax rule out of `runner.push` into `def valid_branch(name: str) -> bool`. The rule is unchanged: `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]*", name)` holds and no `/`-separated component is `""`, `"."` or `".."`.
- Add `DEFAULT_PUSH_BRANCH_PREFIXES: tuple[str, ...] = ("agent/",)`.
- Add the field `push_branch_prefixes: tuple[str, ...] = DEFAULT_PUSH_BRANCH_PREFIXES` to the frozen `Policy` dataclass, as its **last** field with that default. This keeps the positional fixture in `tests/conftest.py` working. `load_policy` always passes the value explicitly as `tuple(entry)`.
- Add `def check_push_branch(policy: Policy, branch: str) -> None`:
  - If `not valid_branch(branch)`, raise `SeanbotError("Invalid destination branch")`. This is the same message `push()` uses today.
  - If `not branch.startswith(policy.push_branch_prefixes)`, raise `SeanbotError("Destination branch is not allowed by policy")`. `str.startswith` accepts a tuple. Matching is case-sensitive.

`src/gh_seanbot/runner.py`:

- `push()` keeps its own syntax check as defense in depth, but calls `valid_branch(branch)` from `.config` instead of its inline regex. Its signature and behavior don't change, and it does no prefix check.

`src/gh_seanbot/cli.py`, in `execute`:

- Directly after the existing `if args.command == "push" and args.capability not in {"push", "contribute"}` check, and therefore before `audit.record(... "started")` and before `mint`, add:

  ```python
  if args.command == "push":
      check_push_branch(policy, args.branch)
  ```

### CLI arguments, output and exit codes

No new arguments. `--branch` keeps its meaning.

| Case | stderr | Exit | Audit records | Token |
| --- | --- | --- | --- | --- |
| Branch matches an allowed prefix | (unchanged) | child's code | started, authenticated, finished/failed, revoked | minted, then revoked |
| Valid branch outside the prefixes (for example `main`) | `gh-seanbot: Destination branch is not allowed by policy` | 1 | none | not minted |
| Syntactically invalid branch (for example `--all`, `foo//bar`) | `gh-seanbot: Invalid destination branch` | 1 | none | not minted (this used to happen after minting) |
| Malformed `push_branch_prefixes` | `gh-seanbot: Invalid or unreadable policy file` | 1 | none | not minted, for every subcommand, including `api` |

The `SeanbotError` reaches the existing handler in `main()`, which prints it and returns 1. The message never includes the token, the policy contents or the branch value.

### Order of operations for `push`

1. Parse arguments and check that `--max-seconds` is positive (unchanged).
2. `load_policy` validates the repository, the capability and `push_branch_prefixes`.
3. Check that the capability is `push` or `contribute` (unchanged).
4. **New:** `check_push_branch`.
5. Write the `started` audit record, mint, run `push()` (which still checks SHA and branch syntax), write `finished` or `failed`, revoke, write `revoked` (unchanged).

### How each failure fails closed

- **Missing table or missing entry:** the default `agent/` applies. It never means "allow all".
- **Malformed table or entry:** the whole policy is rejected before mint.
- **Branch outside the allowlist, or invalid:** rejected before audit and mint, so no token exists to revoke.
- **Prefix check bypassed (a future code path calling `push()` directly):** `push()` still enforces syntax, but not prefixes. The CLI is the only caller today. Branch-level enforcement on the server side remains repository rules (#5).

### Documentation changes

- `examples/policy.example.toml`: add the `[push_branch_prefixes]` table with `"OWNER/TEST_REPOSITORY" = ["agent/"]` and a one-line comment covering the default and the trailing-`/` rule.
- `README.md`, **Install and use** paragraph on the policy file: add that `push` destinations must start with a prefix from the optional `push_branch_prefixes` table (default `agent/`, entries end with `/`), and that this is checked before minting. Keep the `--branch agent/example` example. Don't edit the rollout sections, which are moving in #14.
- `docs/operations.md`, **Scope and credential boundary** push paragraph: add that the destination prefix check is a CLI policy check, not token scope. The token still has repository-wide `contents: write`, `api` calls with write profiles can update any ref, and repository rules remain the enforcement point (#5).
- `docs/operations.md`, **Acceptance evidence**: add "push branch allowlist" to the list of offline-tested behavior.
- ADRs: no change. ADR 0002 already says tokens are narrowed "where supported", and branch scope isn't supported by GitHub tokens. ADR 0003 already says local operations aren't a sandbox. Neither ADR's decision changes.

## Acceptance criteria

- [ ] `gh-seanbot push --branch main` with the `push` capability exits 1 with `Destination branch is not allowed by policy`. `mint` is never called and no audit record is written. A test covers this.
- [ ] Without a `push_branch_prefixes` entry for the repository, `agent/x` is allowed and `main`, `agentx/y` and `feature/x` are denied.
- [ ] With `"owner/test" = ["bot/", "agent/"]`, `bot/x` and `agent/x` are allowed and `main` is denied.
- [ ] Each malformed value listed in Design §3, a non-table `push_branch_prefixes`, and an unknown top-level key make `load_policy` raise `SeanbotError`.
- [ ] Existing policy files without the new table load unchanged, and the existing tests pass unmodified.
- [ ] A syntactically invalid `--branch` fails before mint.
- [ ] An allowed branch still runs the full lifecycle: started, authenticated, finished, revoked.
- [ ] The example policy, README and `docs/operations.md` are updated as described above.
- [ ] `just check` passes: ruff check, ruff format check, mypy strict and pytest.

## Test plan

All tests are offline, using mocked mint and revoke, matching the existing style.

`tests/test_policy_provenance.py`:

- `test_push_branch_prefixes_default`: the existing `config()` file has no table, so `load_policy(...).push_branch_prefixes == ("agent/",)`.
- `test_push_branch_prefixes_configured`: append `[push_branch_prefixes]` with `"owner/test" = ["bot/", "agent/"]` and assert the tuple.
- `test_bad_push_branch_prefixes`: parametrize over `[]`, `"agent/"`, `["agent"]`, `["/"]`, `["../"]`, `["agent//"]`, `[1]`, and `push_branch_prefixes = 1` as a top-level scalar. Each raises `SeanbotError`.
- Add a case for an unknown top-level key, for example `extra = 1`, to the existing parametrized `test_bad_config`, or as its own test.
- `test_check_push_branch`: use `dataclasses.replace` on a `Policy` to set the prefixes. `agent/x` passes. `main`, `agentx/y`, `Agent/x` and `agent/` raise `SeanbotError`. `agent/` fails the syntax rule because of its empty component.

`tests/test_cli.py`:

- `test_push_outside_allowlist_never_mints`: parse `push ... --capability push --sha <40 hex> --branch main`. Monkeypatch `load_policy` to return the fixture (default prefixes) and `mint` to call `pytest.fail`. Expect `SeanbotError` matching `not allowed by policy`, and assert `not policy.audit_log.exists()`.
- `test_push_allowed_branch_lifecycle`: the same arguments with `--branch agent/x`. Monkeypatch `mint`, `revoke` and `gh_seanbot.cli.push` to return 0. Assert exit 0 and the four audit events.
- `test_main_reports_branch_denial`: monkeypatch `sys.argv` and `load_policy`, call `main()`, and assert it returns 1. Use `capsys` to check stderr is `gh-seanbot: Destination branch is not allowed by policy\n`.

`tests/test_runner.py`: no new tests. The existing `test_push_rejects_ambiguous_inputs` and `test_push_ignores_source_network_config` keep `push()`'s syntax check covered after the move to `valid_branch`.

Run `just check`.

## Risks

- **False sense of protection.** The allowlist governs only `gh-seanbot push`. A caller with `push`, `merge` or `contribute` can still move `main` through `gh-seanbot api` (for example `PATCH git/refs/heads/main`), or with the token's repository-wide scope by any path the token reaches. The docs must say this plainly. Consider a follow-up issue to restrict ref-writing endpoints in `api`, or to rely only on verified repository rules (#4, #5).
- **Prefix-only is strict.** Operators can't allow exact names such as `gh-pages`. This is acceptable until a need is recorded.
- **Merge conflict with #11.** #11 changes the branch syntax. After this spec, that change lives in `config.valid_branch` and applies to both branches and prefixes, which is the intended coupling. Whichever PR lands second must rebase.
- **Case sensitivity.** Matching is case-sensitive, so `Agent/x` is denied under `agent/`. That is fail-closed, but it may surprise operators.
- **No live verification.** These tests use mocks and local git only. They don't show that GitHub rejects or accepts any push. Live checks remain #5.
