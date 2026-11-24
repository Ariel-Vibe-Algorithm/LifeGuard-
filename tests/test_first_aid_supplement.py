# -*- coding: utf-8 -*-
"""
חבילת הרחבה 02: תוספת בלבד (המאגר המקורי לא משתנה), מניעת כפילויות, סטטוס בדיקה, נהלי בטיחות,
חיפוש בעברית לכל מזהה, גרסאות / נפילה לגרסה קודמת / rollback, וייבוא שלא דורס.
"""
import hashlib
import json
import os
import shutil

import pytest

from first_aid import library as FA
from first_aid import supplements as SP

PKG = "lifeguard_first_aid_supplement_02"
LIB = FA.load()
BASE = FA.load(supplements=False)
RAW = json.load(open(os.path.join(SP.SUPP_DIR, PKG, "1.2.0", SP.SUPP_JSON), encoding="utf-8"))
SUPP_IDS = [g["id"] for g in RAW["guides"]]


def test_additive_merge_keeps_every_original_guide_unchanged():
    raw_base = json.load(open(FA.GUIDES_PATH, encoding="utf-8"))["guides"]
    for g in raw_base:
        assert LIB.guide(g["id"]) == g                                      # בדיוק כמו במקור
    assert len(LIB.guides) == len(BASE.guides) + 43
    assert {c["id"] for c in LIB.conflicts} == {"frostbite", "heat_exhaustion"}
    assert LIB.guide("frostbite")["medical_review_status"].startswith("REQUIRES_REVIEW")   # המקור נשאר
    assert LIB.packages == [{"package_id": PKG, "version": "1.2.0", "added": 43, "total": 45,
                             "updated": "2026-11-07"}]


@pytest.mark.parametrize("gid", SUPP_IDS)
def test_every_supplement_id_is_loaded_reviewed_and_findable_by_title(gid):
    g = LIB.guide(gid)
    assert g is not None
    if gid in ("frostbite", "heat_exhaustion"):
        assert "package_id" not in g                                         # הגרסה המקורית
        return
    raw = next(x for x in RAW["guides"] if x["id"] == gid)
    assert g["what_to_do"] == raw["actions"] and g["do_not"] == raw["do_not"]   # טקסט כלשונו
    assert g["medical_review_status"] == "pending_medical_review"
    assert g["urgency"] in FA.URGENCIES and g["package_version"] == "1.2.0"
    assert all(len(s) > 1 for s in g["setting"])                             # "בית/רחוב" -> רשימה, לא אותיות
    assert all(LIB.sources[s]["url"] for s in g["source_ids"])
    top = [r["id"] for r in LIB.search(raw["title"])["results"][:3]]
    assert gid in top, (raw["title"], top)


@pytest.mark.parametrize("query,expected", [
    ("עקרב", "scorpion_sting_israel"), ("ריח גז", "gas_leak_home"), ("דליפת גז", "gas_leak_home"),
    ("צבע אדום", "israel_rocket_alert_shelter"), ("אזעקה", "israel_rocket_alert_shelter"),
    ("מקלט", "israel_rocket_alert_shelter"), ("בלע סוללה", "button_battery_ingestion"),
    ("מגנט", "multiple_magnet_ingestion"), ("קרציה", "tick_bite_removal"), ("שיטפון", "flash_flood_wadi"),
    ("זרם סחף", "rip_current"), ("מחבל", "active_attack_safety"), ("שן נפלה", "permanent_tooth_avulsion"),
    ("סוכר גבוה", "diabetic_ketoacidosis"), ("דימום בהריון", "pregnancy_bleeding_emergency"),
    ("כלבת", "animal_bite_rabies_israel"), ("עקרב בחצר", "scorpion_sting_israel"), ("عقرب", "scorpion_sting_israel"),
    ("gas leak", "gas_leak_home"),
])
def test_hebrew_and_multilingual_search_for_new_topics(query, expected):
    ids = [r["id"] for r in LIB.search(query)["results"][:2]]
    assert expected in ids, (query, ids)


def test_original_queries_still_rank_as_before():
    assert LIB.search("תינוק נחנק")["results"][0]["id"] == "choking_infant"
    assert LIB.search("מבוגר לא נושם")["results"][0]["id"] == "cpr_adult"
    assert LIB.search("כוויית קור")["results"][0]["id"] == "frostbite"
    ids = [r["id"] for r in LIB.search("דימום")["results"]]
    assert ids.index("severe_bleeding") < ids.index("nosebleed")


def test_safety_procedures_and_official_dynamic_content():
    for gid in ("gas_leak_home", "home_fire_escape", "active_attack_safety", "israel_rocket_alert_shelter",
                "flash_flood_wadi"):
        g = LIB.guide(gid)
        assert g["kind"] == "safety" and "לא טיפול רפואי" in g["category"] and g["authority_review"]
    assert LIB.guide("scorpion_sting_israel")["kind"] == "medical"
    rocket = LIB.guide("israel_rocket_alert_shelter")
    assert rocket["official_dynamic"]["authority"] == "פיקוד העורף"
    assert "דבש" in " ".join(LIB.guide("button_battery_ingestion")["do_not"])   # "לא כברירת מחדל" נשמר
    assert LIB.emergency_numbers["police"] == "100" and LIB.emergency_numbers["fire_and_rescue"] == "102"


def test_supplement_content_has_no_dosages():
    import re
    for gid in SUPP_IDS:
        g = LIB.guide(gid)
        text = " ".join(g["what_to_do"])
        assert not re.search(r"\d+\s*(מ\"ג|מ״ג|mg|יחידות|units)", text), gid


# ---------------------------------------------------------------- גרסאות, נפילה, rollback, ייבוא
@pytest.fixture
def supp_copy(tmp_path):
    d = tmp_path / "supplements"
    shutil.copytree(SP.SUPP_DIR, d)
    return d


def test_corrupt_active_version_falls_back_to_previous_and_base_survives(supp_copy):
    bad = supp_copy / PKG / "1.2.1"
    bad.mkdir()
    (bad / SP.SUPP_JSON).write_text("{broken", encoding="utf-8")
    SP.write_active({"packages": {PKG: {"active": "1.2.1", "history": ["1.2.0", "1.2.1"]}}},
                    str(supp_copy / "active.json"))
    lib = FA.load(supp_dir=str(supp_copy))
    assert lib.packages[0]["version"] == "1.2.0" and len(lib.guides) == 82
    assert any("1.2.1" in e and "גרסה קודמת" in e for e in lib.errors)
    shutil.rmtree(supp_copy / PKG / "1.2.0")                                  # גם הקודמת חסרה
    lib2 = FA.load(supp_dir=str(supp_copy))
    assert len(lib2.guides) == 39 and lib2.search("חנק")["results"]          # המאגר המקורי עובד
    (supp_copy / "active.json").write_text("garbage", encoding="utf-8")
    assert len(FA.load(supp_dir=str(supp_copy)).guides) == 39


def test_import_is_versioned_never_overwrites_and_rollback(tmp_path, supp_copy):
    from tools import build_first_aid as B
    base_hash = hashlib.sha256(open(FA.GUIDES_PATH, "rb").read()).hexdigest()
    pack = tmp_path / "pack"
    (pack / "content").mkdir(parents=True)
    src = supp_copy / PKG / "1.2.0"
    for f in os.listdir(src):
        dest = pack / ("content/" + f if f.startswith("first_aid_supplement_he") else f)
        shutil.copy(src / f, dest)
    code, rep = B.import_supplement(str(pack), supp_dir=str(supp_copy))      # אותה גרסה, אותו תוכן
    assert code == 0 and any("2 מזהים כבר קיימים" in r for r in rep)
    data = json.load(open(pack / "content" / SP.SUPP_JSON, encoding="utf-8"))
    data["guides"][0]["title"] = "שינוי"
    json.dump(data, open(pack / "content" / SP.SUPP_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    code, rep = B.import_supplement(str(pack), supp_dir=str(supp_copy))
    assert code == 1 and any("כבר קיימת עם תוכן שונה" in r for r in rep)    # לא דורסים גרסה
    man = json.load(open(pack / "manifest.json", encoding="utf-8"))
    man["version"] = "1.3.0"
    json.dump(man, open(pack / "manifest.json", "w", encoding="utf-8"))
    code, rep = B.import_supplement(str(pack), supp_dir=str(supp_copy))
    assert code == 0
    act = SP.read_active(str(supp_copy / "active.json"))["packages"][PKG]
    assert act["active"] == "1.3.0" and act["history"] == ["1.2.0", "1.3.0"]
    assert FA.load(supp_dir=str(supp_copy)).guide(data["guides"][0]["id"])["title"] == "שינוי"
    code, rep = B.rollback(PKG, supp_dir=str(supp_copy))
    assert code == 0 and SP.read_active(str(supp_copy / "active.json"))["packages"][PKG]["active"] == "1.2.0"
    assert FA.load(supp_dir=str(supp_copy)).guide(data["guides"][0]["id"])["title"] != "שינוי"
    data["guides"][1]["actions"] = []                                         # מדריך פגום -> לא מופעל
    man["version"] = "1.4.0"
    json.dump(data, open(pack / "content" / SP.SUPP_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(man, open(pack / "manifest.json", "w", encoding="utf-8"))
    code, rep = B.import_supplement(str(pack), supp_dir=str(supp_copy))
    assert code == 1 and SP.read_active(str(supp_copy / "active.json"))["packages"][PKG]["active"] == "1.2.0"
    assert hashlib.sha256(open(FA.GUIDES_PATH, "rb").read()).hexdigest() == base_hash   # המקורי לא נגע


def test_rocket_guide_screen_shows_official_source_and_safety_label():
    import tkinter as tk
    from tests.test_first_aid import _texts, _tk_root
    root = _tk_root()
    try:
        from first_aid_ui import FirstAidScreen

        class App:
            pass
        app = App()
        app.root = root
        scr = FirstAidScreen(root, app, lib=LIB)
        scr.pack(fill="both", expand=True)
        scr.search("אזעקה")
        scr.open_guide("israel_rocket_alert_shelter")
        root.update()
        flat = "\n".join(_texts(scr.body))
        assert "המקור המחייב: פיקוד העורף" in flat and "נוהל בטיחות - לא טיפול רפואי" in flat
        assert "ממתין לבדיקה" in flat and "1.2.0" in flat
        assert "משטרה 100" in flat and "כבאות 102" in flat
        assert "מיועד ל: לא צוין בחבילה" in flat                                # לא מנחשים גיל
        scr.open_guide("animal_bite_rabies_israel")
        root.update()
        assert any("מדריך קשור" in t for t in _texts(scr.body))
    finally:
        root.destroy()
