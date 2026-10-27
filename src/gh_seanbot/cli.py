"""Command-line entry point. No command prints or exports a raw token."""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from .auth import mint, revoke
from .config import PROFILES, SeanbotError, load_policy
from .provenance import Audit, Provenance
from .runner import api_command, environment, push, run_process


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="gh-seanbot")
    commands = result.add_subparsers(dest="command", required=True)
    metadata = argparse.ArgumentParser(add_help=False)
    for name in ("harness", "model", "run"):
        metadata.add_argument(f"--{name}", required=True)
    footer = commands.add_parser("footer", parents=[metadata])
    footer.add_argument("--body", type=Path, help="Read body from file (default: stdin)")
    execution = argparse.ArgumentParser(parents=[metadata], add_help=False)
    execution.add_argument("--policy", type=Path, required=True)
    execution.add_argument("--repo", required=True)
    execution.add_argument("--capability", choices=PROFILES, default="read")
    execution.add_argument("--max-seconds", type=int, default=900)
    api = commands.add_parser(
        "api", parents=[execution], help="Repository REST API via isolated gh"
    )
    api.add_argument("--method", choices=["GET", "POST", "PATCH", "PUT", "DELETE"], default="GET")
    api.add_argument("--endpoint", required=True, help="Relative to repos/OWNER/REPO/")
    api.add_argument("--input", type=Path, help="JSON request body file")
    git = commands.add_parser(
        "push", parents=[execution], help="Push one commit to an HTTPS branch"
    )
    git.add_argument("--source", type=Path, default=Path.cwd())
    git.add_argument("--sha", required=True)
    git.add_argument("--branch", required=True)
    return result


def execute(args: argparse.Namespace) -> int:
    provenance = Provenance(args.harness, args.model, args.run)
    if args.command == "footer":
        body = args.body.read_text() if args.body else sys.stdin.read()
        sys.stdout.write(provenance.footer(body))
        return 0
    if args.max_seconds <= 0:
        raise SeanbotError("--max-seconds must be positive")
    policy = load_policy(args.policy, args.repo, args.capability)
    audit = Audit(policy.audit_log)
    if args.command == "push" and args.capability not in {"push", "contribute"}:
        raise SeanbotError("Push requires push or contribute capability")
    command = None
    if args.command == "api":
        command = api_command(policy.repository, args.method, args.endpoint, args.input)
    audit.record(provenance, args.repo, args.capability, "started")
    # Ignore proxy/netrc configuration inherited from a human environment.
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        token = None
        try:
            token = mint(policy, client)
            audit.record(provenance, args.repo, args.capability, "authenticated")
            with tempfile.TemporaryDirectory(prefix="seanbot-home-") as directory:
                env = environment(os.environ, token.value, policy.repository, Path(directory))
                timeout = min(args.max_seconds, token.remaining())
                if command is not None:
                    code = run_process(command, env, timeout)
                else:
                    code = push(policy.repository, args.sha, args.branch, args.source, env, timeout)
            audit.record(provenance, args.repo, args.capability, "finished", exit_code=code)
        except BaseException:
            audit.record(provenance, args.repo, args.capability, "failed")
            raise
        finally:
            if token is not None:
                revoked = revoke(token, client)
                audit.record(provenance, args.repo, args.capability, "revoked", success=revoked)
        if not revoked:
            raise SeanbotError("Command ended, but token revocation failed; token may remain valid")
        return code


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    signal.signal(signal.SIGTERM, _interrupt)
    args = parser().parse_args()
    try:
        return execute(args)
    except SeanbotError as error:
        print(f"gh-seanbot: {error}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError):
        print("gh-seanbot: local file or subprocess operation failed", file=sys.stderr)
    except KeyboardInterrupt:
        print("gh-seanbot: interrupted", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
