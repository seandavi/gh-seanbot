# Spec for #13: CLI robustness (footer decode errors, audit failure in `finally`, signals during revoke)

Status: proposed (spec stage). Part of tracking issue #1.

## Goal

Fix four robustness gaps in `src/gh_seanbot/cli.py` that came up in review:

1. `gh-seanbot footer` must not crash with a traceback on input that is not UTF-8. It should exit 1 with a clear one-line error.
2. If an audit write fails during cleanup while the command is already failing, the original exception must still propagate. The audit failure gets reported, but it does not replace the original error.
3. SIGINT and SIGTERM that arrive during token revocation must not skip revocation or its `revoked` audit record. They are deferred until both finish.
4. `revoked` is initialised explicitly before the `try`.

The issue's acceptance: a test for each of items 1 and 2; items 3 and 4 fixed or explicitly documented. This spec fixes all four and adds tests for item 3 as well.

## Non-goals

- No policy file changes. `load_policy`, `examples/policy.example.toml` and policy keys are untouched.
- No new CLI arguments, subcommands, or options.
- No change to the mint path. A signal or crash *during minting* can still leave a minted but unrevoked token. That case is already documented, and token expiry bounds it.
- No change to `Audit.record`, `auth.py`, `runner.py` or `provenance.py`.
- No change to what happens when revocation itself fails (`revoke` returns `False`) while the command is already failing. That outcome is still recorded in the audit log (`"revoked"`, `success: false`). Printing a stderr warning for it would be a separate issue (see Risks).
- `code` has the same "only assigned on the success path" shape as `revoked`, but the issue names only `revoked`. `code` is left as is.
- No ADR changes (see Design §6).

## Design

All code changes are in `src/gh_seanbot/cli.py`. Use only the stdlib (`signal`, `contextlib`, `sys`) and what the file already imports. No new dependencies.

### 1. Footer input and output are UTF-8, regardless of locale

Root cause: `args.body.read_text()` and `sys.stdin.read()` decode using the locale encoding. A `UnicodeDecodeError` is a `ValueError`, which `main()` does not catch. The behaviour also depends on the locale: under a latin-1 locale, `\xff` decodes "successfully" into mojibake. GitHub bodies are UTF-8, so pin UTF-8 at both ends.

Replace the footer branch of `execute` with:

```python
    if args.command == "footer":
        raw = args.body.read_bytes() if args.body else sys.stdin.buffer.read()
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SeanbotError("Footer input must be valid UTF-8 text") from None
        sys.stdout.buffer.write(provenance.footer(body).encode("utf-8"))
        sys.stdout.flush()
        return 0
```

- Decoding is strict (`errors="strict"`, the default). Nothing is replaced or passed through.
- A file read error on `--body` stays an `OSError`. `main()` already maps that to `gh-seanbot: local file or subprocess operation failed` (exit 1). This is unchanged.
- CLI contract: valid input gives exit 0 and the footered text on stdout as UTF-8. Invalid UTF-8 gives exit 1, nothing on stdout, and exactly `gh-seanbot: Footer input must be valid UTF-8 text` on stderr (printed by the existing `except SeanbotError` branch in `main`).

### 2. Cleanup audit writes do not replace the propagating exception

Root cause: in `execute`, two audit writes run while an exception may already be propagating:

- `audit.record(..., "failed")` inside `except BaseException`
- `audit.record(..., "revoked", ...)` inside `finally`

If either write raises `SeanbotError`, that error replaces the original. `main` then prints only the audit message. Item 2 in the issue names only the `finally` write, but the `except` write has the same root cause, so both get the same fix.

Add this module-level helper to `cli.py`:

```python
def _record_during_failure(record: Callable[[], None]) -> None:
    """Run a cleanup audit write without replacing the exception that ended the command."""
    try:
        record()
    except SeanbotError as error:
        print(f"gh-seanbot: audit write failed during cleanup: {error}", file=sys.stderr)
```

(`from collections.abc import Callable, Iterator`; `Iterator` is for §3.)

Use the helper only when an exception is already propagating. Track that with a local `failed` flag, set in the `except` block. A clean run keeps today's behaviour: if the `"revoked"` write fails after a successful command, `SeanbotError` propagates and the CLI exits 1. The CLI still fails closed.

### 3. Defer SIGINT/SIGTERM around revocation

Root cause: `main` installs a SIGTERM handler that raises `KeyboardInterrupt`, and SIGINT raises it by default. If either signal arrives during `revoke()`, inside httpx, the `KeyboardInterrupt` escapes the `finally` block. `revoke` catches only `httpx.HTTPError`. The token is then left valid until expiry, with no `"revoked"` audit record.

Add a context manager to `cli.py`. This is the version prototyped and checked under `mypy --strict` during this spec:

```python
@contextmanager
def _deferred_signals(received: list[int]) -> Iterator[None]:
    """Hold SIGINT/SIGTERM until revocation and its audit record finish."""

    def hold(signum: int, frame: object) -> None:
        received.append(signum)

    previous = {number: signal.signal(number, hold) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)
```

- It uses a Python-level handler swap, not `signal.pthread_sigmask`. With a mask, the pending signal would fire as soon as it is unblocked, inside `finally`, and replace an exception that is already propagating. That would reintroduce item 2. The swap lets `execute` decide what to do afterwards.
- `signal.signal` returns `None` when the previous handler was not installed from Python. The context manager restores `SIG_DFL` in that case. (`signal.signal(n, None)` raises `TypeError`.)
- `signal.signal` works only in the main thread. `execute` is called only from `main()` and from tests, both in the main thread. This constraint is accepted, not worked around.
- A blocking socket call interrupted by a signal is retried after the handler returns (PEP 475), so `revoke` finishes normally. It is still bounded by the existing `httpx.Client(timeout=20)`.

After cleanup:

- No exception propagating, revocation succeeded, and a signal was deferred: raise `KeyboardInterrupt`. `main` prints `gh-seanbot: interrupted` and returns 1. The command's own exit code is in the `"finished"` audit record.
- No exception propagating and revocation failed: the revocation-failure `SeanbotError` wins. Its message is the more important one, and the exit code is 1 either way.
- An exception is propagating: deferred signals are dropped. The original exception continues and the process exits nonzero, which is what the signal asked for.

### 4. Resulting `execute` body (from the `httpx.Client` block)

```python
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        token = None
        revoked = False
        failed = False
        deferred: list[int] = []
        try:
            token = mint(policy, client)
            audit.record(provenance, args.repo, args.capability, "authenticated")
            with tempfile.TemporaryDirectory(prefix="seanbot-home-") as directory:
                ...  # unchanged
            audit.record(provenance, args.repo, args.capability, "finished", exit_code=code)
        except BaseException:
            failed = True
            _record_during_failure(
                lambda: audit.record(provenance, args.repo, args.capability, "failed")
            )
            raise
        finally:
            if token is not None:
                with _deferred_signals(deferred):
                    revoked = revoke(token, client)
                    if failed:
                        _record_during_failure(
                            lambda: audit.record(
                                provenance, args.repo, args.capability, "revoked", success=revoked
                            )
                        )
                    else:
                        audit.record(
                            provenance, args.repo, args.capability, "revoked", success=revoked
                        )
        if not revoked:
            raise SeanbotError("Command ended, but token revocation failed; token may remain valid")
        if deferred:
            raise KeyboardInterrupt
        return code
```

The order is unchanged: write the `started` audit record → mint → write `authenticated` → run one command → write `finished`, or `failed` on exception → revoke → write `revoked`. Only the error handling around the cleanup writes changes, plus signal deferral around the final two steps. Nothing new is minted, requested, or checked.

### 5. Failure modes

| Situation | Before | After |
|---|---|---|
| Footer input not UTF-8 | Traceback, exit 1 | `gh-seanbot: Footer input must be valid UTF-8 text`, exit 1, empty stdout |
| `failed` audit write fails during a command failure | Audit error replaces the original; original reason lost | Original propagates; stderr gets `gh-seanbot: audit write failed during cleanup: …`; revocation still runs |
| `revoked` audit write fails during a command failure | Audit error replaces the original | Original propagates; stderr gets the cleanup line; exit 1 |
| `revoked` audit write fails after success | `SeanbotError`, exit 1 | Unchanged |
| SIGINT/SIGTERM during `revoke()` | `KeyboardInterrupt` may skip revocation and its audit record | Revocation and `revoked` record finish; then `gh-seanbot: interrupted`, exit 1 (or the original failure if one is propagating) |
| Signal between `finally` starting and the context manager entering | Can skip revocation | Can still skip revocation (window of a few bytecodes); expiry bounds it; documented |
| SIGKILL, crash, signal during mint | Can skip revocation | Unchanged; expiry bounds it; documented |

### 6. Documentation changes

- `README.md`, the paragraph starting "API endpoints are relative to…": add one sentence: "The footer helper reads and writes UTF-8 and rejects input that is not valid UTF-8."
- `docs/operations.md`, "Scope and credential boundary", the paragraph starting "A mint or initial audit failure prevents execution.": replace it with text that says:
  - revocation runs after command success or failure;
  - SIGINT/SIGTERM received during revocation are deferred until revocation and its audit record finish, then the CLI exits as interrupted;
  - if a cleanup audit write fails while the command is already failing, the CLI reports it on stderr and keeps the original failure;
  - revocation failure produces a nonzero result and an audit event;
  - a process crash, SIGKILL, machine loss, interruption during minting, or a signal in the brief window before deferral begins can still prevent cleanup, and token expiry remains the ultimate bound.
- `docs/operations.md`, "Acceptance evidence": add "footer UTF-8 handling, cleanup audit failures that preserve the original error, and signal deferral during revocation" to the list of offline test coverage.
- ADRs: no change. ADR 0003's "attempts revocation afterward" stays accurate, and this change makes it more reliable without changing the decision.

## Acceptance criteria

- [ ] `printf '\xff' | gh-seanbot footer --harness pi --model provider/model --run run-123` exits 1, prints nothing to stdout, and prints `gh-seanbot: Footer input must be valid UTF-8 text` to stderr, with no traceback.
- [ ] The same holds for `--body FILE` when the file contains `\xff`.
- [ ] Valid UTF-8 input with non-ASCII text (e.g. `café ✓`) round-trips into a footer byte for byte.
- [ ] When the command raises and the `revoked` audit write fails, `execute` raises the original exception, revocation was called, and stderr contains `audit write failed during cleanup`.
- [ ] When the command raises and the `failed` audit write fails, `execute` raises the original exception and revocation is still called.
- [ ] A SIGTERM or SIGINT raised during `revoke` leaves revocation and the `revoked` audit record (`success: true`) complete, then `execute` raises `KeyboardInterrupt`. The signal handlers that were installed before the call are restored afterwards.
- [ ] A signal during `revoke` while the command is already failing gives the original exception, not `KeyboardInterrupt`.
- [ ] `revoked = False` is assigned before the `try` in `execute`.
- [ ] Existing tests in `tests/test_cli.py` pass unchanged.
- [ ] README and `docs/operations.md` are updated as in Design §6.
- [ ] `just check` passes (ruff check, ruff format --check, mypy strict, pytest).

## Test plan

Add all tests to `tests/test_cli.py`. Reuse the existing `policy` fixture and `arguments()` helper, plus the stubbing style already used there: `monkeypatch.setattr("gh_seanbot.cli.mint" | "revoke" | "run_process" | "load_policy", ...)`. New imports: `io`, `signal`, and `main` from `gh_seanbot.cli`.

1. `test_footer_rejects_non_utf8_stdin` covers item 1. Patch `sys.argv` to `["gh-seanbot", "footer", "--harness", "pi", "--model", "provider/model", "--run", "run-123"]` and `sys.stdin` to `io.TextIOWrapper(io.BytesIO(b"\xff"))`. Patch `gh_seanbot.cli.signal.signal` to a no-op so `main()` does not leave a SIGTERM handler installed in the pytest process. Assert `main() == 1`, `capsys` stdout is `""`, and stderr is `gh-seanbot: Footer input must be valid UTF-8 text\n`.
2. `test_footer_rejects_non_utf8_body_file`: write `b"\xff"` to `tmp_path / "body.md"`, then call `execute(parser().parse_args([... "footer", "--body", path]))` inside `pytest.raises(SeanbotError, match="UTF-8")`.
3. `test_footer_round_trips_utf8`: stdin is `io.TextIOWrapper(io.BytesIO("café ✓".encode()))`. `execute` returns 0, and `capsys` stdout equals `"café ✓\n\n<!-- agent: pi; model: provider/model; run: run-123 -->\n"`. pytest's `CaptureIO` is a `TextIOWrapper` over `BytesIO` with UTF-8 encoding (checked in pytest 9.1.1), so `sys.stdout.buffer` writes are captured.
4. `test_revoked_audit_failure_keeps_command_error` covers item 2. `run_process` raises `SeanbotError("launch failed")`. `revoke` stub runs `policy.audit_log.chmod(0o644)` and returns `True`, so the `"failed"` write succeeds and the `"revoked"` write fails. Assert `pytest.raises(SeanbotError, match="launch failed")`, revoke was called, and `capsys` stderr contains `audit write failed during cleanup`.
5. `test_failed_audit_failure_keeps_command_error`: the `run_process` stub chmods the audit log to `0o644`, then raises `SeanbotError("launch failed")`. Assert the original error and that revoke was called.
6. `test_signal_during_revoke_is_deferred` covers item 3, parametrised over `signal.SIGTERM` and `signal.SIGINT`. Install `gh_seanbot.cli._interrupt` for SIGTERM first, as `main` does, and save and restore both handlers with `signal.getsignal`/`signal.signal` in `try`/`finally`. The `revoke` stub calls `signal.raise_signal(number)` and returns `True`. Assert `pytest.raises(KeyboardInterrupt)`, the last audit event is `"revoked"` with `"success": true`, and afterwards `signal.getsignal(signal.SIGTERM) is _interrupt` and `signal.getsignal(signal.SIGINT) is signal.default_int_handler`.
7. `test_signal_during_revoke_keeps_command_error`: like 6 with SIGTERM, but `run_process` raises `SeanbotError("launch failed")`. Assert `pytest.raises(SeanbotError, match="launch failed")`.

Item 4 is a one-line initialisation. Tests 4–7 cover it implicitly; it gets no separate test.

Manual check (record its output in the PR): `printf '\xff' | uv run gh-seanbot footer --harness pi --model provider/model --run run-123; echo "exit=$?"`. Before the fix, it produced a `UnicodeDecodeError` traceback (reproduced during this spec).

## Risks

- **Narrow signal window.** A signal that arrives after `finally` starts but before `_deferred_signals` swaps the handlers can still skip revocation. Closing it would mean deferring signals across the whole mint→revoke path, including the child process, which changes interruption behaviour. Token expiry (≤1 hour) bounds it; documented.
- **Delayed exit.** While revocation runs, Ctrl-C and SIGTERM are held for up to the httpx timeout (20 s per phase). A supervisor with a shorter grace period (e.g. Docker's 10 s default) will send SIGKILL, which skips revocation, as before. Expiry bounds it.
- **Main-thread requirement.** `_deferred_signals` raises `ValueError` if `execute` is ever called from a non-main thread. No current caller does this.
- **Silent revocation failure during a failing command.** When the command fails and `revoke` returns `False`, the user sees only the original error. The token-may-remain-valid fact is only in the audit log. This is unchanged by this spec; a follow-up issue could add a stderr line.
- **Behaviour change for non-UTF-8 locales.** Footer output is now always UTF-8. Before, text was encoded with the locale. For GitHub bodies this is the correct behaviour.
- **Mocked tests only.** All tests use mocked HTTP and synthetic signals. None of this is verified against a live App installation.
