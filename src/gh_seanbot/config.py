"""Operator-owned allowlist; deliberately limited to github.com."""

import re
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
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]*", name) is not None and not any(
        part in {"", ".", ".."} for part in name.split("/")
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
        data = tomllib.loads(path.read_text())
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
