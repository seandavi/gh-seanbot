import json
import os

import pytest

from gh_seanbot.config import SeanbotError, load_policy
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
    path.chmod(0o600)
    return path


def test_policy_allowlist(tmp_path):
    path = config(tmp_path)
    assert load_policy(path, "owner/test", "read").permissions["contents"] == "read"
    for repo in ["other/test", "owner/other", "https://github.com/owner/test", "owner/.."]:
        with pytest.raises(SeanbotError):
            load_policy(path, repo, "read")
    with pytest.raises(SeanbotError):
        load_policy(path, "owner/test", "admin")


@pytest.mark.parametrize("mode", [0o640, 0o620, 0o604, 0o602])
def test_policy_rejects_shared_modes(tmp_path, mode):
    path = config(tmp_path)
    path.chmod(mode)
    with pytest.raises(SeanbotError, match="mode 0600"):
        load_policy(path, "owner/test", "read")


def test_policy_accepts_read_only(tmp_path):
    path = config(tmp_path)
    path.chmod(0o400)
    assert load_policy(path, "owner/test", "read").repository == "owner/test"


def test_policy_symlink(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(config(tmp_path))
    with pytest.raises(SeanbotError):
        load_policy(link, "owner/test", "read")


def test_policy_not_regular(tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    for path in (directory, fifo):
        with pytest.raises(SeanbotError, match="regular file"):
            load_policy(path, "owner/test", "read")


@pytest.mark.parametrize(
    "old,new",
    [
        ("installation_id = 123", "installation_id = true"),
        ('"/private/app.pem"', '"relative.pem"'),
        ('["read", "contribute"]', '["admin"]'),
        ('client_id = "Iv1.example"', "client_id = 123"),
    ],
)
def test_bad_config(tmp_path, old, new):
    path = config(tmp_path)
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(SeanbotError):
        load_policy(path, "owner/test", "read")


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
