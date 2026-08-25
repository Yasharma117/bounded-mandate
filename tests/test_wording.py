"""Codes are for the ledger. These are for the person holding the phone."""

from __future__ import annotations

import re

from bounded_mandate import engine
from bounded_mandate.wording import TITLES, summary, title

CODES_IN_ENGINE = set(re.findall(r'"([a-z]+\.[a-z_]+)"', open(engine.__file__).read()))


def test_every_code_the_engine_can_emit_has_words():
    """A code reaching the screen is a bug in the table, and this is where it
    gets caught rather than in a screenshot."""
    missing = CODES_IN_ENGINE - set(TITLES)
    assert not missing, f"no wording for: {sorted(missing)}"


def test_no_title_leaks_an_identifier():
    for code, words in TITLES.items():
        assert "_" not in words, f"{code} still reads like a symbol"
        assert "." not in words, f"{code} still reads like a symbol"
        assert words[0].isupper()


def test_an_unmapped_code_still_reads_as_english():
    assert title("something.went_sideways") == "Went sideways"


def test_a_stack_of_reasons_reads_as_a_sentence():
    assert summary("cap.exceeded") == "Over your limit"
    assert summary("category.not_allowed+cap.exceeded") == (
        "Not what you allowed and over your limit"
    )
    assert summary("provenance.total_mismatch+category.not_allowed+cap.exceeded") == (
        "The agent misreported the total, Not what you allowed and over your limit"
    )


def test_the_table_does_not_rot_against_the_engine():
    """Titles for codes the engine no longer emits are dead weight, and dead
    weight in a translation table is how the wrong word ships."""
    stale = set(TITLES) - CODES_IN_ENGINE
    assert not stale, f"wording for codes the engine never emits: {sorted(stale)}"
