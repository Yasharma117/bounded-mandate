"""Append-only, hash-chained decision ledger.

Every entry carries the SHA-256 of the entry before it, so "append-only,
replayable" is a property you can *verify* rather than a convention you trust.
Editing or removing any past entry breaks the chain from that point on.

Storage is one JSON object per line. This is deliberately not a database: what
the product needs from the ledger is replay and tamper-evidence, not queries.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class Entry:
    """One immutable decision record."""

    seq: int
    ts: str
    prev_hash: str
    hash: str
    payload: dict[str, Any]


def _digest(seq: int, ts: str, prev_hash: str, payload: dict[str, Any]) -> str:
    # sort_keys makes the digest independent of dict insertion order, so a
    # replay on another machine produces the same chain.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{seq}|{ts}|{prev_hash}|{canonical}".encode()).hexdigest()


class ChainBroken(Exception):
    """Raised when the ledger's hash chain does not verify."""


class Ledger:
    """A hash-chained JSONL ledger.

    ponytail: single-process, no file locking. Fine for one engine process;
    add an advisory lock (or move to SQLite) before running two.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> Iterator[Entry]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield Entry(**json.loads(line))

    def head(self) -> Entry | None:
        last = None
        for last in self.entries():  # noqa: B007 - we want the final value
            pass
        return last

    def append(self, payload: dict[str, Any], *, now: datetime | None = None) -> Entry:
        """Append one record and return it, chained to the current head."""
        head = self.head()
        seq = 0 if head is None else head.seq + 1
        prev_hash = GENESIS_HASH if head is None else head.hash
        ts = (now or datetime.now(UTC)).isoformat()
        entry = Entry(
            seq=seq,
            ts=ts,
            prev_hash=prev_hash,
            hash=_digest(seq, ts, prev_hash, payload),
            payload=payload,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), sort_keys=True, ensure_ascii=False) + "\n")
        return entry

    def verify(self) -> int:
        """Walk the chain. Returns the entry count, raises `ChainBroken` on tamper."""
        expected_prev = GENESIS_HASH
        count = 0
        for entry in self.entries():
            if entry.seq != count:
                raise ChainBroken(f"entry {count}: out-of-order seq {entry.seq}")
            if entry.prev_hash != expected_prev:
                raise ChainBroken(f"entry {entry.seq}: prev_hash does not match entry {count - 1}")
            if entry.hash != _digest(entry.seq, entry.ts, entry.prev_hash, entry.payload):
                raise ChainBroken(f"entry {entry.seq}: contents do not match its hash")
            expected_prev = entry.hash
            count += 1
        return count
