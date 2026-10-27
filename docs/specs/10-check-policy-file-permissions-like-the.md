# Spec: check policy file permissions like the key and audit log (#10)

Part of tracking issue #1. Defense in depth, not containment: a same-user process can still read the key directly (docs/operations.md, "Scope and credential boundary"). The point is to protect all three operator-owned files the same way.

## Goal

`load_policy` (`src/gh_seanbot/config.py`) must refuse a policy file unless it is a regular, non-symlink file with no group/world permission bits (`st_mode & 0o077 == 0`). The check opens with `O_NOFOLLOW` and runs `fstat` on the descriptor, the same intent as the key (`auth.mint`) and audit log (`provenance.Audit.record`) checks, but `fstat` runs before `os.fdopen` (see File check). Every refusal fails closed before any audit write, JWT, network call, or child process.

## Non-goals

- No owner check (`st_uid == geteuid()`). The key and audit log don't have one either. Adding one for all three files would be a separate issue.
- No parent-directory or ancestor permission checks. docs/operations.md already makes the operator responsible for those.
- No shared "open private file" helper and no refactor of `auth.py` or `provenance.py`. Three short copies of one pattern are acceptable for now. A helper only makes sense if a fourth file appears.
- No change to policy keys, the TOML schema, CLI arguments, capabilities, or the mint/revoke lifecycle.
- No `expanduser()` on `--policy`. It is still resolved as given, relative to the cwd.
- No ADR change. ADR 0002 ("fail closed") already covers this, and no ADR states file-mode requirements.

## Design

### Policy file keys and their validation

These are unchanged: top level `{"app", "repositories", "audit_log"}`, `app` = `{"client_id", "installation_id", "private_key"}`, plus the existing type, allowlist and absolute-path checks. Only how the file is opened changes.

### File check (`src/gh_seanbot/config.py`, `load_policy`)

Replace `tomllib.loads(path.read_text())` inside the existing `try` block with:

```python
# Refuse symlinks and group/world-accessible policy, like the key and audit log.
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise SeanbotError("Policy file must be a regular file with mode 0600 or stricter")
except BaseException:
    os.close(fd)
    raise
with os.fdopen(fd, "rb") as stream:
    data = tomllib.load(stream)
```

The check has to run on the raw descriptor before `os.fdopen`: `fdopen` raises `IsADirectoryError` (an `OSError`) for a directory descriptor, which would produce the generic error instead of the "regular file" message, and it does not close a caller-supplied fd when it fails, so the fd would leak. `auth.mint` uses the `fdopen`-first order and is not changed here (see Non-goals).

- Add `import os` and `import stat`. No new dependency.
- `tomllib.load(fp: IO[bytes])` parses a binary file object. This was checked against the stdlib signature and docstring ("Parse TOML from a binary file object").
- `tomllib.TOMLDecodeError` and `UnicodeDecodeError` both subclass `ValueError` (verified), so the existing `except` turns them into "Invalid or unreadable policy file".
- The `fstat` runs on the opened descriptor, so the check and the read hit the same inode (no TOCTOU race).
- `O_NOFOLLOW` makes `os.open` fail with `ELOOP` (an `OSError`) when the final path component is a symlink. That error maps to the existing generic "Invalid or unreadable policy file" message, the same way a symlinked key maps to the generic auth error. Symlinks in parent components are still followed (ancestors are the operator's job, per the non-goals).
- `O_NONBLOCK` stops a FIFO from blocking the open. The FIFO then fails `S_ISREG`.
- A directory opens `O_RDONLY` on Linux and fails `S_ISREG` on the raw fd, before `fdopen` is reached.
- The new `SeanbotError` is raised inside the `try`. `SeanbotError` subclasses `Exception` and is not in the caught tuple, so it propagates unchanged, like the existing "not allowed by policy" error.
- Order inside `load_policy`: the repository/capability argument checks still run first, then the file check, then parsing and validation.

### CLI arguments, exit codes and output

The arguments don't change. When the policy is refused, `main()` prints `gh-seanbot: Policy file must be a regular file with mode 0600 or stricter` (or `gh-seanbot: Invalid or unreadable policy file` for a symlink, missing file or unparsable file) to stderr and returns exit code 1. Nothing goes to stdout. `footer` never loads a policy and is unaffected.

### What is minted, requested, checked and revoked, and in what order

The order is unchanged: `load_policy` (now including the file check), then `Audit.record("started")`, then `mint`, `authenticated`, run, `finished`/`failed`, `revoke`, `revoked`. A policy refusal happens before the audit path is even known, so nothing is audited, minted, requested, run or revoked. The key file is never opened.

### Failure modes (all fail closed, exit 1)

| Condition | Result |
| --- | --- |
| Missing or unreadable file | `OSError` → "Invalid or unreadable policy file" |
| Final component is a symlink | `ELOOP` → "Invalid or unreadable policy file" |
| Directory, FIFO, or other non-regular file | "Policy file must be a regular file with mode 0600 or stricter" |
| Any group or world bit set (e.g. 0640, 0620, 0604, 0602, 0601) | "Policy file must be a regular file with mode 0600 or stricter" |
| Invalid TOML or non-UTF-8 content | "Invalid or unreadable policy file" (unchanged) |

Legend: Result is the `SeanbotError` message that the CLI prints after `gh-seanbot: `.

Modes 0600 and 0400 are accepted.

### Documentation changes

- `README.md`, "Install and use", the paragraph about copying the example policy: say that `policy.toml` must be a regular file (not a symlink) with mode `0600` or stricter, e.g. `chmod 600 policy.toml`, and that the CLI refuses it otherwise. Keep the existing key/audit sentences.
- `examples/policy.example.toml`, header comment: add "chmod 600 after copying; the CLI refuses group/world-accessible or symlinked policy files."
- `docs/operations.md`, "Acceptance evidence": change "unsafe key/audit files" to "unsafe key/audit/policy files".
- ADRs: no change (see Non-goals).

## Acceptance criteria

- [ ] `load_policy` on a 0600 regular policy file returns a `Policy` exactly as before.
- [ ] `load_policy` raises `SeanbotError` for modes 0640, 0620, 0604 and 0602, for a symlink to a valid 0600 policy, for a directory, and for a FIFO. The FIFO case does not hang.
- [ ] The mode/non-regular refusal message is exactly "Policy file must be a regular file with mode 0600 or stricter".
- [ ] A CLI-level test shows that an unsafe policy makes `execute` raise before `mint` or `Audit.record` is called, and that no audit file is created.
- [ ] Existing tests still pass on a host with umask 027. The test helper writes policies at 0600 explicitly.
- [ ] README and the example note the requirement. The operations evidence line mentions policy files.
- [ ] `just check` passes (ruff check, ruff format --check, mypy, pytest).

## Test plan

All tests are offline and use pytest's `tmp_path`.

`tests/test_policy_provenance.py`:
- `config()` helper: add `path.chmod(0o600)` after `write_text`. Without it the file comes out 0640 under umask 027 and the existing tests would fail. `test_bad_config` rewrites the file with `write_text`, which keeps the existing mode, so it doesn't need its own chmod.
- `test_policy_rejects_shared_modes`: parametrize over `0o640, 0o620, 0o604, 0o602`. Chmod the file, then assert `pytest.raises(SeanbotError, match="mode 0600")`.
- `test_policy_accepts_read_only`: chmod 0o400, and `load_policy` succeeds.
- `test_policy_symlink`: create `link` pointing to a valid 0600 `config()` file, then assert `load_policy(link, ...)` raises `SeanbotError`.
- `test_policy_not_regular`: a directory (`tmp_path / "dir"`, mkdir, chmod 0o700) and a FIFO (`os.mkfifo(tmp_path / "fifo", 0o600)`) both raise `SeanbotError` with `match="regular file"`.

`tests/test_cli.py`:
- `test_unsafe_policy_prevents_audit_and_mint`: write a valid policy whose `audit_log` points at `tmp_path / "audit.jsonl"`, chmod it 0o644, and build args with `--policy` set to that path (extend `arguments()` with an optional policy-path parameter, defaulting to `"policy.toml"`). Monkeypatch `gh_seanbot.cli.mint` to `pytest.fail`. Assert `execute` raises `SeanbotError` and that the audit file does not exist. Do not monkeypatch `load_policy`.

Commands: `just check`.

## Risks

- Existing operators with a 0640/0644 `policy.toml` will get a hard failure after upgrading. That is intended: the error message says how to fix it, and the README documents it.
- A policy managed through a symlink (e.g. a dotfiles repo or config-management link) will now be refused. The operator has to point `--policy` at the real file.
- On platforms without `O_NOFOLLOW` the `os.open` call raises `AttributeError`. The existing `except` maps that to the generic error, so the tool fails closed. POSIX is already a stated requirement.
- This is not a security boundary against same-user processes. Those can still edit a 0600 policy file. The docs already say so.
