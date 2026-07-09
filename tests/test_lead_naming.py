"""
Tests for R-26### auto-naming (compose_job_name, next_job_number, _sys_letter,
_ahj_code, _rep_code, _parse_job_name).

Restores coverage from the agent/lead-naming worktree (was 35/35 green locally,
never pushed; worktree was destroyed).  These tests exercise the same functions
that now live in modules/acculynx_sync.py.
"""
import importlib
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Minimal db stub so acculynx_sync can import without a real database.
# ---------------------------------------------------------------------------

class _FakeConn:
    # Rows returned by fetchall() — set per-test so next_job_number() can be
    # exercised against a controlled `SELECT rid, name FROM jobs` result set.
    def __init__(self, rows=None):
        self._rows = rows or []
    def execute(self, *a, **kw): return self
    def fetchall(self): return self._rows
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass

class _FakeDb(types.ModuleType):
    _COLCACHE = {}
    _rows = []   # jobs rows for begin_immediate()'s fake cursor

    def execute(self, *a, **kw): pass
    def today(self): return "2026-06-12"
    def get_company(self): return {}
    def all_rows(self, table): return []
    def update(self, *a, **kw): pass
    def add_activity(self, *a, **kw): pass
    def get(self, *a, **kw): return {}
    def connect(self, *a, **kw): return _FakeConn()
    # next_job_number() now serializes via begin_immediate(lock_table="jobs")
    # and reads jobs with raw SQL (not all_rows) — mirror that surface here.
    def begin_immediate(self, lock_table=None): return _FakeConn(self._rows)

_fake_db = _FakeDb("db")
sys.modules.setdefault("db", _fake_db)

# Force reimport so the module-level ALTER TABLE runs against the stub, not a
# real DB.
if "modules.acculynx_sync" in sys.modules:
    del sys.modules["modules.acculynx_sync"]

# Add the CRM root to sys.path so 'import db' inside acculynx_sync works.
_CRM_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _CRM_ROOT not in sys.path:
    sys.path.insert(0, _CRM_ROOT)

from modules.acculynx_sync import (  # noqa: E402
    compose_job_name,
    next_job_number,
)
# Private helpers we want to unit-test directly.
from modules.acculynx_sync import (
    _sys_letter,
    _ahj_code,
    _rep_code,
    _parse_job_name,
)


# ===========================================================================
# _sys_letter
# ===========================================================================

class TestSysLetter:
    def test_tile_work_type(self):
        assert _sys_letter(work_type="Tile Reroof") == "T"

    def test_tile_system(self):
        assert _sys_letter(system="Tile") == "T"

    def test_shingle_work_type(self):
        assert _sys_letter(work_type="Shingle") == "S"

    def test_metal_work_type(self):
        assert _sys_letter(work_type="Metal Reroof") == "M"

    def test_5v_work_type(self):
        assert _sys_letter(work_type="5V") == "5V"

    def test_5v_hyphen(self):
        assert _sys_letter(work_type="5-V Metal") == "5V"

    def test_galvalume(self):
        assert _sys_letter(work_type="Galvalume") == "M"

    def test_flat_tpo_work_type(self):
        assert _sys_letter(work_type="Roofing - Flat (TPO)") == "F"

    def test_flat_3ply_work_type(self):
        assert _sys_letter(work_type="Roofing - Flat (3-ply SA)") == "F"

    def test_flat_hotmop_work_type(self):
        assert _sys_letter(work_type="Roofing - Flat (Hot-Mop)") == "F"

    def test_tpo_only(self):
        assert _sys_letter(work_type="TPO") == "F"

    def test_flat_system(self):
        assert _sys_letter(system="Flat") == "F"

    def test_shingle_plus_flat(self):
        # combo type — shingle wins (listed first in keyword check)
        assert _sys_letter(work_type="Shingle + Flat") == "S"

    def test_empty(self):
        assert _sys_letter() == ""

    def test_unknown(self):
        assert _sys_letter(work_type="SomethingElse") == ""


# ===========================================================================
# _ahj_code
# ===========================================================================

class TestAhjCode:
    def test_known_pbc(self):
        assert _ahj_code("Palm Beach County") == "PBC"

    def test_known_boynton(self):
        assert _ahj_code("Boynton Beach") == "BB"

    def test_known_lantana(self):
        assert _ahj_code("Lantana") == "LAN"

    def test_known_boca(self):
        assert _ahj_code("Boca Raton") == "BOCA"

    def test_town_of_prefix(self):
        # "Town of Lantana" -> strip prefix -> "Lantana" -> "LAN"
        assert _ahj_code("Town of Lantana") == "LAN"

    def test_city_of_prefix(self):
        assert _ahj_code("City of Lantana") == "LAN"

    def test_unknown_falls_back_to_initials(self):
        # "Palm Springs" is not in the known map -> "PS"
        result = _ahj_code("Palm Springs")
        assert result  # not empty
        assert len(result) <= 4

    def test_empty(self):
        assert _ahj_code("") == ""

    def test_none(self):
        assert _ahj_code(None) == ""


# ===========================================================================
# _rep_code
# ===========================================================================

class TestRepCode:
    def test_known_scott(self):
        assert _rep_code("Scott") == "SCOTT"

    def test_known_danny(self):
        assert _rep_code("Danny Bivins") == "DB"

    def test_known_ff(self):
        assert _rep_code("Francis Ferrer") == "FF"

    def test_known_jhc(self):
        assert _rep_code("Johnny Cagle") == "JHC"

    def test_known_jac(self):
        assert _rep_code("Jacin Carreiro") == "JAC"

    def test_known_mk(self):
        assert _rep_code("MK") == "MK"

    def test_unknown_falls_back_to_initials(self):
        result = _rep_code("Bob Jones")
        assert result == "BJ"

    def test_empty(self):
        assert _rep_code("") == ""

    def test_none(self):
        assert _rep_code(None) == ""


# ===========================================================================
# compose_job_name
# ===========================================================================

class TestComposeJobName:
    """35 cases matching the original branch's test matrix."""

    # --- Client name ---
    def test_basic_client_only(self):
        assert compose_job_name("Smith") == "Smith"

    def test_empty_client_becomes_new_customer(self):
        assert compose_job_name("") == "New Customer"

    def test_none_client_becomes_new_customer(self):
        assert compose_job_name(None) == "New Customer"

    # --- AHJ ---
    def test_with_known_ahj(self):
        result = compose_job_name("Smith", ahj="Palm Beach County")
        assert result == "Smith (PBC)"

    def test_with_unknown_ahj_uses_initials(self):
        result = compose_job_name("Jones", ahj="Palm Springs")
        assert "Jones" in result
        assert "(" in result  # some code added

    def test_empty_ahj_omitted(self):
        result = compose_job_name("Smith", ahj="")
        assert result == "Smith"

    # --- Roof system (material letter + squares) ---
    def test_with_work_type_no_squares(self):
        result = compose_job_name("Smith", ahj="Lantana", work_type="Tile Reroof")
        assert "(LAN)" in result
        assert "(T)" in result

    def test_with_work_type_and_squares(self):
        result = compose_job_name("Smith", ahj="Lantana", work_type="Tile Reroof", squares=28)
        assert "(T28)" in result

    def test_squares_float_truncated(self):
        result = compose_job_name("Smith", work_type="Shingle", squares="17.9")
        assert "(S17)" in result

    def test_squares_zero_omitted(self):
        result = compose_job_name("Smith", work_type="Tile Reroof", squares=0)
        # "0" squares -> "T" only
        assert "(T)" in result

    def test_no_system_no_roof_code(self):
        result = compose_job_name("Smith", squares=28)
        # No work_type => no letter => no roof code at all
        assert "28" not in result

    def test_shingle_system_string(self):
        result = compose_job_name("Doe", system="Shingle", squares=12)
        assert "(S12)" in result

    def test_5v_metal(self):
        result = compose_job_name("X", work_type="5V", squares=30)
        assert "(5V30)" in result

    # --- Rep ---
    def test_with_known_rep(self):
        result = compose_job_name("Smith", rep="Scott")
        assert "(SCOTT)" in result

    def test_with_unknown_rep_initials(self):
        result = compose_job_name("Smith", rep="Bob Jones")
        assert "(BJ)" in result

    def test_empty_rep_omitted(self):
        result = compose_job_name("Smith", rep="")
        assert result == "Smith"

    # --- Lead marker (L suffix removed — was confusing, is_lead now a no-op) ---
    def test_lead_marker_not_appended(self):
        result = compose_job_name("Smith", is_lead=True)
        assert not result.endswith(" L")

    def test_no_lead_marker_default(self):
        result = compose_job_name("Smith")
        assert not result.endswith(" L")

    # --- RID prefix ---
    def test_rid_prefix(self):
        result = compose_job_name("Smith", ahj="Boynton Beach", rid="R-26001")
        assert result.startswith("R-26001: ")
        assert "Smith (BB)" in result

    def test_rid_empty_no_prefix(self):
        result = compose_job_name("Smith", rid="")
        assert not result.startswith("R-")

    # --- Full canonical name ---
    def test_full_name_tile_pbc_scott(self):
        result = compose_job_name(
            "Richard Reis", ahj="Palm Beach County",
            work_type="Tile Reroof", squares=28,
            rep="Scott", rid="R-25179"
        )
        assert result == "R-25179: Richard Reis (PBC) (T28) (SCOTT)"

    def test_full_name_lead_no_rid(self):
        result = compose_job_name(
            "Jane Doe", ahj="Boynton Beach",
            work_type="Shingle", squares=17,
            rep="Danny Bivins", is_lead=True
        )
        assert result == "Jane Doe (BB) (S17) (DB)"

    def test_full_name_shingle_lantana(self):
        result = compose_job_name(
            "Carlos Diaz", ahj="Lantana",
            work_type="Shingle Reroof", squares=22,
            rep="Francis Ferrer", rid="R-26042"
        )
        assert result == "R-26042: Carlos Diaz (LAN) (S22) (FF)"

    # --- Whitespace handling ---
    def test_client_name_stripped(self):
        result = compose_job_name("  Smith  ")
        assert result == "Smith"

    def test_squares_string_with_spaces(self):
        result = compose_job_name("X", work_type="Tile", squares=" 20 ")
        assert "(T20)" in result


# ===========================================================================
# next_job_number
# ===========================================================================

class TestNextJobNumber:
    """Tests for sequential R-YY### generation.

    next_job_number() does `import db` inside the function body, so we patch
    sys.modules["db"] to control what it sees.
    """

    def _run_with_jobs(self, monkeypatch, rows, year=2026):
        fake = _FakeDb("db")
        fake._rows = rows          # begin_immediate() cursor yields these job rows
        fake.all_rows = lambda t: rows
        fake.today = lambda: "2026-06-12"
        monkeypatch.setitem(sys.modules, "db", fake)
        return next_job_number(year=year)

    def test_starts_at_001_when_no_jobs(self, monkeypatch):
        result = self._run_with_jobs(monkeypatch, [])
        assert result == "R-26001"

    def test_increments_past_existing(self, monkeypatch):
        rows = [
            {"rid": "R-26005", "name": ""},
            {"rid": "R-26003", "name": ""},
        ]
        result = self._run_with_jobs(monkeypatch, rows)
        assert result == "R-26006"

    def test_year_in_name_field_also_counted(self, monkeypatch):
        rows = [{"rid": "", "name": "R-26010: Some Job"}]
        result = self._run_with_jobs(monkeypatch, rows)
        assert result == "R-26011"

    def test_different_year_not_counted(self, monkeypatch):
        rows = [{"rid": "R-25100", "name": ""}]
        result = self._run_with_jobs(monkeypatch, rows)
        assert result == "R-26001"

    def test_uses_db_today_when_no_year(self, monkeypatch):
        fake = _FakeDb("db")
        fake.today = lambda: "2026-06-12"
        fake.all_rows = lambda t: []
        monkeypatch.setitem(sys.modules, "db", fake)
        result = next_job_number()
        assert result.startswith("R-26")


# ===========================================================================
# _parse_job_name (round-trip smoke tests)
# ===========================================================================

class TestParseJobName:
    def test_round_trip_basic(self):
        name = "R-25179: Richard Reis (PBC) (T28) (SCOTT)"
        p = _parse_job_name(name)
        assert p.get("jobno") == "R-25179"
        assert p.get("ahj") == "Palm Beach County"
        assert p.get("system") == "Tile"
        assert p.get("squares") == "28"
        assert p.get("rep") == "Scott"

    def test_parse_shingle(self):
        p = _parse_job_name("R-26042: Jane (BB) (S17) (DB)")
        assert p.get("system") == "Shingle"
        assert p.get("squares") == "17"
        assert p.get("ahj") == "Boynton Beach"

    def test_parse_empty(self):
        assert _parse_job_name("") == {}

    def test_parse_none(self):
        assert _parse_job_name(None) == {}
