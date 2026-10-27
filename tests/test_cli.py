import io
import json
import signal
import sys
from datetime import UTC, datetime, timedelta

import pytest

from gh_seanbot.auth import InstallationToken
from gh_seanbot.cli import _interrupt, execute, main, parser
from gh_seanbot.config import SeanbotError


def arguments():
    return parser().parse_args(
        [
            "api",
            "--policy",
            "policy.toml",
            "--repo",
            "owner/test",
            "--harness",
            "pi",
            "--model",
            "provider/model",
            "--run",
            "run-123",
            "--endpoint",
            "issues",
        ]
    )


def test_lifecycle_and_revocation(policy, monkeypatch):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    monkeypatch.setattr(
        "gh_seanbot.cli.mint",
        lambda *a: InstallationToken("SECRET", datetime.now(UTC) + timedelta(hours=1)),
    )
    revoked = []
    monkeypatch.setattr("gh_seanbot.cli.revoke", lambda *a: revoked.append(True) or True)
    monkeypatch.setattr("gh_seanbot.cli.run_process", lambda *a: 7)
    assert execute(arguments()) == 7
    assert revoked == [True]
    text = policy.audit_log.read_text()
    assert "SECRET" not in text
    assert [json.loads(line)["event"] for line in text.splitlines()] == [
        "started",
        "authenticated",
        "finished",
        "revoked",
    ]


def test_failed_mint_never_launches(policy, monkeypatch):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)

    def fail(*a):
        raise SeanbotError("mint failed")

    monkeypatch.setattr("gh_seanbot.cli.mint", fail)
    monkeypatch.setattr("gh_seanbot.cli.run_process", lambda *a: pytest.fail("launched"))
    with pytest.raises(SeanbotError, match="mint failed"):
        execute(arguments())
    assert '"event":"failed"' in policy.audit_log.read_text()


def test_audit_failure_prevents_mint(policy, monkeypatch):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    policy.audit_log.write_text("")
    policy.audit_log.chmod(0o644)
    monkeypatch.setattr("gh_seanbot.cli.mint", lambda *a: pytest.fail("minted"))
    with pytest.raises(SeanbotError):
        execute(arguments())


def test_command_exception_still_revokes(policy, monkeypatch):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    monkeypatch.setattr(
        "gh_seanbot.cli.mint",
        lambda *a: InstallationToken("SECRET", datetime.now(UTC) + timedelta(hours=1)),
    )
    revoked = []
    monkeypatch.setattr("gh_seanbot.cli.revoke", lambda *a: revoked.append(True) or True)

    def fail(*a):
        raise SeanbotError("launch failed")

    monkeypatch.setattr("gh_seanbot.cli.run_process", fail)
    with pytest.raises(SeanbotError, match="launch failed"):
        execute(arguments())
    assert revoked == [True]


def test_revocation_failure_is_nonzero(policy, monkeypatch):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    monkeypatch.setattr(
        "gh_seanbot.cli.mint",
        lambda *a: InstallationToken("SECRET", datetime.now(UTC) + timedelta(hours=1)),
    )
    monkeypatch.setattr("gh_seanbot.cli.revoke", lambda *a: False)
    monkeypatch.setattr("gh_seanbot.cli.run_process", lambda *a: 0)
    with pytest.raises(SeanbotError, match="revocation"):
        execute(arguments())


FOOTER_ARGUMENTS = ["footer", "--harness", "pi", "--model", "provider/model", "--run", "run-123"]


def test_footer_rejects_non_utf8_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gh-seanbot", *FOOTER_ARGUMENTS])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff")))
    monkeypatch.setattr("gh_seanbot.cli.signal.signal", lambda *a: None)
    assert main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "gh-seanbot: Footer input must be valid UTF-8 text\n"


def test_footer_rejects_non_utf8_body_file(tmp_path):
    body = tmp_path / "body.md"
    body.write_bytes(b"\xff")
    with pytest.raises(SeanbotError, match="UTF-8"):
        execute(parser().parse_args([*FOOTER_ARGUMENTS, "--body", str(body)]))


def test_footer_round_trips_utf8(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO("café ✓".encode())))
    assert execute(parser().parse_args(FOOTER_ARGUMENTS)) == 0
    assert capsys.readouterr().out == (
        "café ✓\n\n<!-- agent: pi; model: provider/model; run: run-123 -->\n"
    )


def stub_session(policy, monkeypatch, revoke, run_process):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    monkeypatch.setattr(
        "gh_seanbot.cli.mint",
        lambda *a: InstallationToken("SECRET", datetime.now(UTC) + timedelta(hours=1)),
    )
    monkeypatch.setattr("gh_seanbot.cli.revoke", revoke)
    monkeypatch.setattr("gh_seanbot.cli.run_process", run_process)


def launch_failed(*a):
    raise SeanbotError("launch failed")


def test_revoked_audit_failure_keeps_command_error(policy, monkeypatch, capsys):
    revoked = []

    def revoke(*a):
        revoked.append(True)
        policy.audit_log.chmod(0o644)
        return True

    stub_session(policy, monkeypatch, revoke, launch_failed)
    with pytest.raises(SeanbotError, match="launch failed"):
        execute(arguments())
    assert revoked == [True]
    assert "audit write failed during cleanup" in capsys.readouterr().err


def test_failed_audit_failure_keeps_command_error(policy, monkeypatch):
    revoked = []

    def run_process(*a):
        policy.audit_log.chmod(0o644)
        raise SeanbotError("launch failed")

    stub_session(policy, monkeypatch, lambda *a: revoked.append(True) or True, run_process)
    with pytest.raises(SeanbotError, match="launch failed"):
        execute(arguments())
    assert revoked == [True]


@pytest.fixture
def restored_handlers():
    signal.signal(signal.SIGTERM, _interrupt)
    yield
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.default_int_handler)


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
def test_signal_during_revoke_is_deferred(policy, monkeypatch, restored_handlers, number):
    def revoke(*a):
        signal.raise_signal(number)
        return True

    stub_session(policy, monkeypatch, revoke, lambda *a: 0)
    with pytest.raises(KeyboardInterrupt):
        execute(arguments())
    last = json.loads(policy.audit_log.read_text().splitlines()[-1])
    assert last["event"] == "revoked"
    assert last["success"] is True
    assert signal.getsignal(signal.SIGTERM) is _interrupt
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_signal_during_revoke_keeps_command_error(policy, monkeypatch, restored_handlers):
    def revoke(*a):
        signal.raise_signal(signal.SIGTERM)
        return True

    stub_session(policy, monkeypatch, revoke, launch_failed)
    with pytest.raises(SeanbotError, match="launch failed"):
        execute(arguments())
