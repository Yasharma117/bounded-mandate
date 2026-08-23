"""Append-only has to be verifiable, not merely intended."""

from __future__ import annotations

import json

import pytest

from bounded_mandate import ChainBroken, Ledger


def test_chain_verifies(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    for n in range(3):
        ledger.append({"n": n})
    assert ledger.verify() == 3


def test_editing_a_past_entry_breaks_the_chain(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = Ledger(path)
    for amount in (185_000, 240_000, 179_000):
        ledger.append({"total_paise": amount})

    lines = path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["total_paise"] = 1  # quietly shrink a past charge
    lines[1] = json.dumps(tampered, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(ChainBroken):
        ledger.verify()


def test_deleting_an_entry_breaks_the_chain(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = Ledger(path)
    for n in range(3):
        ledger.append({"n": n})

    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")

    with pytest.raises(ChainBroken):
        ledger.verify()
