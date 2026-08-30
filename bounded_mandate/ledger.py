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
import threading
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

    `append` is serialised by a lock, and that is not a scalability nicety —
    it is what makes the chain a chain. The write is read-head → compute →
    append, so two threads interleaving inside it both read the same head and
    both mint `seq`, and `verify()` then raises on its own output.

    This is reachable at zero load, not at high volume: every route that writes
    is a sync `def`, which FastAPI runs in anyio's threadpool, and the scheduler
    adds `asyncio.to_thread(run_due_lists)` on every tick. One tick overlapping
    one request is enough. Since `/api/home` renders `chain_intact`, the failure
    mode was the tamper-evidence screen accusing itself.

    ponytail: the lock is per-process, so it holds for one engine and not for
    two. Take an advisory file lock (or move to SQLite) before running a second
    worker — the guarantee this class advertises does not survive `--workers 2`.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        # Reentrant, and public: `append` takes it, and `decide` takes it
        # *around* `append` to make the duplicate check and the write that
        # satisfies it one critical section. A plain Lock would deadlock on the
        # nested acquire.
        #
        # Not a `dataclass` field: a lock is per-instance machinery, and every
        # caller shares one `Ledger` per process, which is what makes it work.
        self.lock = threading.RLock()
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
        """Append one record and return it, chained to the current head.

        The whole body is under the lock, including the read: reading the head
        outside it and writing inside would serialise the wrong half.
        """
        with self.lock:
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
