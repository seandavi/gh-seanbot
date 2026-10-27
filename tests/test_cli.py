import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from gh_seanbot.auth import InstallationToken
from gh_seanbot.cli import execute, main, parser
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


def push_argv(branch):
    return [
        "push",
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
        "--capability",
        "push",
        "--sha",
        "a" * 40,
        f"--branch={branch}",
    ]


def push_arguments(branch):
    return parser().parse_args(push_argv(branch))


@pytest.mark.parametrize("branch", ["main", "--all", "foo//bar"])
def test_push_outside_allowlist_never_mints(policy, monkeypatch, branch):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    monkeypatch.setattr("gh_seanbot.cli.mint", lambda *a: pytest.fail("minted"))
    with pytest.raises(SeanbotError, match="Destination branch|Invalid destination"):
        execute(push_arguments(branch))
    assert not policy.audit_log.exists()


def test_push_allowed_branch_lifecycle(policy, monkeypatch):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    monkeypatch.setattr(
        "gh_seanbot.cli.mint",
        lambda *a: InstallationToken("SECRET", datetime.now(UTC) + timedelta(hours=1)),
    )
    monkeypatch.setattr("gh_seanbot.cli.revoke", lambda *a: True)
    monkeypatch.setattr("gh_seanbot.cli.push", lambda *a: 0)
    assert execute(push_arguments("agent/x")) == 0
    events = [json.loads(line)["event"] for line in policy.audit_log.read_text().splitlines()]
    assert events == ["started", "authenticated", "finished", "revoked"]


def test_main_reports_branch_denial(policy, monkeypatch, capsys):
    monkeypatch.setattr("gh_seanbot.cli.load_policy", lambda *a: policy)
    monkeypatch.setattr("gh_seanbot.cli.mint", lambda *a: pytest.fail("minted"))
    monkeypatch.setattr(sys, "argv", ["gh-seanbot", *push_argv("main")])
    assert main() == 1
    assert capsys.readouterr().err == "gh-seanbot: Destination branch is not allowed by policy\n"
