# -*- coding: utf-8 -*-
"""בדיקות שכבת האחסון: כתיבה אטומית, הגנה על קובץ פגום, DPAPI, מיגרציות."""
import json
import os

import pytest

import storage


def test_atomic_write_and_load(tmp_path):
    p = str(tmp_path / "a.json")
    storage.atomic_write_json(p, {"x": 1})
    assert storage.load_json(p, {}) == {"x": 1}
    # אין קבצים זמניים שנשארו
    assert not any(f.startswith(".tmp-") for f in os.listdir(tmp_path))


def test_corrupt_file_raises_and_is_backed_up(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(storage.StorageError):
        storage.load_json(str(p), {})
    backups = [f for f in os.listdir(tmp_path) if ".corrupt-" in f]
    assert len(backups) == 1  # הקובץ הפגום גובה, לא נמחק


def test_missing_file_returns_default(tmp_path):
    assert storage.load_json(str(tmp_path / "nope.json"), {"d": 1}) == {"d": 1}


def test_dpapi_roundtrip_if_available():
    if not storage.HAS_DPAPI:
        pytest.skip("DPAPI לא זמין בסביבה זו")
    enc = storage.protect_text("סוד רגיש")
    assert enc.startswith("dpapi:")
    assert storage.is_protected(enc)
    assert storage.unprotect_text(enc) == "סוד רגיש"


def test_unprotect_plaintext_passthrough():
    # ערך legacy לא מוצפן מוחזר כמו שהוא
    assert storage.unprotect_text("plain text") == "plain text"


def test_sqlite_migrations_run_once(tmp_path):
    migrations = [
        "CREATE TABLE t1(id INTEGER);",
        "CREATE TABLE t2(id INTEGER);",
    ]
    p = str(tmp_path / "m.db")
    con = storage.connect_sqlite(p, migrations)
    ver = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert ver == 2
    con.close()
    # פתיחה חוזרת לא מריצה שוב (לא תיכשל על CREATE TABLE כפול)
    con2 = storage.connect_sqlite(p, migrations)
    assert con2.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
    con2.close()


def test_sqlite_health(tmp_path):
    p = str(tmp_path / "h.db")
    storage.connect_sqlite(p, ["CREATE TABLE x(id INTEGER);"]).close()
    ok, _ = storage.sqlite_health(p)
    assert ok
