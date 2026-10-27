"""Fresh repository-scoped tokens. Never expose upstream response bodies."""

import os
import stat
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import jwt

from .config import Policy, SeanbotError


@dataclass(frozen=True)
class InstallationToken:
    value: str = field(repr=False)
    expires_at: datetime

    def remaining(self) -> float:
        return (self.expires_at - datetime.now(UTC)).total_seconds() - 60


def mint(policy: Policy, client: httpx.Client) -> InstallationToken:
    try:
        # Refuse symlinks and group/world-readable key material.
        fd = os.open(policy.private_key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise SeanbotError("Private key must be a regular file with mode 0600 or stricter")
            key = stream.read()
        now = int(time.time())
        assertion = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": policy.client_id}, key, algorithm="RS256"
        )
        # Verify owner as well as name: the mint endpoint accepts repository names, not owner/name.
        installation = client.get(
            f"https://api.github.com/repos/{policy.repository}/installation",
            headers=_headers(assertion),
        )
        if installation.status_code != 200 or installation.json()["id"] != policy.installation_id:
            raise SeanbotError("Repository does not belong to the configured installation")
        response = client.post(
            f"https://api.github.com/app/installations/{policy.installation_id}/access_tokens",
            headers=_headers(assertion),
            json={
                "repositories": [policy.repository.split("/")[1]],
                "permissions": policy.permissions,
            },
        )
        if response.status_code != 201:
            raise SeanbotError(f"Installation token mint failed (HTTP {response.status_code})")
        body = response.json()
        token = InstallationToken(
            body["token"], datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        )
        if not isinstance(token.value, str) or not token.value or token.remaining() <= 0:
            raise ValueError
        return token
    except SeanbotError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        jwt.PyJWTError,
        httpx.HTTPError,
    ):
        raise SeanbotError(
            "Could not authenticate the App or validate the token response"
        ) from None


def revoke(token: InstallationToken, client: httpx.Client) -> bool:
    try:
        return (
            client.delete(
                "https://api.github.com/installation/token", headers=_headers(token.value)
            ).status_code
            == 204
        )
    except httpx.HTTPError:
        return False


def _headers(credential: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
