# -*- coding: utf-8 -*-
"""
עזרה ראשונה אופליין: אימות תוכן, חיפוש (נרמול, שגיאות כתיב, מילים נרדפות, ערבית/אנגלית), דירוג לפי דחיפות,
גיל (לא מערבבים מבוגר/ילד/תינוק), תוכן פגום, אפס פניות לרשת, ומסך שמתחיל בשורת חיפוש בלי קטלוג.
"""
import json
import os
import shutil
import socket
import time

import pytest

from first_aid import library as FA

LIB = FA.load()
BASE = FA.load(supplements=False)


# ---------------------------------------------------------------- תוכן
def test_bundled_content_is_valid_complete_and_flagged_for_review():
    assert BASE.errors == [] and len(BASE.guides) == 39
    assert LIB.errors == [] and len(LIB.guides) == 39 + 43                     # + חבילת הרחבה 02
    assert LIB.meta["medical_review_required"] is True
    for g in BASE.guides:
        assert g["medical_review_status"].startswith("REQUIRES_REVIEW")       # לא הוסר
        assert g["source_ids"] and all(s in LIB.sources and LIB.sources[s]["url"] for s in g["source_ids"])
        assert g["content_updated"] and g["what_to_do"]
    assert BASE.emergency_numbers == {"mda": "101", "poison_center": "04-7771900"}
    idx = json.load(open(FA.INDEX_PATH, encoding="utf-8"))
    assert {i["id"] for i in idx["items"]} == set(BASE.by_id)


def test_build_tool_check_and_rejects_bad_pack_without_touching_content(tmp_path):
    from tools import build_first_aid as B
    assert B.main(["--check"]) == 0
    pack = tmp_path / "pack"
    (pack / "content").mkdir(parents=True)
    for rel in ("content/first_aid_guides_he.json", "content/first_aid_guides_he.md"):
        shutil.copy(os.path.join(FA.CONTENT_DIR, os.path.basename(rel)), pack / rel)
    for name in ("SOURCES_AND_REVIEW.md", "manifest.json", "README.md"):
        shutil.copy(os.path.join(FA.CONTENT_DIR, name), pack / name)
    data = json.load(open(pack / "content/first_aid_guides_he.json", encoding="utf-8"))
    data["guides"][0]["urgency"] = "whenever"
    del data["guides"][1]["medical_review_status"]
    json.dump(data, open(pack / "content/first_aid_guides_he.json", "w", encoding="utf-8"), ensure_ascii=False)
    before = open(FA.GUIDES_PATH, encoding="utf-8").read()
    assert B.main(["--from", str(pack)]) == 1
    assert open(FA.GUIDES_PATH, encoding="utf-8").read() == before
    assert B.parse_sources(open(os.path.join(FA.CONTENT_DIR, "SOURCES_AND_REVIEW.md"), encoding="utf-8").read())[0][
        "id"] == "AHA_ADULT_2025"


def test_malformed_content_is_skipped_safely(tmp_path):
    good = json.load(open(FA.GUIDES_PATH, encoding="utf-8"))
    bad = dict(good)
    g0 = dict(good["guides"][0])
    bad["guides"] = [g0, dict(g0), {"id": "x"}, "junk", dict(good["guides"][2], urgency="soon"),
                     dict(good["guides"][3], age_groups=["martian"]), good["guides"][4]]
    p = tmp_path / "g.json"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    lib = FA.load(str(p), supplements=False)
    assert {g["id"] for g in lib.guides} == {g0["id"], good["guides"][4]["id"]}
    assert len(lib.errors) == 5 and any("כפול" in e for e in lib.errors)
    p.write_text("{not json", encoding="utf-8")
    broken = FA.load(str(p), supplements=False)
    assert broken.guides == [] and broken.errors
    assert broken.search("חנק")["results"] == [] and broken.emergency_numbers["mda"] == "101"


# ---------------------------------------------------------------- נרמול וחיפוש
def test_hebrew_normalization():
    assert FA.normalize("כְּוִויָּה") == FA.normalize("כוויה") == FA.normalize("כויה")
    assert FA.normalize("מד״א") == FA.normalize('מד"א') == "מדא"
    assert FA.normalize("חום!!") == "חומ"                                   # אות סופית + פיסוק
    assert "ילד" in FA.stems("והילדים") and "נחנק" in FA.stems("נחנקת")


def _top(q, **kw):
    r = LIB.search(q, **kw)["results"]
    return r[0]["id"] if r else None


@pytest.mark.parametrize("query,expected", [
    ("החיאה", ("cpr_adult", "cpr_child_infant")),            # שגיאת כתיב
    ("אסטמה", ("asthma_attack",)),
    ("כויה ממים רותחים", ("burns_thermal",)),
    ("קיבל מכה בראש", ("head_spine_injury",)),               # ביטוי מדובר
    ("בלע כדורים", ("poison_ingestion",)),
    ("יורד לו דם מהאף", ("nosebleed",)),
    ("סוכר נמוך", ("hypoglycemia",)),
    ("צירים", ("birth_emergency",)),
    ("מפרכס", ("seizure",)),
    ("choking", ("choking_adult_child", "choking_infant")),  # אנגלית
    ("نزيف", ("severe_bleeding", "shock_internal_bleeding")),   # ערבית
    ("اختناق", ("choking_adult_child", "choking_infant")),
    ("התחשמל", ("electric_shock",)),
])
def test_search_typos_synonyms_and_languages(query, expected):
    assert _top(query) in expected


def test_urgency_ranking_and_ambiguity():
    r = LIB.search("דימום")
    ids = [x["id"] for x in r["results"]]
    assert ids.index("severe_bleeding") < ids.index("nosebleed")             # מסכן חיים לפני לא דחוף
    assert r["ambiguous"]                                                     # כמה התאמות - המשתמש/ת בוחר/ת
    assert LIB.search("")["results"] == [] and LIB.search("   ")["results"] == []
    assert LIB.search("xyzqq")["results"] == [] and LIB.search("אני עצוב")["results"] == []


def test_age_detection_and_families_never_mix_adult_and_infant():
    assert FA.detect_age("תינוק נחנק") == "infant" and FA.detect_age("בן 4 חודשים לא נושם") == "infant"
    assert FA.detect_age("ילד בן 3 נחנק") == "child" and FA.detect_age("אבא שלי לא נושם") == "adult"
    assert FA.detect_age("נחנק") is None and FA.detect_age("בן 40") is None   # לא מנחשים
    inf = [x["id"] for x in LIB.search("תינוק נחנק")["results"]]
    assert inf[0] == "choking_infant" and "choking_adult_child" not in inf
    adult = [x["id"] for x in LIB.search("נחנק", age="adult")["results"]]
    assert "choking_adult_child" in adult and "choking_infant" not in adult
    assert [x["id"] for x in LIB.search("תינוק לא נושם")["results"]][0] == "cpr_child_infant"
    assert "cpr_child_infant" not in [x["id"] for x in LIB.search("מבוגר לא נושם")["results"]]
    assert LIB.needs_age("choking_adult_child", None) and not LIB.needs_age("choking_adult_child", "child")
    assert LIB.resolve("choking_adult_child", "infant") == "choking_infant"
    assert LIB.resolve("cpr_child_infant", "adult") == "cpr_adult"
    assert "בתינוק" in LIB.age_note("cpr_child_infant", "infant")
    assert "101" in LIB.age_note("severe_bleeding", "infant")                # מדריך שלא נכתב לתינוקות - אומרים
    assert LIB.age_note("severe_bleeding", "adult") is None
    assert _top("חום לתינוק") == "child_fever"


def _tk_root():
    """Tk ב-Windows נכשל לפעמים באתחול ("Can't find a usable tk.tcl") כשיוצרים חלונות ברצף - מנסים שוב."""
    import tkinter as tk
    last = None
    for _ in range(3):
        try:
            return tk.Tk()
        except tk.TclError as exc:
            last = exc
            time.sleep(0.5)
    pytest.skip(f"no display: {last}")


# ---------------------------------------------------------------- אפס רשת
@pytest.fixture
def no_network(monkeypatch):
    import threading
    import traceback
    calls = []
    me = threading.current_thread()

    def blocked(*a, **k):
        # נספר רק מה שמגיע מה-thread של הבדיקה (החיפוש והמסך רצים בו) או מקוד העזרה הראשונה עצמו.
        # threads ברקע שנשארו מבדיקות קודמות (לקוח שמנסה להתחבר לשרת שנסגר) אינם חלק מהבדיקה.
        frames = traceback.extract_stack()[:-1]
        ours = any(os.sep + "first_aid" + os.sep in f.filename or f.filename.endswith("first_aid_ui.py")
                   for f in frames)
        if threading.current_thread() is me or ours:
            calls.append((a, "".join(traceback.format_list(frames[-6:]))))
        raise OSError("network disabled (airplane mode test)")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    return calls


def test_search_and_open_every_guide_make_no_network_request(no_network, monkeypatch):
    import connectivity_module
    connectivity_module._set_state_for_tests(False)                          # מצב טיסה
    lib = FA.load()
    for q in ("חנק", "לא נושם", "دم", "bleeding", "כויה", "x"):
        lib.search(q)
    import tkinter as tk
    root = _tk_root()
    try:
        from first_aid_ui import FirstAidScreen

        class App:
            pass
        app = App()
        app.root = root
        scr = FirstAidScreen(root, app, lib=lib)
        scr.pack(fill="both", expand=True)
        scr.on_show()
        root.update()
        assert "אין אינטרנט" in scr.net_label.cget("text")
        for g in lib.guides:
            for age in (None, "infant"):
                scr.age = age
                scr.open_guide(g["id"])
        scr.search("דימום")
        root.update()
    finally:
        root.destroy()
        connectivity_module._set_state_for_tests(None)
    assert no_network == []


def test_first_aid_modules_do_not_import_network_libraries():
    for path in (os.path.join(os.path.dirname(FA.__file__), "library.py"),):
        src = open(path, encoding="utf-8").read()
        for lib in ("import requests", "import urllib", "import socket", "import http"):
            assert lib not in src


# ---------------------------------------------------------------- מסך
def _texts(widget):
    import tkinter as tk
    out = []

    def walk(w):
        for ch in w.winfo_children():
            if isinstance(ch, (tk.Label, tk.Button)):
                out.append(ch.cget("text"))
            walk(ch)
    walk(widget)
    return out


@pytest.fixture
def screen():
    import tkinter as tk
    root = _tk_root()
    from first_aid_ui import FirstAidScreen

    class App:
        pass
    app = App()
    app.root = root
    scr = FirstAidScreen(root, app)
    scr.pack(fill="both", expand=True)
    root.update()
    yield scr, root
    root.destroy()


def test_landing_is_search_first_without_a_catalog(screen):
    scr, root = screen
    texts = " ".join(_texts(scr.body))
    assert not any(g["title"] in texts for g in LIB.guides)                  # אין רשימת מצבים (גם לא מהתוספת)
    assert scr.hint.winfo_manager() == "place" and "מה קרה" in scr.hint.cget("text")
    assert "101" in texts and ("טיוטת" in texts or "תחליף" in texts)          # הצהרה + 101 תמיד


def test_results_age_prompt_guide_order_and_fallback(screen):
    scr, root = screen
    scr.search("נחנק")
    root.update()
    t = _texts(scr.body)
    assert any("מה גיל" in x for x in t) and not any("טפיחות" in x for x in t)   # שואלים גיל לפני צעדים
    scr._choose_age("infant", "choking_adult_child")
    root.update()
    assert scr.current == "choking_infant"
    t = _texts(scr.body)
    flat = "\n".join(t)
    order = [flat.index(h) for h in ("חנק בתינוק", "101", "מה עושים עכשיו", "מה אסור לעשות",
                                     "מתי חייבים להזעיק עזרה", "סטטוס:")]
    assert order == sorted(order)                                             # הסדר הנדרש
    assert "לחיצות בטן" not in flat.split("מה אסור לעשות")[0]                  # אין טכניקת מבוגר בצעדי התינוק
    assert any("טעונה אישור" in x for x in t) and any("עדכון תוכן: 2026-11-07" in x for x in t)
    assert scr.query_text() == "נחנק"                                          # שורת החיפוש נשארת למעלה
    scr.age = None
    scr.age_var.set("")
    scr.search("xyzqq")
    root.update()
    t = _texts(scr.body)
    assert any("לא מצאנו מדריך מתאים" in x for x in t)
    assert {"חוסר הכרה", "קושי בנשימה", "דימום חמור"} <= set(t)


def test_poison_guides_show_poison_center(screen):
    scr, root = screen
    scr.open_guide("poison_ingestion")
    root.update()
    assert any("04-7771900" in x and "24/7" in x for x in _texts(scr.body))


def test_wrap_text_breaks_long_rtl_lines():
    from first_aid_ui import wrap_text
    text = "מילה " * 60
    lines = wrap_text(text.strip(), 10_000, len).split("\n")
    assert all(len(line) <= 90 for line in lines) and " ".join(lines) == text.strip()
    assert wrap_text("א ב ג", 3, len) == "א ב\nג"
