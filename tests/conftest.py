from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gh_seanbot.config import Policy


@pytest.fixture
def policy(tmp_path: Path) -> Policy:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    return Policy(
        "Iv1.example", 123, path, tmp_path / "audit.jsonl", {"contents": "write"}, "owner/test"
    )
