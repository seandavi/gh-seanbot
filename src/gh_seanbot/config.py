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


@dataclass(frozen=True)
class Policy:
    client_id: str
    installation_id: int
    private_key: Path
    audit_log: Path
    permissions: dict[str, str]
    repository: str


def load_policy(path: Path, repository: str, capability: str) -> Policy:
    if not REPO.fullmatch(repository) or repository.split("/")[1] in {".", ".."}:
        raise SeanbotError("Repository must be owner/name on github.com")
    if capability not in PROFILES:
        raise SeanbotError("Unknown capability")
    try:
        data = tomllib.loads(path.read_text())
        if set(data) != {"app", "repositories", "audit_log"}:
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
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SeanbotError("Invalid or unreadable policy file") from None
