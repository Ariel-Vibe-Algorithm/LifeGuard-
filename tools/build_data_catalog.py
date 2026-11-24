# -*- coding: utf-8 -*-
"""
build_data_catalog.py - קטלוג כל נכסי הנתונים של LifeGuard (research/DATA_CATALOG_HE.md, research/data_catalog.csv).

    python -m tools.build_data_catalog                # סורק מחדש ומעדכן את research/data_catalog.csv
    python -m tools.build_data_catalog --out X.csv    # פלט לנתיב אחר
    python -m tools.build_data_catalog --no-bulk      # בלי מעבר על תיקיות ענק (FSD50K וכו') - מהיר

מה מחושב מחדש בכל ריצה: size_mb, files, records, mtime (ו-built_at כשאין תאריך מתועד).
עמודות השיפוט (verdict, recommendation, licence...) באות מהרשימה CATALOG שבקובץ הזה - לעדכן כאן.

כללים קשיחים:
- תיקיית data/ של המאגר (נתוני משתמשים אמיתיים) לא נפתחת, לא נסרקת ולא נרשמת. _assert_not_data אוכף זאת
  לכל נתיב, והסורק מדלג על כל תיקייה בשם data בשורש המאגר.
- קריאה בלבד. אין מחיקה/שינוי של קובץ נתונים. אין רשת.
- תיקיות גדולות (FSD50K, research_data/audio - ייתכן שסוכן אחר עדיין מוריד אליה) נסרקות ב-os.scandir
  לגודל ומספר קבצים בלבד - בלי לפתוח קבצים.
- records נספר רק לקבצים קטנים (< RECORDS_MAX_BYTES), ורק בסוגים זולים (csv/tsv/json/json.gz/npz).
"""

import argparse
import csv
import gzip
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_CSV = os.path.join(ROOT, "research", "data_catalog.csv")
RECORDS_MAX_BYTES = 30 * 1024 * 1024
BULK_FILES = 2000          # תיקייה עם יותר קבצים מזה נחשבת "bulk" (מדולגת ב---no-bulk)
FORBIDDEN_TOP = "data"     # תיקיית נתוני המשתמשים - לעולם לא

COLUMNS = ["path", "name_he", "engine", "kind", "source", "licence", "size_mb", "records", "precision",
           "built_at", "used_by", "verdict", "recommendation",
           # עמודות הרחבה
           "role", "format", "files", "mtime", "coverage",
           "v_route", "v_safe_places", "v_acoustic", "v_asr", "v_first_aid", "v_news", "v_national",
           "licence_flag", "ship_in_app", "regenerate"]
ENGINES = ("route", "safe_places", "acoustic", "asr", "first_aid", "news", "national")
VERDICTS = ("in_use", "relevant_not_used", "redundant", "stale", "not_relevant", "unclear")
ROLES = ("runtime", "runtime_optional", "build_input", "research_eval", "research_output", "test_fixture",
         "raw_dataset", "runtime_state", "docs")
KINDS = ("official_verified", "open_data_community", "mixed_official_osm", "estimate", "user_report",
         "news_unverified", "model_weights", "test_fixture", "generated_cache", "curated_content",
         "research_audio", "runtime_state")

SKIP_DIRS = {".venv", ".tools", "node_modules", "__pycache__", ".git", ".gradle", "build"}

# ======================================================================================== שיפוט (ידני)
# path: קובץ או תיקייה (יחסית לשורש). paths: כמה נתיבים שמרכיבים נכס אחד (path הוא התווית).
# records: "csv" | "tsv" | "json:<key>[,<key>...]" | "jsongz:<key>[,<key>]" | "npz:<array>" | "files[:.ext]"
#          | "sqlite" | "doc:<טקסט קבוע מהתיעוד>"
# v: פסיקה לכל מנוע שהנכס רלוונטי אליו; מנוע שלא מופיע = not_relevant.

L_OSM = "OSM ODbL 1.0 (share-alike למאגר נגזר)"
L_TLV = "GIS ת\"א - רישיון השרת לא אומת"
NOT_SHIP = "no (gitignored, research)"

CATALOG = [
    # ------------------------------------------------------------------ שכבות ריצה (models/)
    dict(path="models/safety_layers.json.gz",
         name_he="שכבות רשמיות: מוקדי תאונות הולכי רגל (רשת) + מקלטים ציבוריים",
         engine="route", role="runtime", kind="official_verified", format="json.gz",
         source="הלמ\"ס PUF תאונות הולכי רגל 2020-2024; מקלטים: עיריות ת\"א, ב\"ש, חיפה, ירושלים",
         licence="הלמ\"ס 2.1 + data.gov.il Open (מסחרי מותר, ייחוס); ירושלים ODbL; " + L_TLV + " (גם חיפה)",
         licence_flag="unverified (TLV/Haifa GIS); share-alike (Jerusalem ODbL)",
         precision="street (~100 m cell); shelters: point", coverage="תאונות: כל הארץ; מקלטים: 4 ערים",
         built_at="2026-11-07", records="jsongz:accidents.cells,shelters",
         used_by="server/safety_intel.py:Layers.load, SafetyIntelMixin._layers; server/national.py (layers.shelters_near)",
         v=dict(route="in_use", national="in_use", safe_places="relevant_not_used"), verdict="in_use",
         recommendation="לחשב את סף המוקד (Layers._hotspot_threshold) בזמן הבנייה ולשמור בקובץ - כ-40% מזמן הטעינה; "
                        "אינדקס רשת למקלטים במקום סריקה ליניארית של 858 בכל קריאה; לשקול מקלטים כקטגוריה בחיפוש מקום בטוח בזמן התרעה",
         ship_in_app="server only (gitignored - build locally)",
         regenerate="python -m research_tools.build_safety_layers [--offline]"),
    dict(path="models/street_lights_grid.json.gz",
         name_he="רשת עמודי תאורה (~50 מ') - ת\"א ובאר שבע",
         engine="route", role="runtime", kind="official_verified", format="json.gz",
         source="GIS ת\"א שכבה 543; data.gov.il street-light-br7 (באר שבע)",
         licence="ב\"ש: data.gov.il Open; " + L_TLV, licence_flag="unverified (TLV GIS)",
         precision="street (~50 m cell)", coverage="ת\"א-יפו, באר שבע", built_at="2026-11-07",
         records="jsongz:cells,cover",
         used_by="context_risk.py:LightGrid.load/_grid/assess; server/safety_intel.py:_light_grid",
         v=dict(route="in_use"), verdict="in_use",
         recommendation="אותו מקור מופיע גם כשדה lights ב-risk_factors_grid (שאינו נקרא בזמן ריצה) - להשאיר רק כאן; "
                        "לאמת רישיון GIS ת\"א לפני הפצה. עמוד ממופה != עמוד דולק",
         ship_in_app="server + Windows (gitignored - build locally)",
         regenerate="python -m research_tools.israel_layer build (lights) && python -m research_tools.build_light_grid"),
    dict(path="models/risk_factors_grid.json.gz",
         name_he="רשת גורמי סיכון סביבתיים (~100 מ') + הקשר אזור סטטיסטי (פשיעה)",
         engine="route", role="runtime", kind="mixed_official_osm", format="json.gz",
         source="OSM (Overpass); עיריית ת\"א gisn (רישוי עסקים, אזורי בילוי, פארקים); משטרה - פשיעה לפי א\"ס; הלמ\"ס א\"ס 2011",
         licence=L_OSM + "; ת\"א gisn - לא אומת; משטרה/הלמ\"ס data.gov.il Open",
         licence_flag="share-alike (ODbL); unverified (TLV gisn)",
         precision="street (~100 m) + stat-area (crime)", coverage="ת\"א, ירושלים, חיפה, באר שבע",
         built_at="2026-11-08", records="jsongz:cells,stat_areas",
         used_by="server/risk_factors_layer.py:RiskFactors.load/flags_at/area_context; server/safety_intel.py:_risk_factors",
         v=dict(route="in_use"), verdict="in_use",
         recommendation="השכבה הכבדה ביותר בזמן ריצה (2.2MB JSON, ~12MB בזיכרון). רק 5 מ-19 השדות נקראים "
                        "(bar, nightclub, bar_licensed_tlv, night_zone_close, rail_station); 64% מהתאים הם רק עיר+א\"ס. "
                        "לייצא גרסת ריצה מצומצמת: שדות בשימוש + טבלת א\"ס בתאים גסים",
         ship_in_app="server only (gitignored - build locally)",
         regenerate="python -m research_tools.risk_factors build [--offline]"),
    dict(path="models/distress_head.npz",
         name_he="ראש סיווג מצוקה (רגרסיה לוגיסטית מעל YAMNet, lr_ctx_s2 + העשרה)",
         engine="acoustic", role="runtime", kind="model_weights", format="npz",
         source="research_tools/distress_experiments.py --holdout על FSD50K dev (+1,828 קטעי העשרה)",
         licence="נגזר מ-FSD50K (CC0/CC-BY/CC-BY-NC/Sampling+); license_note במודל: non-commercial research use",
         licence_flag="NC-derived; tracked in git",
         precision="n.a.", coverage="n.a.", built_at="2026-11-07", records="npz:w",
         used_by="acoustic_ai_module.py:load (Windows); research_tools/multilingual_eval.py",
         v=dict(acoustic="in_use"), verdict="in_use",
         recommendation="לאמן מחדש על קטעי CC0+CC-BY בלבד (הרישיון לכל קטע ב-FSD50K metadata) לפני הפצה מסחרית; "
                        "לא נארז ב-LifeGuard.spec (וגם YAMNet לא) - ב-EXE הזיהוי האקוסטי נופל לעוצמת רעש",
         ship_in_app="in git; not in LifeGuard.spec",
         regenerate="python -m research_tools.distress_experiments --holdout lr_ctx_s2"),

    # ------------------------------------------------------------------ מודלים (research/models)
    dict(path="research/models/yamnet", name_he="YAMNet (TFLite) - הטמעות קול + מפת מחלקות",
         engine="acoustic", role="runtime", kind="model_weights", format="tflite, csv",
         source="Google YAMNet (Kaggle Models)", licence="Apache-2.0", licence_flag="",
         precision="n.a.", coverage="n.a.", built_at="2026-11-05", records="files",
         used_by="acoustic_ai_module.py:load (yamnet_float_embeddings.tflite); research_tools/yamnet_detector.py",
         v=dict(acoustic="in_use"), verdict="in_use",
         recommendation="להעביר את yamnet_float_embeddings.tflite ל-models/ ולארוז ב-spec; "
                        "yamnet_classification.tflite (4.1MB) לא מוזכר בקוד - לא לארוז",
         ship_in_app="no (gitignored); needed for acoustic AI", regenerate="הורדה ידנית מ-Kaggle (research/acoustic_inventory.md)"),
    dict(path="research/models/distress_lr_fsd50k.npz", name_he="מודל LR מוקדם (benchmark FSD50K)",
         engine="acoustic", role="research_eval", kind="model_weights", format="npz",
         source="research_tools/distress_benchmark.py", licence="נגזר מ-FSD50K (כולל CC-BY-NC)",
         licence_flag="NC-derived", precision="n.a.", coverage="n.a.", built_at="2026-11-06", records="npz:w",
         used_by="research_tools/distress_benchmark.py; research_tools/multilingual_eval.py (השוואה)",
         v=dict(acoustic="redundant"), verdict="redundant",
         recommendation="הוחלף ע\"י models/distress_head.npz; להשאיר רק לשחזור benchmark (14KB)",
         ship_in_app="no", regenerate="python -m research_tools.distress_benchmark --fsd50k research/samples/fsd50k"),
    dict(path="research/models/asr/zipformer2-66m-streaming-he", name_he="מודל תמלול עברית מקומי (Zipformer streaming int8)",
         engine="asr", role="runtime", kind="model_weights", format="onnx",
         source="ivrit-ai/zipformer2-66m-streaming-he (Yakov Kolani)", licence="CC-BY-4.0",
         licence_flag="attribution required", precision="n.a.", coverage="עברית", built_at="2026-11-06", records="files",
         used_by="speech_local.py (MODELS['he']); voice_trigger_module.py; voice_input.py; first_aid_ui.py",
         v=dict(asr="in_use", first_aid="in_use"), verdict="in_use",
         recommendation="לוודא שהייחוס מוצג במסך אודות; מורד ומאומת (sha1) ע\"י speech_local",
         ship_in_app="downloaded on demand (sha1-verified)", regenerate="speech_local מוריד ומאמת"),
    dict(path="research/models/asr/sherpa-onnx-streaming-zipformer-en-2023-06-26", name_he="מודל תמלול אנגלית מקומי",
         engine="asr", role="runtime", kind="model_weights", format="onnx",
         source="csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26", licence="Apache-2.0", licence_flag="",
         precision="n.a.", coverage="אנגלית", built_at="2026-11-06", records="files",
         used_by="speech_local.py (MODELS['en'])", v=dict(asr="in_use"), verdict="in_use",
         recommendation="", ship_in_app="downloaded on demand", regenerate="speech_local מוריד ומאמת"),
    dict(path="research/models/asr/vosk-model-small-streaming-ru", name_he="מודל תמלול רוסית מקומי",
         engine="asr", role="runtime", kind="model_weights", format="onnx",
         source="alphacep/vosk-model-small-streaming-ru 0.54", licence="Apache-2.0", licence_flag="",
         precision="n.a.", coverage="רוסית", built_at="2026-11-06", records="files",
         used_by="speech_local.py (MODELS['ru'])", v=dict(asr="in_use"), verdict="in_use",
         recommendation="", ship_in_app="downloaded on demand", regenerate="speech_local מוריד ומאמת"),
    dict(path="research/models/asr/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17", name_he="מודל אנגלית קטן (נפסל)",
         engine="asr", role="research_eval", kind="model_weights", format="onnx",
         source="csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17", licence="Apache-2.0", licence_flag="",
         precision="n.a.", coverage="אנגלית", built_at="2026-11-06", records="files",
         used_by="אף קוד (speech_local.py: 'נבדק ונפסל')", v=dict(asr="redundant"), verdict="redundant",
         recommendation="מועמד לארכוב/מחיקה (44MB) - הוחלף ע\"י en-2023-06-26", ship_in_app="no", regenerate="-"),
    dict(path="research/models/oww", name_he="openWakeWord: melspectrogram + embedding דיבור (ONNX)",
         engine="asr", role="runtime", kind="model_weights", format="onnx",
         source="openWakeWord (Google speech_embedding בגרסת ONNX)",
         licence="קוד Apache-2.0; קובצי המודל CC BY-NC-SA 4.0 (personal_kws.py, acoustic_inventory.md)",
         licence_flag="NC + share-alike - blocks commercial release", precision="n.a.", coverage="n.a.",
         built_at="2026-11-06", records="files", used_by="personal_kws.py (OWW_DIRS, מילות מפתח אישיות)",
         v=dict(asr="in_use"), verdict="in_use",
         recommendation="להחליף במחלץ תכונות ברישיון מתירני (למשל הטמעות YAMNet שכבר קיימות, Apache-2.0) לפני שחרור מסחרי",
         ship_in_app="no (gitignored) - must not ship commercially", regenerate="הורדה ידנית (openWakeWord releases)"),
    dict(path="research/models/clip", name_he="CLIP ViT-B/32 מכומת (ONNX) - ניתוח תמונות רחוב",
         engine="route", role="research_eval", kind="model_weights", format="onnx, json",
         source="openai/clip-vit-base-patch32 (Xenova ONNX quantized)", licence="MIT", licence_flag="",
         precision="n.a.", coverage="n.a.", built_at="2026-11-06", records="files",
         used_by="research_tools/street_vision.py (מחקר בלבד)", v=dict(route="relevant_not_used"),
         verdict="relevant_not_used",
         recommendation="156MB, לא בשום מנוע ריצה. אם שכבת תמונות רחוב תיכנס - רק בשרת, לא באפליקציה",
         ship_in_app="no", regenerate="הורדה ידנית (research/acoustic_inventory.md / street_vision.py)"),
    dict(path="research/models/yunet", name_he="YuNet - זיהוי פנים לטשטוש (פרטיות בתמונות רחוב)",
         engine="route", role="research_eval", kind="model_weights", format="onnx",
         source="OpenCV Zoo face_detection_yunet_2023mar", licence="MIT", licence_flag="",
         precision="n.a.", coverage="n.a.", built_at="2026-11-06", records="files",
         used_by="research_tools/street_vision.py", v=dict(route="relevant_not_used"), verdict="relevant_not_used",
         recommendation="נדרש רק אם street_vision יופעל", ship_in_app="no", regenerate="הורדה ידנית"),
    dict(path="research/models/testclips", name_he="3 קטעי ESC-50 + esc50.csv לבדיקות YAMNet",
         engine="acoustic", role="test_fixture", kind="test_fixture", format="wav, csv",
         source="ESC-50 (karolpiczak)", licence="CC BY-NC 3.0", licence_flag="NC (tests only)",
         precision="n.a.", coverage="n.a.", built_at="2026-11-05", records="files:.wav",
         used_by="tests/test_yamnet_detector.py", v=dict(acoustic="in_use"), verdict="in_use",
         recommendation="בדיקות בלבד - לא לארוז", ship_in_app="no", regenerate="הורדה ידנית מ-ESC-50"),

    # ------------------------------------------------------------------ טבלאות מחקר במאגר (research/datasets)
    dict(path="research/datasets/crime_by_stat_area_summary.csv", name_he="סיכום פשיעה לפי אזור סטטיסטי (2024-2025)",
         engine="route", role="research_output", kind="official_verified", format="csv",
         source="משטרת ישראל, נתוני פשיעה (data.gov.il) - research_tools/israel_layer.py",
         licence="data.gov.il Open; אסור להצליב לפגיעה בפרטיות", licence_flag="",
         precision="stat-area", coverage="כל הארץ (א\"ס)", built_at="2026-11-06", records="csv",
         used_by="research_tools/israel_layer.py (כותב); אף מנוע לא קורא",
         v=dict(route="redundant"), verdict="redundant",
         recommendation="זהה בייט-לבייט ל-research/samples/israel/derived/crime_by_stat_area_summary.csv; "
                        "אות הפשיעה בזמן ריצה מגיע מ-risk_factors_grid (stat_areas). להשאיר עותק אחד",
         ship_in_app="no (tracked in git)", regenerate="python -m research_tools.israel_layer build"),
    dict(path="research/datasets/neighborhood_crime_signal.csv", name_he="טבלת אות פשיעה שכונתי שסופקה ע\"י המשתמש (19 שורות)",
         engine="route", role="research_eval", kind="user_report", format="csv",
         source="טבלה ידנית של המשתמש (israel_layer_report.md §3)", licence="unknown", licence_flag="unknown licence",
         precision="neighbourhood (estimated boundary)", coverage="ערים נבחרות", built_at="2026-11-05", records="csv",
         used_by="research_tools/israel_layer.py:crosscheck_user_table", v=dict(route="not_relevant"),
         verdict="not_relevant",
         recommendation="לא להזין למנוע - הדו\"ח (§3.2) מצא שהיחס בטבלה אינו יחס לחציון העיר; משמש רק להצלבה",
         ship_in_app="no (tracked in git)", regenerate="-"),

    # ------------------------------------------------------------------ research/samples/israel
    dict(path="research/samples/israel/cache", name_he="מטמון API גולמי: משטרה, למ\"ס, GIS ת\"א/חיפה, תאורה ומקלטים ב\"ש",
         engine="route", role="build_input", kind="generated_cache", format="json.gz",
         source="data.gov.il CKAN, CBS, GIS ת\"א/חיפה", licence="לפי המקור (data.gov.il Open / CBS 2.1 / GIS לא אומת)",
         licence_flag="unverified (TLV/Haifa GIS)", precision="mixed", coverage="כל הארץ + 4 ערים",
         built_at="2026-11-06", records="files",
         used_by="research_tools/israel_layer.py --offline; research_tools/build_safety_layers.py --offline",
         v=dict(route="in_use", national="in_use"), verdict="in_use",
         recommendation="קלט לבנייה אופליין בלבד; ~20 עמודי police_crime של 1.48MB - לשמור עד הבנייה הבאה",
         ship_in_app=NOT_SHIP, regenerate="python -m research_tools.israel_layer build (רשת)"),
    dict(path="research/samples/israel/derived/pedestrian_accidents_2020-2024.geojson",
         paths=["research/samples/israel/derived/pedestrian_accidents_2020-2024.geojson",
                "research/samples/israel/derived/pedestrian_accidents_2020-2024.csv"],
         name_he="תאונות הולכי רגל עם קואורדינטות 2020-2024 (geojson + csv)",
         engine="route", role="build_input", kind="official_verified", format="geojson, csv",
         source="הלמ\"ס PUF תאונות דרכים", licence="הלמ\"ס 2.1 / data.gov.il Open", licence_flag="",
         precision="point (accident)", coverage="כל הארץ", built_at="2026-11-06", records="csv:2",
         used_by="research_tools/build_safety_layers.py (ACCIDENTS = ה-geojson)",
         v=dict(route="in_use"), verdict="in_use",
         recommendation="ה-csv (1.5MB) הוא כפילות של ה-geojson (4.5MB) - הבנייה קוראת רק את ה-geojson",
         ship_in_app=NOT_SHIP, regenerate="python -m research_tools.israel_layer build"),
    dict(path="research/samples/israel/derived/streetlights_tel_aviv.csv",
         paths=["research/samples/israel/derived/streetlights_tel_aviv.csv",
                "research/samples/israel/derived/streetlights_beer_sheva.csv"],
         name_he="נקודות עמודי תאורה ת\"א + באר שבע (csv)", engine="route", role="build_input",
         kind="official_verified", format="csv", source="GIS ת\"א 543; data.gov.il street-light-br7",
         licence="ב\"ש Open; " + L_TLV, licence_flag="unverified (TLV GIS)", precision="point (pole)",
         coverage="ת\"א, באר שבע", built_at="2026-11-06", records="csv:all",
         used_by="research_tools/build_light_grid.py; research_tools/lighting_validation.py",
         v=dict(route="in_use"), verdict="in_use", recommendation="",
         ship_in_app=NOT_SHIP, regenerate="python -m research_tools.israel_layer build"),
    dict(path="research/samples/israel/derived/crime_units_all_2024-2025.csv",
         paths=["research/samples/israel/derived/crime_units_all_2024-2025.csv",
                "research/samples/israel/derived/crime_city_summary_2024-2025.csv",
                "research/samples/israel/derived/crime_by_stat_area_summary.csv",
                "research/samples/israel/derived/crosscheck_user_table_2024-2025.csv",
                "research/samples/israel/derived/build_summary_2024-2025.json"],
         name_he="תוצרי פשיעה (יחידות, ערים, א\"ס, הצלבה, סיכום בנייה)", engine="route", role="research_output",
         kind="official_verified", format="csv, json", source="משטרת ישראל (data.gov.il)",
         licence="data.gov.il Open", licence_flag="", precision="stat-area / city", coverage="כל הארץ",
         built_at="2026-11-06", records="csv:1",
         used_by="research_tools/israel_layer.py (כותב); research/israel_layer_report.md",
         v=dict(route="relevant_not_used"), verdict="relevant_not_used",
         recommendation="crime_by_stat_area_summary.csv כאן כפול ל-research/datasets; השאר - ראיות לדו\"ח",
         ship_in_app=NOT_SHIP, regenerate="python -m research_tools.israel_layer build"),
    dict(path="research/samples/israel/(probes)",
         paths=["research/samples/israel/beer_sheva_data_gov_il", "research/samples/israel/cbs_census2022_statistical_areas",
                "research/samples/israel/cbs_road_accidents_puf", "research/samples/israel/haifa_gis",
                "research/samples/israel/jerusalem_datacity", "research/samples/israel/osm_overpass",
                "research/samples/israel/police_crime_data_gov_il", "research/samples/israel/police_receptions",
                "research/samples/israel/tel_aviv_gis"],
         name_he="דגימות בדיקת API (first5/count) לתיעוד data_inventory_israel.md", engine="route",
         role="docs", kind="official_verified", format="txt, json", source="data.gov.il, GIS עירוני, Overpass",
         licence="לפי המקור", licence_flag="", precision="n.a.", coverage="n.a.", built_at="2026-11-05",
         records="files", used_by="אף קוד (ראיות לתיעוד)", v=dict(route="not_relevant"), verdict="not_relevant",
         recommendation="ראיות בלבד (קטן מאוד) - אפשר לארכב יחד עם הדו\"ח", ship_in_app=NOT_SHIP, regenerate="-"),

    # ------------------------------------------------------------------ research/samples/risk_factors
    dict(path="research/samples/risk_factors/cache", name_he="מטמון OSM/עירוני/משטרה לגורמי סיכון (4 ערים)",
         engine="route", role="build_input", kind="generated_cache", format="json.gz",
         source="Overpass (OSM), GIS ת\"א gisn, משטרה, הלמ\"ס", licence=L_OSM + "; ת\"א gisn לא אומת",
         licence_flag="share-alike (ODbL); unverified (TLV gisn)", precision="point / stat-area",
         coverage="ת\"א, ירושלים, חיפה, באר שבע", built_at="2026-11-07", records="files",
         used_by="research_tools/risk_factors.py build --offline; tools/build_safe_places.py (משטרה/בתי חולים מ-OSM)",
         v=dict(route="in_use", safe_places="in_use"), verdict="in_use",
         recommendation="קלט לבנייה אופליין של שתי שכבות ריצה - לא למחוק", ship_in_app=NOT_SHIP,
         regenerate="python -m research_tools.risk_factors build (רשת)"),
    dict(path="research/samples/risk_factors/(outputs)",
         paths=["research/samples/risk_factors/stat_areas_2011.geojson",
                "research/samples/risk_factors/stat_areas_2011_factors.csv",
                "research/samples/risk_factors/_validation_input.csv",
                "research/samples/risk_factors/validation_results.json",
                "research/samples/risk_factors/build_summary.json"],
         name_he="תוצרי אימות גורמי סיכון (פוליגוני א\"ס 2011, גורמים לכל א\"ס, תוצאות רגרסיה)",
         engine="route", role="research_output", kind="mixed_official_osm", format="geojson, csv, json",
         source="research_tools/risk_factors.py", licence="הלמ\"ס + " + L_OSM, licence_flag="share-alike (ODbL)",
         precision="stat-area", coverage="4 ערים", built_at="2026-11-08", records="csv:2",
         used_by="research_tools/risk_factors.py validate; research/risk_factors_report.md",
         v=dict(route="relevant_not_used"), verdict="relevant_not_used",
         recommendation="ראיות למשקלות במנוע (risk_factors_report.md) - לשמור", ship_in_app=NOT_SHIP,
         regenerate="python -m research_tools.risk_factors build && python -m research_tools.risk_factors validate"),

    # ------------------------------------------------------------------ research/samples/news_intel
    dict(path="research/samples/news_intel/news_intel_2026-11-08T1157.json",
         paths=["research/samples/news_intel/news_intel_2026-11-08T1034.json",
                "research/samples/news_intel/news_intel_2026-11-08T1043.json",
                "research/samples/news_intel/news_intel_2026-11-08T1154.json",
                "research/samples/news_intel/news_intel_2026-11-08T1157.json"],
         name_he="צילומי שכבת חדשות (4 ריצות, 08/11) - קלט ל-LIFEGUARD_NEWS_FILE",
         engine="news", role="runtime_optional", kind="news_unverified", format="json",
         source="RSS מפרסמים ישראליים, GDELT, דוברות משטרה; גאוקוד gazetteer/Nominatim",
         licence="זכויות יוצרים של המפרסמים; נדרש אישור בכתב לפני ייצור (news_intel_report.md)",
         licence_flag="copyright - needs publisher permission", precision="city / street (geocoded, variable)",
         coverage="ישראל", built_at="2026-11-08", records="json:items",
         used_by="server/news_layer.py:NewsLayer.from_env (רק אם LIFEGUARD_NEWS_FILE מוגדר); server/safety_intel.py:_news",
         v=dict(news="stale", route="relevant_not_used"), verdict="stale",
         recommendation="ידיעות דועכות תוך ימים - הצילום מ-08/11 כבר לא רלוונטי; 3 מ-4 הצילומים מיותרים. "
                        "לייצר מחדש בתזמון (cron) רק אחרי אישור מפרסמים",
         ship_in_app="no", regenerate="python -m research_tools.news_intel run [--nominatim]"),
    dict(path="research/samples/news_intel/gazetteer.json.gz",
         paths=["research/samples/news_intel/gazetteer.json.gz", "research/samples/news_intel/store.json.gz"],
         name_he="גזטיר מקומות (OSM) + מאגר פריטים מצטבר של news_intel",
         engine="news", role="build_input", kind="generated_cache", format="json.gz",
         source="OSM/Overpass + Nominatim; פריטי RSS/GDELT", licence=L_OSM + "; פריטים: זכויות מפרסמים",
         licence_flag="share-alike (ODbL); copyright (items)", precision="city / street", coverage="ישראל",
         built_at="2026-11-08", records="files", used_by="research_tools/news_intel.py (GAZETTEER_PATH, STORE_PATH)",
         v=dict(news="in_use"), verdict="in_use", recommendation="", ship_in_app=NOT_SHIP,
         regenerate="python -m research_tools.news_intel gazetteer"),
    dict(path="research/samples/news_intel/labels_dev_2026-11-08.json",
         paths=["research/samples/news_intel/labels_dev_2026-11-08.json",
                "research/samples/news_intel/labels_heldout_2026-11-08.json"],
         name_he="תיוג ידני לאימות news_intel (dev + held-out)", engine="news", role="research_eval",
         kind="estimate", format="json", source="תיוג ידני", licence="פנימי", licence_flag="",
         precision="n.a.", coverage="n.a.", built_at="2026-11-08", records="files",
         used_by="python -m research_tools.news_intel eval --labels", v=dict(news="in_use"), verdict="in_use",
         recommendation="", ship_in_app=NOT_SHIP, regenerate="python -m research_tools.news_intel label-template"),
    dict(path="research/samples/news_intel/cache", name_he="מטמון HTTP של news_intel (RSS, GDELT, robots, Overpass, Nominatim)",
         engine="news", role="build_input", kind="generated_cache", format="json.gz",
         source="RSS/GDELT/robots.txt/Overpass/Nominatim", licence="לפי המקור", licence_flag="copyright (RSS)",
         precision="n.a.", coverage="ישראל", built_at="2026-11-08", records="files",
         used_by="research_tools/news_intel.py (מטמון)", v=dict(news="stale"), verdict="stale",
         recommendation="מטמון חדשות ישן - ניתן לנקות לפני הריצה הבאה", ship_in_app=NOT_SHIP,
         regenerate="נבנה מחדש אוטומטית בריצת news_intel run"),

    # ------------------------------------------------------------------ אודיו - FSD50K
    dict(path="research/samples/fsd50k/raw", name_he="FSD50K גולמי (zip מפוצל dev+eval)",
         engine="acoustic", role="raw_dataset", kind="research_audio", format="zip",
         source="Zenodo 4060432 (FSD50K)", licence="CC-BY (דאטהסט); לכל קטע: CC0 / CC-BY / CC-BY-NC / Sampling+",
         licence_flag="NC + Sampling+ clips", precision="n.a.", coverage="n.a.", built_at="2026-11-06",
         records="doc:51,197 clips (dev 40,966 + eval 10,231)", used_by="research_tools/distress_benchmark.py (חילוץ חד-פעמי)",
         v=dict(acoustic="redundant"), verdict="redundant",
         recommendation="24.7GB - התכונות כבר חולצו (*_features.npz). לארכב לכונן חיצוני (ניתן להורדה מחדש מ-Zenodo, dev_md5.txt לאימות)",
         ship_in_app="no", regenerate="הורדה מ-Zenodo 4060432"),
    dict(path="research/samples/fsd50k/(features)",
         paths=["research/samples/fsd50k/dev_features.npz", "research/samples/fsd50k/eval_features.npz",
                "research/samples/fsd50k/dev_aug_features.npz", "research/samples/fsd50k/dev_valnoisy_features.npz",
                "research/samples/fsd50k/eval_noisy_features.npz"],
         name_he="תכונות YAMNet מאוחדות (dev/eval, נקי+רועש+העשרה)", engine="acoustic", role="build_input",
         kind="generated_cache", format="npz", source="research_tools/distress_experiments.py:load_features",
         licence="נגזר מ-FSD50K (כולל NC)", licence_flag="NC-derived", precision="n.a.", coverage="n.a.",
         built_at="2026-11-06", records="files", used_by="research_tools/distress_experiments.py (אימון distress_head)",
         v=dict(acoustic="in_use"), verdict="in_use",
         recommendation="זה מה שנחוץ לאימון מחדש - לשמור (2.3GB)", ship_in_app="no",
         regenerate="python -m research_tools.distress_experiments (בונה מה-cache/raw)"),
    dict(path="research/samples/fsd50k/cache", name_he="מטמון תכונות YAMNet לכל קטע (67,400 קבצי npz)",
         engine="acoustic", role="build_input", kind="generated_cache", format="npz",
         source="research_tools/yamnet_detector.py:FeatureCache", licence="נגזר מ-FSD50K (כולל NC)",
         licence_flag="NC-derived", precision="n.a.", coverage="n.a.", built_at="2026-11-06", records="files",
         used_by="research_tools/distress_benchmark.py:extract_features (רק כשאין *_features.npz)",
         v=dict(acoustic="redundant"), verdict="redundant",
         recommendation="2.3GB ב-67K קבצים - כפילות של *_features.npz (load_features קורא את הקובץ המאוחד קודם). "
                        "לארכב/למחוק אחרי אישור; חוסך גם זמן סריקה/גיבוי",
         ship_in_app="no", regenerate="python -m research_tools.distress_benchmark"),
    dict(path="research/samples/fsd50k/meta", name_he="FSD50K ground truth + metadata (תוויות ורישיון לכל קטע)",
         engine="acoustic", role="build_input", kind="research_audio", format="csv, json",
         source="FSD50K.ground_truth + FSD50K.metadata", licence="CC-BY", licence_flag="",
         precision="n.a.", coverage="n.a.", built_at="2020-09-24", records="files",
         used_by="research_tools/distress_benchmark.py:load_index", v=dict(acoustic="in_use"), verdict="in_use",
         recommendation="נחוץ לסינון קטעי NC באימון מחדש מותר-מסחרית - לשמור", ship_in_app="no",
         regenerate="חילוץ FSD50K.metadata.zip / ground_truth.zip"),
    dict(path="research/samples/fsd50k/(logs+results)",
         paths=["research/samples/fsd50k/results.json", "research/samples/fsd50k/run.log",
                "research/samples/fsd50k/holdout.log", "research/samples/fsd50k/holdout2.log",
                "research/samples/fsd50k/experiments.log", "research/samples/fsd50k/dev_extract.log",
                "research/samples/fsd50k/holdout_v1_clean_calibration.md", "research/samples/fsd50k/dev_md5.txt"],
         name_he="יומני ותוצאות benchmark/holdout של FSD50K", engine="acoustic", role="research_output",
         kind="generated_cache", format="json, log, md", source="distress_benchmark / distress_experiments",
         licence="פנימי", licence_flag="", precision="n.a.", coverage="n.a.", built_at="2026-11-07", records="files",
         used_by="אף קוד (ראיות ל-acoustic_benchmark_fsd50k.md, acoustic_model_holdout.md)",
         v=dict(acoustic="not_relevant"), verdict="not_relevant", recommendation="ראיות - קטן, לשמור",
         ship_in_app="no", regenerate="python -m research_tools.distress_benchmark / distress_experiments --holdout"),

    # ------------------------------------------------------------------ אודיו - דיבור / ASR
    dict(path="research/samples/fleurs_he/dev.tar.gz", name_he="FLEURS עברית dev - ארכיון מקורי",
         engine="asr", role="raw_dataset", kind="research_audio", format="tar.gz",
         source="google/fleurs he_il dev (Hugging Face)", licence="CC-BY-4.0", licence_flag="attribution required",
         precision="n.a.", coverage="עברית", built_at="2026-11-06", records="doc:328 clips",
         used_by="research_tools/local_asr_eval.py (חולץ ל-dev/)", v=dict(asr="redundant"), verdict="redundant",
         recommendation="כפילות של dev/*.wav שחולצו ממנו (154MB) - להשאיר אחד מהשניים",
         ship_in_app="no", regenerate="הורדה מ-HF google/fleurs"),
    dict(path="research/samples/fleurs_he/dev",
         paths=["research/samples/fleurs_he/dev", "research/samples/fleurs_he/dev.tsv",
                "research/samples/fleurs_he/test.tsv", "research/samples/fleurs_he/asr_eval_results.json"],
         name_he="FLEURS עברית dev - קטעי wav + תמלילים + תוצאות הערכת ASR", engine="asr", role="research_eval",
         kind="research_audio", format="wav, tsv, json", source="google/fleurs he_il", licence="CC-BY-4.0",
         licence_flag="attribution required", precision="n.a.", coverage="עברית", built_at="2026-11-06",
         records="files:.wav", used_by="research_tools/local_asr_eval.py",
         v=dict(asr="in_use", acoustic="relevant_not_used"), verdict="in_use",
         recommendation="test.tsv - אין לו אודיו כאן (האודיו של test נמצא ב-research_data/audio/fleurs/he_il)",
         ship_in_app="no", regenerate="python -m research_tools.local_asr_eval"),
    dict(path="research/samples/asr_he", name_he="קטע FLEURS יחיד (\"לפי המשטרה...\") לבדיקת ASR",
         engine="asr", role="test_fixture", kind="test_fixture", format="wav", source="google/fleurs he_il dev",
         licence="CC-BY-4.0", licence_flag="attribution required", precision="n.a.", coverage="עברית",
         built_at="2026-11-06", records="files:.wav", used_by="tests/test_speech_local.py",
         v=dict(asr="in_use"), verdict="in_use", recommendation="",
         ship_in_app="no", regenerate="python -m research_tools.local_asr_eval --export-test-clip"),
    dict(path="research/samples/asr_tts", name_he="30 משפטי מצוקה מסונתזים (Windows TTS) he/en",
         engine="asr", role="research_eval", kind="research_audio", format="wav, tsv",
         source="Windows OneCore TTS (he-IL Asaf, en-US) - research_tools/local_asr_eval.py", licence="unknown (פלט TTS של Microsoft)",
         licence_flag="unknown licence", precision="n.a.", coverage="עברית, אנגלית", built_at="2026-11-06",
         records="files:.wav", used_by="research_tools/local_asr_eval.py", v=dict(asr="in_use"), verdict="in_use",
         recommendation="דיבור סינתטי - לא מייצג קול אמיתי במצוקה; לא להפיץ", ship_in_app="no",
         regenerate="python -m research_tools.local_asr_eval"),
    dict(path="research/samples/asr_sanity", name_he="קטעי en/ru לבדיקת שפיות ASR",
         engine="asr", role="research_eval", kind="research_audio", format="wav, txt", source="לא מתועד",
         licence="unknown", licence_flag="unknown licence", precision="n.a.", coverage="אנגלית, רוסית",
         built_at="2026-11-06", records="files:.wav", used_by="אף קוד במאגר",
         v=dict(asr="unclear"), verdict="unclear",
         recommendation="לא מוזכר בקוד ומקור/רישיון לא מתועדים - לתעד או לארכב", ship_in_app="no", regenerate="-"),
    dict(path="research/samples/street_sanity", name_he="10 תמונות רחוב (Wikimedia) לבדיקת street_vision",
         engine="route", role="test_fixture", kind="test_fixture", format="jpg, json",
         source="Wikimedia Commons (sources.json)", licence="CC BY-SA 4.0 / CC BY 3.0 לפי תמונה",
         licence_flag="share-alike (some images)", precision="n.a.", coverage="n.a.", built_at="2026-11-06",
         records="files:.jpg", used_by="tests/test_street_vision.py", v=dict(route="in_use"), verdict="in_use",
         recommendation="", ship_in_app="no", regenerate="ראו sources.json"),

    # ------------------------------------------------------------------ research_data/audio (בהורדה ע\"י סוכן אחר)
    dict(path="research_data/audio/fleurs", name_he="FLEURS test - he/ar/ru/am/en (שליליים: דיבור רגוע) - בהורדה",
         engine="acoustic", role="research_eval", kind="research_audio", format="tar.gz, tsv",
         source="google/fleurs @70bb2e8 (MANIFEST.json)", licence="CC-BY-4.0", licence_flag="attribution required",
         precision="n.a.", coverage="5 שפות", built_at="2026-11-11", records="files",
         used_by="research_tools/multilingual_eval.py (לא במאגר עדיין)", v=dict(acoustic="in_use", asr="in_use"),
         verdict="in_use", recommendation="בהורדה פעילה (סוכן אחר) - הגודל ישתנה; בסיום לשקול חילוץ חד-פעמי לתכונות ולארכב את ה-tar",
         ship_in_app="no", regenerate="research_tools/multilingual_eval.py (MANIFEST.json)"),
    dict(path="research_data/audio/vivae", name_he="VIVAE - קולות רגש לא-מילוליים (פחד/כאב/כעס) - חיוביים",
         engine="acoustic", role="research_eval", kind="research_audio", format="zip", source="Zenodo 4066235",
         licence="CC-BY-NC-4.0", licence_flag="NC", precision="n.a.", coverage="n.a.", built_at="2026-11-11",
         records="doc:1,085 files", used_by="research_tools/multilingual_eval.py", v=dict(acoustic="in_use"),
         verdict="in_use", recommendation="הערכה בלבד - לא לאמן מודל מסחרי עליו", ship_in_app="no",
         regenerate="MANIFEST.json"),
    dict(path="research_data/audio/cremad", name_he="CREMA-D - דיבור רגשי באנגלית (91 שחקנים)",
         engine="acoustic", role="research_eval", kind="research_audio", format="tar.gz",
         source="HF myleslinder/crema-d (מקור: CheyneyComputerScience)", licence="ODbL + DbCL",
         licence_flag="share-alike (ODbL)", precision="n.a.", coverage="אנגלית", built_at="2026-11-11",
         records="doc:7,442 clips", used_by="research_tools/multilingual_eval.py", v=dict(acoustic="in_use"),
         verdict="in_use", recommendation="", ship_in_app="no", regenerate="MANIFEST.json"),
    dict(path="research_data/audio/resd", name_he="RESD - דיאלוגים רגשיים ברוסית (test)",
         engine="acoustic", role="research_eval", kind="research_audio", format="parquet", source="HF Aniemore/resd",
         licence="MIT", licence_flag="", precision="n.a.", coverage="רוסית", built_at="2026-11-11",
         records="doc:280 clips", used_by="research_tools/multilingual_eval.py", v=dict(acoustic="in_use"),
         verdict="in_use", recommendation="", ship_in_app="no", regenerate="MANIFEST.json"),
    dict(path="research_data/audio/arabic_emotional_demo", name_he="DataHive - דיבור רגשי בערבית (דמו, שעה)",
         engine="acoustic", role="research_eval", kind="research_audio", format="parquet",
         source="HF datahiveai/arabic-multidialect-emotional-speech-demo", licence="CC-BY-NC-4.0",
         licence_flag="NC", precision="n.a.", coverage="ערבית (4 ניבים)", built_at="2026-11-11",
         records="doc:496 utterances", used_by="research_tools/multilingual_eval.py", v=dict(acoustic="in_use"),
         verdict="in_use", recommendation="הערכה בלבד; רישוי מסחרי רק מול הספק", ship_in_app="no",
         regenerate="MANIFEST.json"),
    dict(path="research_data/audio/cache", name_he="מטמון תכונות YAMNet לקטעי ההערכה הרב-לשונית",
         engine="acoustic", role="build_input", kind="generated_cache", format="npz",
         source="research_tools/yamnet_detector.py:FeatureCache", licence="נגזר (לפי הדאטהסטים)",
         licence_flag="NC-derived (partly)", precision="n.a.", coverage="n.a.", built_at="2026-11-11", records="files",
         used_by="research_tools/multilingual_eval.py features", v=dict(acoustic="in_use"), verdict="in_use",
         recommendation="בבנייה פעילה", ship_in_app="no", regenerate="python -m research_tools.multilingual_eval features"),
    dict(path="research_data/audio/scores",
         paths=["research_data/audio/scores", "research_data/audio/MANIFEST.json"],
         name_he="ציונים, תמלולים ותוצאות ההערכה הרב-לשונית + MANIFEST", engine="acoustic", role="research_output",
         kind="generated_cache", format="json, jsonl, md", source="research_tools/multilingual_eval.py",
         licence="פנימי", licence_flag="", precision="n.a.", coverage="n.a.", built_at="2026-11-11", records="files",
         used_by="research_tools/multilingual_eval.py report", v=dict(acoustic="in_use", asr="in_use"),
         verdict="in_use", recommendation="בבנייה פעילה", ship_in_app="no",
         regenerate="python -m research_tools.multilingual_eval asr/report"),

    # ------------------------------------------------------------------ מקומות בטוחים
    dict(path="safe_places/safe_places_il.json", name_he="רשימה ארצית מובנית: תחנות משטרה, כיבוי ובתי חולים עם מיון",
         engine="safe_places", role="runtime", kind="mixed_official_osm", format="json",
         source="data.gov.il תחנות כיבוי 2026 (רשמי); OSM משטרה/בתי חולים; מנחתי בתי חולים data.gov.il",
         licence="data.gov.il Open; " + L_OSM, licence_flag="share-alike (ODbL)",
         precision="point", coverage="כל הארץ (159 משטרה, 130 כיבוי, 33 בתי חולים)", built_at="2026-11-08",
         records="json:places", used_by="map_module.py:_bundled_places/_choose; android SafePlaces.kt (assets); LifeGuard.spec",
         v=dict(safe_places="in_use"), verdict="in_use",
         recommendation="בתי חולים: 33 בלבד ומ-OSM - לשקול רשימת משרד הבריאות; לציין ייחוס ODbL בממשק",
         ship_in_app="yes (Windows EXE + Android assets)", regenerate="python -m tools.build_safe_places [--no-overpass]"),

    # ------------------------------------------------------------------ עזרה ראשונה והנחיות
    dict(path="first_aid/content/first_aid_guides_he.json", name_he="39 מדריכי עזרה ראשונה בעברית (אופליין)",
         engine="first_aid", role="runtime", kind="curated_content", format="json",
         source="תוכן מקורי מסוכם מ-AHA 2025, ERC, מד\"א ועוד (sources.json)", licence="תוכן הפרויקט; מקורות מצוטטים",
         licence_flag="medical review pending", precision="n.a.", coverage="ישראל (he-IL)", built_at="2026-11-07",
         records="json:guides", used_by="first_aid/library.py:load; android FirstAidLibrary.kt; LifeGuard.spec",
         v=dict(first_aid="in_use"), verdict="in_use",
         recommendation="medical_review_required=true - חובה סקירה רפואית לפני שחרור. החיפוש (Library.search) כבד "
                        "יחסית (פי ~3 מטעינת הספרייה כולה לשאילתה) - לשקול מטמון/אינדקס מראש",
         ship_in_app="yes", regenerate="python -m tools.build_first_aid --from <pack>"),
    dict(path="first_aid/content/search_index_he.json", name_he="אינדקס חיפוש מוכן מראש לעזרה ראשונה",
         engine="first_aid", role="runtime", kind="generated_cache", format="json",
         source="tools/build_first_aid.py:build_index", licence="תוכן הפרויקט", licence_flag="",
         precision="n.a.", coverage="n.a.", built_at="2026-11-07", records="files",
         used_by="רק tools/build_first_aid.py (בדיקת תאימות); library.py בונה אינדקס בזיכרון ולא קורא אותו",
         v=dict(first_aid="relevant_not_used"), verdict="relevant_not_used",
         recommendation="נארז באפליקציה ולא נקרא. או לטעון אותו ב-Library (חוסך בניית אינדקס בכל הפעלה) או להוציא מהאריזה",
         ship_in_app="yes (unused)", regenerate="python -m tools.build_first_aid"),
    dict(path="first_aid/content/sources.json",
         paths=["first_aid/content/sources.json", "first_aid/synonyms.json", "first_aid/supplement_policy.json",
                "first_aid/content/supplements/active.json"],
         name_he="מקורות, מילים נרדפות (ערבית/אנגלית), מדיניות הרחבות, הרחבה פעילה",
         engine="first_aid", role="runtime", kind="curated_content", format="json",
         source="פרויקט LifeGuard", licence="תוכן הפרויקט", licence_flag="", precision="n.a.", coverage="n.a.",
         built_at="2026-11-07", records="files",
         used_by="first_aid/library.py:load; first_aid/supplements.py; android Supplements.kt/FirstAidLibrary.kt",
         v=dict(first_aid="in_use"), verdict="in_use", recommendation="", ship_in_app="yes",
         regenerate="python -m tools.build_first_aid --from <pack>"),
    dict(path="first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/first_aid_supplement_he.json",
         name_he="חבילת הרחבה 1.2.0 - 45 מדריכים נוספים", engine="first_aid", role="runtime",
         kind="curated_content", format="json", source="supplement_02 (SOURCES_AND_MEDICAL_REVIEW.md)",
         licence="תוכן הפרויקט; מקורות מצוטטים", licence_flag="medical review pending", precision="n.a.",
         coverage="ישראל", built_at="2026-11-07", records="json:guides",
         used_by="first_aid/supplements.py; android Supplements.kt", v=dict(first_aid="in_use"), verdict="in_use",
         recommendation="", ship_in_app="yes", regenerate="tools/build_first_aid.py (חבילת הרחבה)"),
    dict(path="first_aid/content/first_aid_guides_he.md",
         paths=["first_aid/content/first_aid_guides_he.md", "first_aid/content/README.md",
                "first_aid/content/SOURCES_AND_REVIEW.md", "first_aid/content/manifest.json",
                "first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/first_aid_supplement_he.md",
                "first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/README.md",
                "first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/SOURCES_AND_MEDICAL_REVIEW.md",
                "first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/CLAUDE_SUPPLEMENT_PROMPT.md",
                "first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/manifest.json"],
         name_he="עותקי Markdown קריאים + תיעוד/פרומפט של חבילות העזרה הראשונה", engine="first_aid", role="docs",
         kind="curated_content", format="md, json", source="חבילות התוכן", licence="תוכן הפרויקט", licence_flag="",
         precision="n.a.", coverage="n.a.", built_at="2026-11-07", records="files",
         used_by="אף קוד ריצה (נארז כי LifeGuard.spec/Gradle אורזים את content/**)",
         v=dict(first_aid="redundant"), verdict="redundant",
         recommendation="~150KB של md + CLAUDE_SUPPLEMENT_PROMPT.md נארזים באפליקציה בלי שימוש - "
                        "להחריג *.md מה-spec ומ-firstAidIncludes ב-Gradle (לשמור במאגר)",
         ship_in_app="yes (should not)", regenerate="-"),
    dict(path="content/emergency_guidance_he.json", name_he="הנחיות חירום (10 תרחישים, 12 מקורות) - מרכז הבטיחות",
         engine="first_aid", role="runtime", kind="curated_content", format="json",
         source="מסוכם ממקורות (מד\"א, פיקוד העורף, St John ועוד)", licence="תוכן הפרויקט; מקורות מצוטטים",
         licence_flag="professional review pending", precision="n.a.", coverage="ישראל",
         built_at="2026-11-04", records="json:scenarios", used_by="guidance_engine.py; LifeGuard.spec",
         v=dict(first_aid="in_use", national="in_use"), verdict="in_use",
         recommendation="review_status: לא נבדק ע\"י גורם מוסמך - חובה סקירה לפני שחרור; גרסה 2026.11.04",
         ship_in_app="yes", regenerate="עריכה ידנית"),

    # ------------------------------------------------------------------ fixtures של Android
    dict(path="android/app/src/test/resources/first_aid_parity.json", name_he="תאימות עזרה ראשונה Python<->Kotlin",
         engine="first_aid", role="test_fixture", kind="test_fixture", format="json",
         source="נוצר מ-first_aid/library.py", licence="פנימי", licence_flag="", precision="n.a.", coverage="n.a.",
         built_at="2026-11-07", records="files", used_by="android FirstAidParityTest.kt",
         v=dict(first_aid="in_use"), verdict="in_use",
         recommendation="אין סקריפט מחולל ב-tools/ (בניגוד לשני האחרים) - להוסיף tools/build_first_aid_parity.py",
         ship_in_app="no", regenerate="לא מתועד (נוצר מ-first_aid/library.py)"),
    dict(path="android/app/src/test/resources/helpers_contract.json", name_he="חוזה API מתנדבים/עוזרים לבדיקות אנדרואיד",
         engine="safe_places", role="test_fixture", kind="test_fixture", format="json",
         source="tools/build_helpers_contract.py", licence="פנימי", licence_flag="", precision="n.a.", coverage="n.a.",
         built_at="2026-11-08", records="files", used_by="android HelpersContractTest.kt, HelpersApiAndOsrmTest.kt",
         v=dict(safe_places="in_use"), verdict="in_use", recommendation="", ship_in_app="no",
         regenerate="python -m tools.build_helpers_contract"),
    dict(path="android/app/src/test/resources/safe_places_parity.json", name_he="תאימות בחירת מקום בטוח Python<->Kotlin",
         engine="safe_places", role="test_fixture", kind="test_fixture", format="json",
         source="tools/build_safe_places_parity.py", licence="פנימי (נגזר מ-safe_places_il.json - ODbL)",
         licence_flag="", precision="n.a.", coverage="n.a.", built_at="2026-11-08", records="files",
         used_by="android SafePlacesParityTest.kt", v=dict(safe_places="in_use"), verdict="in_use",
         recommendation="לייצר מחדש אחרי כל בנייה של safe_places_il.json", ship_in_app="no",
         regenerate="python -m tools.build_safe_places_parity"),

    # ------------------------------------------------------------------ מצב ריצה (לא דאטהסטים)
    dict(path="community.db", name_he="מסד קהילתי ישן בשורש המאגר (incidents)", engine="none", role="runtime_state",
         kind="runtime_state", format="sqlite", source="גרסה מוקדמת של backend קהילתי (לפני server/)",
         licence="n.a. (ייתכן מידע אישי)", licence_flag="possible personal data", precision="n.a.", coverage="n.a.",
         built_at="2026-11-04", records="sqlite", used_by="אף קוד במאגר (gitignored ע\"י *.db)",
         v={}, verdict="not_relevant",
         recommendation="לא בשימוש. להעביר ל-data/ או למחוק אחרי אישור המשתמש; לעולם לא לארוז",
         ship_in_app="never", regenerate="-"),
    dict(path="server/server_data", name_he="מצב ריצה של שרת הפיתוח המקומי (DB, WAL, מפתח הצפנה, לוג)",
         engine="none", role="runtime_state", kind="runtime_state", format="sqlite, key",
         source="server/lifeguard_server.py", licence="n.a. (נתוני משתמשים + data.key)",
         licence_flag="secret key + user data", precision="n.a.", coverage="n.a.", built_at="2026-11-11",
         records="doc:not opened (runtime state)", used_by="server/lifeguard_server.py, server/data_sources.py (מטמון)",
         v={}, verdict="not_relevant",
         recommendation="לא דאטהסט - לא נפתח. ה-WAL (4.1MB) גדול פי 8 מה-DB: checkpoint בכיבוי השרת; data.key לעולם לא לארוז/לשתף",
         ship_in_app="never", regenerate="-"),
]

# שורשים שנבדקים לגילוי נכסים חדשים שלא בקטלוג (רק רשימת ילדים ישירים - בלי לפתוח קבצים)
DISCOVERY_ROOTS = ["models", "safe_places", "content", "first_aid/content", "research/datasets", "research/samples",
                   "research/samples/israel", "research/samples/israel/derived", "research/samples/risk_factors",
                   "research/samples/news_intel", "research/samples/fsd50k", "research/samples/fleurs_he",
                   "research/models", "research/models/asr", "research_data", "research_data/audio",
                   "android/app/src/test/resources", "server/server_data"]


# ======================================================================================== סריקה
def _norm(rel):
    r = rel.replace("\\", "/")
    while r.startswith("./"):
        r = r[2:]
    return r.strip("/")


def _assert_not_data(rel):
    """הגנה קשיחה: אף נתיב תחת data/ בשורש המאגר."""
    r = rel.replace("\\", "/")
    while r.startswith("./"):
        r = r[2:]
    r = r.strip("/")
    if r == FORBIDDEN_TOP or r.startswith(FORBIDDEN_TOP + "/"):
        raise PermissionError(f"refusing to touch the user data directory: {rel}")
    return r


def _abs(root, rel):
    rel = _assert_not_data(rel)
    p = os.path.normpath(os.path.join(root, rel))
    top = os.path.normcase(os.path.join(os.path.normpath(root), FORBIDDEN_TOP))
    if os.path.normcase(p) == top or os.path.normcase(p).startswith(top + os.sep):
        raise PermissionError(f"refusing to touch the user data directory: {rel}")
    return p


def walk_sizes(root, path, max_files=None):
    """(files, bytes, newest_mtime, ext_counts) בעזרת os.scandir - בלי לפתוח קבצים, בלי symlinks.
    לא נכנס לתיקיית data/ בשורש, ולא לתיקיות SKIP_DIRS."""
    data_dir = os.path.normcase(os.path.join(os.path.normpath(root), FORBIDDEN_TOP))
    files = size = 0
    newest = 0.0
    exts = {}
    if os.path.isfile(path):
        st = os.stat(path)
        e = os.path.splitext(path)[1].lower()
        return 1, st.st_size, st.st_mtime, {e: 1}
    stack = [path]
    while stack:
        d = stack.pop()
        if os.path.normcase(os.path.normpath(d)) == data_dir:
            continue
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name not in SKIP_DIRS:
                            stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        files += 1
                        size += st.st_size
                        newest = max(newest, st.st_mtime)
                        x = os.path.splitext(e.name)[1].lower()
                        exts[x] = exts.get(x, 0) + 1
                        if max_files and files > max_files:
                            return files, size, newest, exts
                except OSError:
                    continue
    return files, size, newest, exts


def _dig(obj, dotted):
    for k in dotted.split("."):
        obj = (obj or {}).get(k) if isinstance(obj, dict) else None
    return obj


def count_records(root, spec, paths, exts):
    """ספירה זולה בלבד. מחזיר מחרוזת (ריק אם לא ניתן/יקר)."""
    if not spec:
        return ""
    kind, _, arg = spec.partition(":")
    if kind == "doc":
        return arg
    if kind == "files":
        if arg:
            return f"{exts.get(arg.lower(), 0)} {arg} files"
        return f"{sum(exts.values())} files"
    targets = [p for p in paths if os.path.isfile(p)]
    if kind in ("csv", "tsv"):
        idx = [0] if arg in ("", "1") else ([1] if arg == "2" else list(range(len(targets))))
        out = []
        for i in idx:
            if i >= len(targets) or os.path.getsize(targets[i]) > RECORDS_MAX_BYTES:
                continue
            with open(targets[i], "rb") as fh:
                n = sum(1 for _ in fh)
            out.append(f"{max(0, n - (1 if kind == 'csv' else 0))} rows ({os.path.basename(targets[i])})")
        return "; ".join(out)
    if kind in ("json", "jsongz"):
        p = targets[-1] if kind == "json" and len(targets) > 1 else (targets[0] if targets else None)
        if not p or os.path.getsize(p) > RECORDS_MAX_BYTES:
            return ""
        opener = gzip.open if kind == "jsongz" else open
        with opener(p, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        parts = []
        for key in arg.split(","):
            v = _dig(d, key)
            if v is not None:
                parts.append(f"{len(v) if isinstance(v, (list, dict)) else v} {key}")
        return " / ".join(parts)
    if kind == "npz":
        try:
            import numpy as np
        except ImportError:
            return ""
        with np.load(targets[0], allow_pickle=False) as z:
            return f"{z[arg].size} weights ({arg})" if arg in z.files else ", ".join(z.files)
    if kind == "sqlite":
        uri = "file:" + targets[0].replace("\\", "/") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            counts = []
            for n in names:
                q = 'SELECT COUNT(*) FROM "%s"' % n.replace('"', '""')
                counts.append(f"{n}={con.execute(q).fetchone()[0]}")
            return ", ".join(counts)
        finally:
            con.close()
    return ""


def _is_bulk(entry):
    return entry.get("role") == "raw_dataset" or entry["path"] in (
        "research/samples/fsd50k/cache", "research_data/audio/fleurs", "research_data/audio/cache")


def scan_entry(root, entry, bulk=True):
    rels = entry.get("paths") or [entry["path"]]
    paths = [_abs(root, r) for r in rels]
    row = {c: "" for c in COLUMNS}
    for c in COLUMNS:
        if c in entry and c != "v":
            row[c] = entry[c]
    v = entry.get("v") or {}
    for e in ENGINES:
        row["v_" + e] = v.get(e, "not_relevant")
    if not bulk and _is_bulk(entry):
        row.update(size_mb="(skipped)", files="(skipped)", records=entry.get("records", "")
                   if str(entry.get("records", "")).startswith("doc:") else "")
        row["records"] = str(row["records"]).replace("doc:", "")
        return row
    files = size = 0
    newest = 0.0
    exts = {}
    present = False
    for p in paths:
        if not os.path.exists(p):
            continue
        present = True
        f, s, m, x = walk_sizes(root, p)
        files, size, newest = files + f, size + s, max(newest, m)
        for k, n in x.items():
            exts[k] = exts.get(k, 0) + n
    if not present:
        row.update(size_mb="0", files="0", records="MISSING")
        return row
    row["size_mb"] = f"{size / 1e6:.3f}" if size < 1e7 else f"{size / 1e6:.1f}"
    row["files"] = str(files)
    row["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)) if newest else ""
    if not row["built_at"] and newest:
        row["built_at"] = row["mtime"][:10]
    try:
        row["records"] = count_records(root, entry.get("records"), paths, exts)
    except (OSError, ValueError, KeyError, sqlite3.Error) as e:  # noqa: PERF203
        row["records"] = f"(error: {type(e).__name__})"
    return row


def discover(root, catalog=CATALOG):
    """ילדים ישירים של DISCOVERY_ROOTS שלא מכוסים ע\"י אף שורה בקטלוג -> שורות 'unclear'."""
    covered = set()
    for e in catalog:
        for r in (e.get("paths") or [e["path"]]):
            covered.add(_norm(r))
    out = []
    for base in DISCOVERY_ROOTS:
        b = _abs(root, base)
        if not os.path.isdir(b):
            continue
        for e in sorted(os.scandir(b), key=lambda x: x.name):
            if e.name in SKIP_DIRS or e.name.startswith("."):
                continue
            rel = _norm(base + "/" + e.name)
            if rel == FORBIDDEN_TOP or rel.startswith(FORBIDDEN_TOP + "/"):
                continue
            hit = any(c == rel or c.startswith(rel + "/") or rel.startswith(c + "/") for c in covered)
            if hit or (rel + "/") in {d + "/" for d in DISCOVERY_ROOTS}:
                continue
            out.append(dict(path=rel, name_he="(לא מקוטלג - נמצא בסריקה)", engine="unknown", role="",
                            kind="", verdict="unclear", records="files" if e.is_dir() else "",
                            recommendation="נכס חדש - להוסיף שורה ל-CATALOG ב-tools/build_data_catalog.py", v={}))
    return out


def scan_rows(root=ROOT, catalog=None, bulk=True, with_discovery=True):
    catalog = list(CATALOG if catalog is None else catalog)
    for e in catalog:
        _assert_not_data(e["path"])
        for r in e.get("paths") or []:
            _assert_not_data(r)
    if with_discovery:
        catalog += discover(root, catalog)
    return [scan_entry(root, e, bulk=bulk) for e in catalog]


def write_csv(rows, path=OUT_CSV):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:   # BOM - Excel מציג עברית נכון
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def summary(rows):
    by = {}
    for r in rows:
        by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    total = sum(float(r["size_mb"]) for r in rows if r["size_mb"] not in ("", "(skipped)"))
    return by, total


def main(argv=None):
    ap = argparse.ArgumentParser(description="LifeGuard data catalog (read-only scan; never touches data/)")
    ap.add_argument("--out", default=OUT_CSV)
    ap.add_argument("--no-bulk", action="store_true", help="לדלג על תיקיות ענק (FSD50K raw/cache, FLEURS)")
    a = ap.parse_args(argv)
    t = time.time()
    rows = scan_rows(ROOT, bulk=not a.no_bulk)
    write_csv(rows, a.out)
    by, total = summary(rows)
    print(f"{len(rows)} rows -> {a.out} ({time.time() - t:.1f}s)")
    print("verdicts:", ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    print(f"total size: {total / 1000:.2f} GB")
    new = [r["path"] for r in rows if r["engine"] == "unknown"]
    if new:
        print("uncatalogued:", ", ".join(new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
