# -*- coding: utf-8 -*-
"""Root-level guard for the AccuLynx money-string 500 class.

AccuLynx-imported amounts land in SQLite as comma/symbol TEXT ("$1,200.50",
"1,200.50", "N/A"). theme.money()/money_k() did a bare float() and 500'd every
page that rendered them (invoices, homeowner portal, dashboard home). And a
corrupt jobs.payments blob could be a non-dict, so theme.paid_pct() raised
AttributeError. These are hardened at the root so no caller can crash on them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("CRM_NOBROWSER", "1")

import theme


def test_money_never_raises_on_dirty_input():
    for v in ["$1,200.50", "1,200.50", "$5,000", "N/A", "", None, 0, 1200.5,
              -500, "(500)", "1.234.50", "abc", "  ", "$-0"]:
        out = theme.money(v)
        assert isinstance(out, str) and out.startswith("$")
    # Parsing sanity
    assert theme.money("$1,200.50") == "$1,200.50"
    assert theme.money("N/A") == "$0"
    assert theme.money(None) == "$0"


def test_money_k_never_raises():
    for v in ["1,000,000", None, "N/A", 2500, 0, "abc"]:
        out = theme.money_k(v)
        assert isinstance(out, str) and out.startswith("$")


def test_paid_pct_handles_non_dict():
    assert theme.paid_pct({}) == 0.0
    assert theme.paid_pct("corrupt-json-string") == 0.0
    assert theme.paid_pct(None) == 0.0
    assert theme.paid_pct([1, 2, 3]) == 0.0


if __name__ == "__main__":
    test_money_never_raises_on_dirty_input()
    test_money_k_never_raises()
    test_paid_pct_handles_non_dict()
    print("ok")
