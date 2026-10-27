"""Validated text provenance and payload-free lifecycle audit records."""

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import SeanbotError

VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:+-]{0,199}\Z")
FOOTER = re.compile(r"\n*<!-- agent: [^\n]* -->\s*\Z")


@dataclass(frozen=True)
class Provenance:
    harness: str
    model: str
    run: str

    def __post_init__(self) -> None:
        for value in (self.harness, self.model, self.run):
            if not VALUE.fullmatch(value) or "--" in value:
                raise SeanbotError("Invalid provenance value: use short identifiers, not free text")

    def footer(self, body: str) -> str:
        body = FOOTER.sub("", body).rstrip()
        return f"{body}\n\n<!-- agent: {self.harness}; model: {self.model}; run: {self.run} -->\n"


class Audit:
    """Append to a private regular file. Directory access/retention is operator-owned."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self,
        provenance: Provenance,
        repository: str,
        capability: str,
        event: str,
        **outcome: int | bool,
    ) -> None:
        data = {
            "time": datetime.now(UTC).isoformat(),
            "run": provenance.run,
            "harness": provenance.harness,
            "model": provenance.model,
            "repository": repository,
            "capability": capability,
            "event": event,
            **outcome,
        }
        try:
            fd = os.open(
                self.path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            with os.fdopen(fd, "a") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise SeanbotError("Audit log must be a private regular file (mode 0600)")
                stream.write(json.dumps(data, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise SeanbotError("Cannot write audit log; refusing unaudited execution") from None
