"""Constrained gh API and HTTPS push paths for trusted local callers, not a sandbox."""

import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import SeanbotError, valid_branch


# Start from a small allowlist, rather than trying to enumerate every inherited secret.
def environment(parent: Mapping[str, str], token: str, repo: str, home: Path) -> dict[str, str]:
    env = {key: parent[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in parent}
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home),
            "GH_CONFIG_DIR": str(home / "gh"),
            "GH_TOKEN": token,
            "GH_HOST": "github.com",
            "GH_REPO": repo,
            "GH_PROMPT_DISABLED": "1",
            "GH_PAGER": "cat",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false",
            "SSH_ASKPASS": "/bin/false",
            "GIT_SSH_COMMAND": "/bin/false",
            "GIT_ALLOW_PROTOCOL": "https",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    return env


def api_command(repository: str, method: str, endpoint: str, payload: Path | None) -> list[str]:
    # No host overrides, absolute URLs, query strings, traversal, or arbitrary gh subcommands.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)*", endpoint) or any(
        set(segment) == {"."} for segment in endpoint.split("/")
    ):
        raise SeanbotError(
            "Endpoint must be a relative repository API path without query parameters"
        )
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
        raise SeanbotError("Unsupported HTTP method")
    command = [
        "gh",
        "api",
        "--hostname",
        "github.com",
        "--method",
        method,
        f"repos/{repository}/{endpoint}",
    ]
    if payload is not None:
        command.extend(["--input", str(payload.resolve())])
    return command


def run_process(command: Sequence[str], env: dict[str, str], timeout: float) -> int:
    """Stop the entire foreground process group on timeout or interruption (POSIX)."""
    if timeout <= 0:
        raise SeanbotError("Token expires too soon to launch command")
    try:
        child = subprocess.Popen(command, env=env, start_new_session=True)
    except OSError:
        raise SeanbotError("Could not launch command; check gh/git installation") from None
    try:
        return child.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        raise SeanbotError("Command interrupted or stopped before token expiry") from None


def push(
    repository: str, sha: str, branch: str, source: Path, env: dict[str, str], timeout: float
) -> int:
    deadline = time.monotonic() + timeout
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SeanbotError("Push requires an explicit full SHA-1 commit ID")
    if not valid_branch(branch):
        raise SeanbotError("Invalid destination branch")
    # Source repository configuration never participates in the network operation.
    # Only object files are shared with a disposable bare repository.
    try:
        objects = (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(source.resolve()),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "objects",
                ],
                env=env,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            .decode()
            .strip()
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise SeanbotError("Cannot locate source repository objects") from None
    if "\n" in objects or not Path(objects).is_dir():
        raise SeanbotError("Invalid source object directory")
    with tempfile.TemporaryDirectory(prefix="seanbot-push-") as directory:
        bare = Path(directory) / "push.git"
        result = subprocess.run(
            ["git", "init", "--bare", "--quiet", str(bare)], env=env, timeout=10, check=False
        )
        if result.returncode:
            raise SeanbotError("Cannot initialize isolated push repository")
        (bare / "objects/info/alternates").write_text(objects + "\n")
        # Git invokes this helper internally. Secret is read from env, never embedded in argv/file.
        helper = (
            '!f() { if [ "$1" = get ]; then '
            'printf "username=x-access-token\\npassword=%s\\n" "$GH_TOKEN"; fi; }; f'
        )
        command = [
            "git",
            "--git-dir",
            str(bare),
            "-c",
            "credential.helper=",
            "-c",
            f"credential.helper={helper}",
            "-c",
            "core.hooksPath=/dev/null",
            "push",
            "--no-verify",
            f"https://github.com/{repository}.git",
            f"{sha}:refs/heads/{branch}",
        ]
        return run_process(command, env, deadline - time.monotonic())
