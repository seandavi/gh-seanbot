import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from gh_seanbot.auth import mint, revoke
from gh_seanbot.config import SeanbotError


def response_body():
    return {
        "token": "ghs_TEST_SECRET",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "permissions": {"contents": "write"},
        "repository_selection": "selected",
        "repositories": [{"full_name": "owner/test"}],
    }


def scope_handler(body, delete_status=204, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 123})
        if request.method == "DELETE":
            return httpx.Response(delete_status)
        return httpx.Response(201, json=body)

    return handler


def test_mint_scopes_and_signs(policy):
    requests = []
    public_key = load_pem_private_key(policy.private_key.read_bytes(), password=None).public_key()

    def handler(request):
        requests.append(request)
        claims = jwt.decode(
            request.headers["Authorization"].split()[1], public_key, algorithms=["RS256"]
        )
        assert claims["iss"] == policy.client_id
        assert claims["exp"] - claims["iat"] == 600
        if request.method == "GET":
            assert request.url.path == "/repos/owner/test/installation"
            return httpx.Response(200, json={"id": 123})
        assert json.loads(request.content) == {
            "repositories": ["test"],
            "permissions": {"contents": "write"},
        }
        return httpx.Response(201, json=response_body())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = mint(policy, client)
    assert len(requests) == 2  # GET + POST, no DELETE
    assert token.value == "ghs_TEST_SECRET"
    assert "SECRET" not in repr(token)
    assert 3500 < token.remaining() < 3600


def test_mint_accepts_implicit_metadata_read_and_case(policy):
    body = response_body() | {
        "permissions": {"contents": "write", "metadata": "read"},
        "repositories": [{"full_name": "Owner/Test"}],
    }
    requests = []
    with httpx.Client(transport=httpx.MockTransport(scope_handler(body, requests=requests))) as c:
        assert mint(policy, c).value == "ghs_TEST_SECRET"
    assert [r.method for r in requests] == ["GET", "POST"]


MISMATCHES = {
    "extra-permission": {"permissions": {"contents": "write", "issues": "write"}},
    "raised-level": {"permissions": {"contents": "write"}},
    "missing-permission": {"permissions": {}},
    "missing-permissions": {"permissions": None},
    "metadata-write": {"permissions": {"contents": "write", "metadata": "write"}},
    "other-repository": {"repositories": [{"full_name": "owner/other"}]},
    "second-repository": {
        "repositories": [{"full_name": "owner/test"}, {"full_name": "owner/other"}]
    },
    "empty-repositories": {"repositories": []},
    "missing-repositories": {"repositories": None},
    "no-full-name": {"repositories": [{}]},
    "selection-all": {"repository_selection": "all"},
}


@pytest.mark.parametrize("case", MISMATCHES)
def test_scope_mismatch_revokes(policy, case):
    from dataclasses import replace

    body = response_body() | MISMATCHES[case]
    if case == "raised-level":
        policy = replace(policy, permissions={"contents": "read"})
    for key in [k for k, v in body.items() if v is None]:
        del body[key]
    requests = []
    with httpx.Client(transport=httpx.MockTransport(scope_handler(body, requests=requests))) as c:
        with pytest.raises(SeanbotError, match="token revoked") as error:
            mint(policy, c)
    assert "scope does not match" in str(error.value)
    assert "SECRET" not in str(error.value)
    deletes = [r for r in requests if r.method == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0].url.path == "/installation/token"
    assert deletes[0].headers["Authorization"] == "Bearer ghs_TEST_SECRET"


def test_scope_mismatch_revocation_failure(policy):
    body = response_body() | MISMATCHES["extra-permission"]
    with httpx.Client(transport=httpx.MockTransport(scope_handler(body, 500))) as client:
        with pytest.raises(SeanbotError, match="may remain valid") as error:
            mint(policy, client)
    assert "SECRET" not in str(error.value)


def test_wrong_owner_installation_never_mints(policy):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"id": 999})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SeanbotError, match="installation"):
            mint(policy, client)
    assert calls == ["GET"]


@pytest.mark.parametrize(
    "status,body",
    [
        (403, {"token": "SECRET"}),
        (201, {}),
        (201, {"token": "SECRET", "expires_at": "2000-01-01T00:00:00Z"}),
        (201, {"token": "SECRET", "expires_at": "not-a-date"}),
        (201, {"token": "SECRET", "expires_at": "2099-01-01T00:00:00"}),
    ],
)
def test_bad_responses_are_redacted(policy, status, body):
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"id": 123})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(status, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SeanbotError) as error:
            mint(policy, client)
    assert "SECRET" not in str(error.value)
    assert requests.count("DELETE") == (1 if "token" in body and status == 201 else 0)


@pytest.mark.parametrize("mode", [0o644, 0o640])
def test_key_permissions_fail_before_network(policy, mode):
    policy.private_key.chmod(mode)
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network"))) as client:
        with pytest.raises(SeanbotError, match="0600"):
            mint(policy, client)


def test_key_symlink_rejected(policy):
    from dataclasses import replace

    link = policy.private_key.with_name("link.pem")
    link.symlink_to(policy.private_key)
    with httpx.Client() as client:
        with pytest.raises(SeanbotError):
            mint(replace(policy, private_key=link), client)


def test_revocation_failure(policy):
    from gh_seanbot.auth import InstallationToken

    token = InstallationToken("SECRET", datetime.now(UTC))
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        assert not revoke(token, client)
