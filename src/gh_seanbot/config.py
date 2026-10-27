"""Operator-owned allowlist; deliberately limited to github.com."""

import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path


class SeanbotError(Exception):
    """Safe, actionable message suitable for display without a traceback."""


PROFILES: dict[str, dict[str, str]] = {
    "read": {"contents": "read", "pull_requests": "read", "issues": "read"},
    "issues": {"issues": "write"},
    "pull-requests": {"contents": "read", "pull_requests": "write"},
    "push": {"contents": "write"},
    "merge": {"contents": "write", "pull_requests": "write"},
    "contribute": {"contents": "write", "pull_requests": "write", "issues": "write"},
}
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\Z")
DEFAULT_PUSH_BRANCH_PREFIXES: tuple[str, ...] = ("agent/",)


def valid_branch(name: str) -> bool:
    # Git's check-ref-format rules for refs/heads/<branch>, within a stricter character set.
    return (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", name) is not None
        and ".." not in name
        and not name.endswith(".")
        and not any(
            not part or part.startswith(".") or part.endswith(".lock") for part in name.split("/")
        )
    )


@dataclass(frozen=True)
class Policy:
    client_id: str
    installation_id: int
    private_key: Path
    audit_log: Path
    permissions: dict[str, str]
    repository: str
    push_branch_prefixes: tuple[str, ...] = DEFAULT_PUSH_BRANCH_PREFIXES


def check_push_branch(policy: Policy, branch: str) -> None:
    if not valid_branch(branch):
        raise SeanbotError("Invalid destination branch")
    if not branch.startswith(policy.push_branch_prefixes):
        raise SeanbotError("Destination branch is not allowed by policy")


def load_policy(path: Path, repository: str, capability: str) -> Policy:
    if not REPO.fullmatch(repository) or repository.split("/")[1] in {".", ".."}:
        raise SeanbotError("Repository must be owner/name on github.com")
    if capability not in PROFILES:
        raise SeanbotError("Unknown capability")
    try:
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
        if (
            not {"app", "repositories", "audit_log"}
            <= set(data)
            <= {
                "app",
                "repositories",
                "audit_log",
                "push_branch_prefixes",
            }
        ):
            raise ValueError
        app = data["app"]
        if set(app) != {"client_id", "installation_id", "private_key"}:
            raise ValueError
        if not isinstance(app["client_id"], str) or not app["client_id"].strip():
            raise ValueError
        if type(app["installation_id"]) is not int or app["installation_id"] <= 0:
            raise ValueError
        repositories = data["repositories"]
        allowed = repositories.get(repository, [])
        if not isinstance(allowed, list) or any(item not in PROFILES for item in allowed):
            raise ValueError
        if capability not in allowed:
            raise SeanbotError("Repository/capability is not allowed by policy")
        prefixes_table = data.get("push_branch_prefixes", {})
        if not isinstance(prefixes_table, dict):
            raise ValueError
        entry = prefixes_table.get(repository)
        if entry is None:
            prefixes = DEFAULT_PUSH_BRANCH_PREFIXES
        else:
            if not isinstance(entry, list) or not entry:
                raise ValueError
            for item in entry:
                if not isinstance(item, str) or not item.endswith("/"):
                    raise ValueError
                if not valid_branch(item[:-1]):
                    raise ValueError
            prefixes = tuple(entry)
        key = Path(app["private_key"]).expanduser()
        audit = Path(data["audit_log"]).expanduser()
        if not key.is_absolute() or not audit.is_absolute():
            raise SeanbotError("Key and audit paths must be absolute")
        return Policy(
            app["client_id"],
            app["installation_id"],
            key,
            audit,
            PROFILES[capability].copy(),
            repository,
            prefixes,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SeanbotError("Invalid or unreadable policy file") from None
