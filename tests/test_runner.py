import os
import subprocess
import sys
from pathlib import Path

import pytest

from gh_seanbot.config import SeanbotError
from gh_seanbot.runner import api_command, environment, push, run_process


def test_environment_drops_human_credentials(tmp_path):
    parent = {
        "PATH": os.environ["PATH"],
        "GH_TOKEN": "human",
        "GITHUB_TOKEN": "human2",
        "GH_ENTERPRISE_TOKEN": "human3",
        "GH_DEBUG": "api",
        "GH_HOST": "evil.example",
        "GH_CONFIG_DIR": "/human/gh",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": "SECRET",
        "SSH_AUTH_SOCK": "/human/ssh",
        "GIT_TRACE_CURL": "1",
        "CUSTOM_SECRET": "secret",
        "HOME": "/human",
        "HTTPS_PROXY": "https://proxy",
        "NETRC": "/human/netrc",
    }
    env = environment(parent, "app-token", "owner/test", tmp_path)
    assert env["GH_TOKEN"] == "app-token"
    assert env["HOME"] == str(tmp_path)
    assert env["GH_REPO"] == "owner/test"
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert (
        not set(parent).difference({"PATH", "GH_TOKEN", "GH_HOST", "GH_CONFIG_DIR", "HOME"})
        & env.keys()
    )
    assert not {"human", "human2", "human3", "/human", "/human/gh"} & set(env.values())


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://evil.example",
        "../installation/token",
        "/issues",
        "issues?x=1",
        "issues/../pulls",
        "--hostname",
    ],
)
def test_api_path_rejected(endpoint):
    with pytest.raises(SeanbotError):
        api_command("owner/test", "GET", endpoint, None)


def test_api_fixed_host_and_repo(tmp_path):
    command = api_command("owner/test", "POST", "issues/1/comments", tmp_path / "payload.json")
    assert command[:6] == ["gh", "api", "--hostname", "github.com", "--method", "POST"]
    assert command[6] == "repos/owner/test/issues/1/comments"


def test_process_real_exit_and_timeout(tmp_path):
    env = environment(os.environ, "app-token", "owner/test", tmp_path)
    assert run_process([sys.executable, "-c", "raise SystemExit(7)"], env, 5) == 7
    with pytest.raises(SeanbotError, match="expiry"):
        run_process([sys.executable, "-c", "import time; time.sleep(10)"], env, 0.1)


def test_gh_stub_sees_only_app_token(tmp_path):
    stub = tmp_path / "gh"
    stub.write_text(f"""#!{sys.executable}
import os, sys
assert os.environ['GH_TOKEN'] == 'app-token'
assert 'GITHUB_TOKEN' not in os.environ
assert 'GH_DEBUG' not in os.environ
assert sys.argv[1:4] == ['api', '--hostname', 'github.com']
""")
    stub.chmod(0o700)
    env = environment(
        {**os.environ, "GITHUB_TOKEN": "human", "GH_DEBUG": "api"},
        "app-token",
        "owner/test",
        tmp_path,
    )
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    assert run_process(api_command("owner/test", "GET", "issues", None), env, 5) == 0


def test_push_ignores_source_network_config(tmp_path, monkeypatch):
    source = tmp_path / "source"
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "credential.helper", "!echo HUMAN"], check=True
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "url.ssh://evil/.insteadOf", "https://github.com/"],
        check=True,
    )
    env = environment(os.environ, "app-token", "owner/test", tmp_path / "home")
    (tmp_path / "home").mkdir()
    seen = []

    def inspect(command, actual_env, timeout):
        seen.append(command)
        bare = Path(command[2])
        text = (bare / "config").read_text()
        assert "HUMAN" not in text and "evil" not in text
        assert (bare / "objects/info/alternates").read_text().strip() == str(
            source / ".git/objects"
        )
        assert "app-token" not in repr(command)
        assert command[-2:] == ["https://github.com/owner/test.git", "a" * 40 + ":refs/heads/test"]
        assert actual_env["GIT_ALLOW_PROTOCOL"] == "https"
        # Exercise Git's real credential-helper protocol without contacting a network.
        credential = subprocess.check_output(
            command[: command.index("push")] + ["credential", "fill"],
            input=b"protocol=https\nhost=github.com\n\n",
            env=actual_env,
        )
        assert b"username=x-access-token" in credential
        assert b"password=app-token" in credential
        return 0

    monkeypatch.setattr("gh_seanbot.runner.run_process", inspect)
    assert push("owner/test", "a" * 40, "test", source, env, 20) == 0
    assert len(seen) == 1


@pytest.mark.parametrize(
    "sha,branch", [("main", "test"), ("a" * 40, "--all"), ("a" * 40, "foo//bar")]
)
def test_push_rejects_ambiguous_inputs(tmp_path, sha, branch):
    with pytest.raises(SeanbotError):
        push("owner/test", sha, branch, tmp_path, {}, 10)
