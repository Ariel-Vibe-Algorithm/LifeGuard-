# -*- coding: utf-8 -*-
"""קטלוג הנתונים (tools/build_data_catalog.py): לא נוגע ב-data/, עמודות צפויות, CSV עם BOM. אופליין, בלי קבצי המאגר."""
import csv
import os

import pytest

from tools import build_data_catalog as C

REQUIRED = ["path", "name_he", "engine", "kind", "source", "licence", "size_mb", "records", "precision",
            "built_at", "used_by", "verdict", "recommendation"]


def test_columns_start_with_required_set():
    assert C.COLUMNS[:len(REQUIRED)] == REQUIRED
    assert len(set(C.COLUMNS)) == len(C.COLUMNS)
    for e in C.ENGINES:
        assert "v_" + e in C.COLUMNS


@pytest.mark.parametrize("p", ["data", "data/", "./data", "data/x.db", "data\\sub\\f.json", "./data/a"])
def test_assert_not_data_refuses(p):
    with pytest.raises(PermissionError):
        C._assert_not_data(p)


@pytest.mark.parametrize("p", ["models/x.gz", "research_data/audio", "research/datasets/data.csv", "server/server_data"])
def test_assert_not_data_allows_other_paths(p):
    assert C._assert_not_data(p)


def test_catalog_entries_are_valid():
    paths = set()
    for e in C.CATALOG:
        for r in e.get("paths") or [e["path"]]:
            C._assert_not_data(r)            # אף נכס תחת data/
        assert e["path"] not in paths, e["path"]
        paths.add(e["path"])
        assert e["verdict"] in C.VERDICTS, e["path"]
        assert e.get("role") in C.ROLES, e["path"]
        assert e.get("kind") in C.KINDS, e["path"]
        assert set(e.get("v") or {}) <= set(C.ENGINES), e["path"]
        assert all(v in C.VERDICTS for v in (e.get("v") or {}).values()), e["path"]
        assert e.get("name_he") and e.get("recommendation") is not None


def _fake_repo(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "secret.json").write_text('{"user": "x"}', encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "grid.csv").write_bytes(b"a,b\n1,2\n3,4\n")
    (tmp_path / "models" / "sub").mkdir()
    (tmp_path / "models" / "sub" / "w.bin").write_bytes(b"\0" * 2048)
    return tmp_path


def test_scan_never_visits_data_dir(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path)
    seen = []
    real_scandir, real_open = os.scandir, open

    def spy_scandir(p="."):
        seen.append(os.path.normpath(str(p)))
        return real_scandir(p)

    def spy_open(p, *a, **k):
        seen.append(os.path.normpath(str(p)))
        return real_open(p, *a, **k)

    monkeypatch.setattr(C.os, "scandir", spy_scandir)
    monkeypatch.setattr("builtins.open", spy_open)
    monkeypatch.setattr(C, "DISCOVERY_ROOTS", [".", "models"])
    catalog = [dict(path="models/grid.csv", name_he="t", engine="route", role="runtime", kind="estimate",
                    verdict="in_use", records="csv", v={"route": "in_use"}, recommendation=""),
               dict(path="models/sub", name_he="t2", engine="acoustic", role="runtime", kind="model_weights",
                    verdict="in_use", records="files", v={}, recommendation="")]
    rows = C.scan_rows(str(root), catalog=catalog)
    data_dir = os.path.normpath(str(root / "data"))
    assert not [p for p in seen if p == data_dir or p.startswith(data_dir + os.sep)]
    assert all(not r["path"].startswith("data") for r in rows)
    by = {r["path"]: r for r in rows}
    assert by["models/grid.csv"]["records"].startswith("2 rows")
    assert by["models/sub"]["files"] == "1" and by["models/sub"]["v_route"] == "not_relevant"


def test_walk_sizes_skips_data_even_from_root(tmp_path):
    root = _fake_repo(tmp_path)
    files, size, _, _ = C.walk_sizes(str(root), str(root))
    assert files == 2                      # grid.csv + sub/w.bin, לא data/secret.json
    assert size == len("a,b\n1,2\n3,4\n") + 2048


def test_catalog_entry_under_data_is_rejected(tmp_path):
    root = _fake_repo(tmp_path)
    bad = [dict(path="data/secret.json", name_he="x", engine="none", role="runtime_state", kind="runtime_state",
                verdict="unclear", v={}, recommendation="")]
    with pytest.raises(PermissionError):
        C.scan_rows(str(root), catalog=bad, with_discovery=False)


def test_missing_asset_and_csv_bom(tmp_path):
    root = _fake_repo(tmp_path)
    catalog = [dict(path="models/nope.json", name_he="x", engine="route", role="runtime", kind="estimate",
                    verdict="stale", v={}, recommendation="")]
    rows = C.scan_rows(str(root), catalog=catalog, with_discovery=False)
    assert rows[0]["records"] == "MISSING"
    out = tmp_path / "out" / "cat.csv"
    C.write_csv(rows, str(out))
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with open(out, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        assert r.fieldnames == C.COLUMNS
        assert next(r)["name_he"] == "x"
