import dataclasses
import json

import pytest

from gh_seanbot.config import SeanbotError, check_push_branch, load_policy
from gh_seanbot.provenance import Audit, Provenance


def config(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text("""audit_log = "/private/audit.jsonl"
[app]
client_id = "Iv1.example"
installation_id = 123
private_key = "/private/app.pem"
[repositories]
"owner/test" = ["read", "contribute"]
""")
    return path


def test_policy_allowlist(tmp_path):
    path = config(tmp_path)
    assert load_policy(path, "owner/test", "read").permissions["contents"] == "read"
    for repo in ["other/test", "owner/other", "https://github.com/owner/test", "owner/.."]:
        with pytest.raises(SeanbotError):
            load_policy(path, repo, "read")
    with pytest.raises(SeanbotError):
        load_policy(path, "owner/test", "admin")


@pytest.mark.parametrize(
    "old,new",
    [
        ("installation_id = 123", "installation_id = true"),
        ('"/private/app.pem"', '"relative.pem"'),
        ('["read", "contribute"]', '["admin"]'),
        ('client_id = "Iv1.example"', "client_id = 123"),
        ('audit_log = "/private', 'extra = 1\naudit_log = "/private'),
    ],
)
def test_bad_config(tmp_path, old, new):
    path = config(tmp_path)
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(SeanbotError):
        load_policy(path, "owner/test", "read")


def test_push_branch_prefixes_default(tmp_path):
    assert load_policy(config(tmp_path), "owner/test", "contribute").push_branch_prefixes == (
        "agent/",
    )


def test_push_branch_prefixes_configured(tmp_path):
    path = config(tmp_path)
    path.write_text(
        path.read_text() + '[push_branch_prefixes]\n"owner/test" = ["bot/", "agent/"]\n'
    )
    policy = load_policy(path, "owner/test", "read")
    assert policy.push_branch_prefixes == ("bot/", "agent/")


@pytest.mark.parametrize(
    "table",
    [
        '[push_branch_prefixes]\n"owner/test" = []',
        '[push_branch_prefixes]\n"owner/test" = "agent/"',
        '[push_branch_prefixes]\n"owner/test" = ["agent"]',
        '[push_branch_prefixes]\n"owner/test" = ["/"]',
        '[push_branch_prefixes]\n"owner/test" = ["../"]',
        '[push_branch_prefixes]\n"owner/test" = ["agent//"]',
        '[push_branch_prefixes]\n"owner/test" = [1]',
    ],
)
def test_bad_push_branch_prefixes(tmp_path, table):
    path = config(tmp_path)
    path.write_text(path.read_text() + table + "\n")
    with pytest.raises(SeanbotError):
        load_policy(path, "owner/test", "read")


def test_push_branch_prefixes_scalar(tmp_path):
    path = config(tmp_path)
    path.write_text("push_branch_prefixes = 1\n" + path.read_text())
    with pytest.raises(SeanbotError):
        load_policy(path, "owner/test", "read")


def test_check_push_branch(policy):
    check_push_branch(policy, "agent/x")
    for branch in ["main", "agentx/y", "Agent/x", "agent/"]:
        with pytest.raises(SeanbotError):
            check_push_branch(policy, branch)
    bot = dataclasses.replace(policy, push_branch_prefixes=("bot/", "agent/"))
    check_push_branch(bot, "bot/x")
    check_push_branch(bot, "agent/x")
    with pytest.raises(SeanbotError):
        check_push_branch(bot, "main")


def test_footer_roundtrip_and_update():
    meta = Provenance("pi", "provider/model", "run-123")
    once = meta.footer("Hello\n")
    assert meta.footer(once) == once
    updated = Provenance("claude-code", "provider/new", "run-456").footer(once)
    assert updated.count("<!-- agent:") == 1
    assert "run-123" not in updated
    assert updated.startswith("Hello\n\n")


@pytest.mark.parametrize("value", ["", "foo -->", "x\ny", "x--y", "a;b", "a" * 201])
def test_footer_rejects_injection(value):
    with pytest.raises(SeanbotError):
        Provenance("pi", value, "run")


def test_private_audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    Audit(path).record(
        Provenance("pi", "provider/model", "run"), "owner/test", "read", "finished", exit_code=0
    )
    record = json.loads(path.read_text())
    assert record["run"] == "run"
    assert record["exit_code"] == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(record) == {
        "time",
        "run",
        "harness",
        "model",
        "repository",
        "capability",
        "event",
        "exit_code",
    }
    path.chmod(0o644)
    with pytest.raises(SeanbotError):
        Audit(path).record(Provenance("pi", "model", "run"), "owner/test", "read", "started")


def test_audit_symlink(tmp_path):
    target = tmp_path / "target"
    target.write_text("untouched")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(SeanbotError):
        Audit(link).record(Provenance("pi", "model", "run"), "owner/test", "read", "started")
    assert target.read_text() == "untouched"
