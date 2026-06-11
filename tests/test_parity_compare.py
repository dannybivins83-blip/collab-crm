# -*- coding: utf-8 -*-
"""Tests for the parity tooling — the math behind the AccuLynx-cancel verdict.
A bug here could wrongly declare parity, so the money parser + tolerance logic are tested.
Run: python -m pytest tests/test_parity_compare.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import parity_compare as P  # noqa: E402


def test_money_basic():
    assert P.money("$1,234.50") == 1234.50
    assert P.money("1234.5") == 1234.5
    assert P.money(None) == 0.0
    assert P.money("") == 0.0


def test_money_negatives():
    # the whole point of the #3 est_num fix: signs must survive
    assert P.money("-$500") == -500.0
    assert P.money("($500)") == -500.0          # accounting negative
    assert P.money("$500") == 500.0


def test_money_malformed():
    # multi-dot -> 0 defensively (never a garbage huge number)
    assert P.money("$1.234.50") == 0.0
    assert P.money("abc") == 0.0


def test_verdict_money_tolerance():
    tol = P.DEFAULT_TOL
    assert P.verdict(100.0, 100.0, tol, "money")[0] is True      # exact
    assert P.verdict(100.0, 100.5, tol, "money")[0] is True       # 0.5% within 1%
    assert P.verdict(100.0, 150.0, tol, "money")[0] is False      # 50% off
    assert P.verdict(100.0, None, tol, "money")[0] is None        # no reference
    assert P.verdict(None, 100.0, tol, "money")[0] is False       # CRM missing


def test_verdict_count_and_zero():
    tol = P.DEFAULT_TOL
    assert P.verdict(1000, 1000, tol, "count")[0] is True
    assert P.verdict(1000, 1200, tol, "count")[0] is False        # 20% off
    assert P.verdict(0, 0, tol, "count")[0] is True               # both zero
    assert P.verdict(5, 0, tol, "count")[0] is False              # ref=0, crm!=0
