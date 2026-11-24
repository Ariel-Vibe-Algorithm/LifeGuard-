# קטלוג הנתונים של LifeGuard

נוצר ב-2026-11-11. הקטלוג סוקר את כל נכסי הנתונים במאגר: מה יש, מאיפה זה הגיע, איזה מנוע משתמש בזה בפועל, האם זה עדיין רלוונטי, ואיפה מבזבזים זמן טעינה או מקום בדיסק.

- **טבלה ממוחשבת:** `research/data_catalog.csv` (UTF-8 עם BOM, נפתח באקסל עם עברית תקינה). זו אותה טבלה כמו כאן, עם עמודות נוספות: פסיקה לכל מנוע, דגל רישיון, האם נארז באפליקציה ופקודת בנייה מחדש.
- **רענון:** `python -m tools.build_data_catalog`. הפקודה סורקת מחדש גודל, מספר קבצים, רשומות ותאריך. עמודות השיפוט נמצאות ב-`CATALOG` בתוך הסקריפט. הסריקה המלאה לוקחת 5–10 שניות, כולל 67 אלף הקבצים של FSD50K. עם `--no-bulk` היא מדלגת על התיקיות הענקיות. נכס חדש שלא מופיע בקטלוג יודפס בסוף הריצה תחת "uncatalogued".
- **מה לא נסרק:** תיקיית `data/` (נתוני משתמשים אמיתיים) לא נפתחה ולא נסרקה. הסקריפט חוסם אותה, ו-`tests/test_data_catalog.py` בודק את החסימה. גם `.venv`, `.tools` ו-`node_modules` לא נסרקו. התיקייה `server/server_data` נמדדה בגודל בלבד, בלי לפתוח אותה. ב-`research_data/audio` סוכן אחר עדיין מוריד קבצים, ולכן הגודל שלה ישתנה.
- **סייג:** לא נמחק, הועבר או שונה אף קובץ נתונים. כל ההמלצות כאן הן המלצות בלבד.

## 1. סיכום

**57 נכסים, בסך הכול כ-40.6 GB.** כמעט כל הנפח הוא אודיו למחקר: FSD50K גולמי תופס 24.7 GB, ו-`research_data/audio` עוד בהורדה. הנתונים שהמנועים טוענים בזמן ריצה קטנים מאוד: כ-0.5 MB בדיסק.

| פסיקה | מספר נכסים |
|---|---|
| בשימוש (`in_use`) | 37 |
| רלוונטי, לא בשימוש (`relevant_not_used`) | 5 |
| כפול/מיותר (`redundant`) | 7 |
| מיושן (`stale`) | 2 |
| לא רלוונטי (`not_relevant`) | 5 |
| לא ברור (`unclear`) | 1 |

איך לקרוא את הפסיקה:
- **בשימוש:** הנכס נקרא בקוד היום, בזמן ריצה או ככלי בנייה, הערכה או בדיקה שמייצר או מאמת נכס ריצה. העמודה `role` מבחינה בין המקרים: runtime: 15, research_eval: 14, build_input: 10, test_fixture: 6, research_output: 5, docs: 2, raw_dataset: 2, runtime_state: 2, runtime_optional: 1.
- **רלוונטי, לא בשימוש:** אין היום קוד שקורא את הנכס.
- **כפול/מיותר:** יש נכס אחר עם אותו תוכן. השם שלו מופיע בהמלצה.

### מדידות יעילות: מה המנועים טוענים בזמן ריצה

המדידה נעשתה על המכונה הזו בזמן שסוכן אחר עיבד אודיו. מבחן CPU קצר רץ פי 6–8 לאט מהרגיל, ולכן חשוב היחס בין השורות ולא הזמנים עצמם. במכונה פנויה הזמנים צפויים להיות קצרים בערך פי 6 (אומדן).

| קובץ / פעולה | בדיסק | JSON לא דחוס | פענוח JSON | טעינה במנוע | זיכרון שנשאר |
|---|---|---|---|---|---|
| `models/risk_factors_grid.json.gz`, ‏`RiskFactors.load` | 144 KB | 2.23 MB | 933 ms | **2,568 ms** | **11.9 MB** |
| `models/safety_layers.json.gz`, ‏`Layers.load` | 53 KB | 0.34 MB | 76 ms | **1,950 ms** (מתוכם 747 ms חישוב סף המוקד) | 2.4 MB |
| `models/street_lights_grid.json.gz`, ‏`LightGrid.load` | 80 KB | 0.38 MB | 71 ms | 149 ms | 4.0 MB |
| `first_aid` ‏`library.load` (39+45 מדריכים) | ~0.2 MB | — | — | 761 ms | 2.9 MB |
| `first_aid` ‏`Library.search`, לשאילתה אחת | — | — | — | **680–3,000 ms** | — |
| `safe_places_il.json`, ‏`_bundled_places` | 53 KB | — | — | 3 ms | 0.2 MB |
| `Layers.shelters_near` (סריקה ליניארית של 858 מקלטים) | — | — | — | ~8 ms לקריאה | — |
| `RiskFactors.flags_at` | — | — | — | 0.07 ms לקריאה | — |
| `content/emergency_guidance_he.json`, ‏`models/distress_head.npz` | 19 KB, 40 KB | — | — | 1 ms, 9 ms | ~0 |

**בזבוזים שזוהו:**
- **השכבות נטענות רק בבקשה הראשונה.** `SafetyIntelMixin._layers/_risk_factors/_light_grid` נטענות בעצלות, ולכן בקשת המסלול הראשונה אחרי הפעלת השרת משלמת על שלוש השכבות יחד: ~4.7 שניות כאן.
- **`risk_factors_grid` נטען כמעט כולו לחינם.**
  - המנוע קורא רק 5 מתוך 19 השדות: `bar`, `nightclub`, `bar_licensed_tlv`, `night_zone_close`, `rail_station`.
  - 64% מהתאים (21,804 מתוך 34,157) מכילים רק עיר וקוד אזור סטטיסטי, בלי שום ערך.
  - השדות `lights` ו-`ped_acc` כפולים ל-`street_lights_grid` ול-`safety_layers`.
- **סף המוקד מחושב מחדש בכל הפעלה.** סף מוקד התאונות (העשירון העליון) נגזר מהנתונים בכל טעינה, אף שהוא תלוי רק בקובץ. זה כ-38% מזמן `Layers.load`.
- **החיפוש בעזרה ראשונה איטי.** שאילתה אחת עולה בערך כמו טעינת הספרייה כולה, וזה מסך שמשתמשים בו בזמן חירום. בנוסף, `search_index_he.json` נארז באפליקציה ואף קוד לא קורא אותו.
- **קבצים שנארזים בלי שימוש.** כ-150 KB של Markdown ו-`CLAUDE_SUPPLEMENT_PROMPT.md` נארזים ב-EXE וב-APK, כי `content/**` נארז כולו.
- **הזיהוי האקוסטי חסר ב-EXE.** `distress_head.npz` ו-YAMNet לא נארזים ב-`LifeGuard.spec`, ולכן ב-EXE הזיהוי האקוסטי נופל לגלאי עוצמת רעש. לפי `acoustic_model_holdout.md`, ה-recall של הגלאי הזה הוא 0%.

### 5 ההמלצות העיקריות להאצת המנועים ולשיפור הדיוק

1. **לייצא גרסת ריצה מצומצמת של `risk_factors_grid`.** הגרסה תכיל רק את 5 השדות הנקראים, בלי תאים ריקים, וטבלת אזורים סטטיסטיים נפרדת ברשת גסה. השדות `lights` ו-`ped_acc` יוסרו, כי הם כבר קיימים בשכבות אחרות. זו השכבה הכבדה ביותר: 2.6 שניות ו-12 MB כאן. השינוי נעשה ב-`research_tools/risk_factors.py` בשלב הייצוא, ו-`server/risk_factors_layer.py` לא צריך להשתנות.
2. **לחשב מראש כל מה שאפשר בזמן הבנייה, ולחמם את השכבות בהפעלת השרת.**
   - לשמור את `hotspot` בתוך `safety_layers.json.gz` (ב-`build_safety_layers.py`).
   - לבנות אינדקס רשת למקלטים.
   - לטעון את שלוש השכבות ב-thread רקע כשהשרת עולה, כדי שבקשת המסלול הראשונה לא תחכה.
3. **להאיץ את החיפוש בעזרה ראשונה.** כדאי למדוד את `Library.search` (מרחק עריכה על כל הטוקנים) ולהוסיף מטמון ל-stems של השאילתה, או להשתמש באינדקס הבנוי מראש שכבר נארז. אם לא משתמשים באינדקס, אין טעם לארוז אותו. זה שיפור ישיר בזמן תגובה במצב חירום.
4. **להחזיר את הזיהוי האקוסטי ל-EXE עם רישיון תקין.**
   - לאמן מחדש את `distress_head` על קטעי FSD50K ברישיון CC0 ו-CC-BY בלבד. הרישיון של כל קטע מופיע ב-`fsd50k/meta`.
   - לארוז את הראש החדש ואת `yamnet_float_embeddings.tflite` ב-`LifeGuard.spec`.
   - בלי השינוי הזה, ה-EXE עובד עם גלאי שה-recall שלו 0%.
5. **שכבת החדשות מיושנת.** הצילום האחרון הוא מ-08/11, וידיעות דועכות תוך ימים. שתי אפשרויות: להשאיר אותה כבויה (ברירת המחדל כש-`LIFEGUARD_NEWS_FILE` לא מוגדר), או לתזמן `news_intel run` אחרי שמתקבל אישור בכתב מהמפרסמים. 3 מתוך 4 הצילומים מיותרים.

המלצות נוספות לדיוק:
- להוסיף מקלטים (858 בשכבה) כקטגוריה בחיפוש מקום בטוח בזמן התרעה.
- ברשימת המקומות הבטוחים יש רק 33 בתי חולים, וכולם מ-OSM. כדאי לבדוק רשימה רשמית של משרד הבריאות.
- הטבלה `neighborhood_crime_signal.csv` לא צריכה להזין שום מנוע. דו"ח השכבה הישראלית (§3.2) מצא שהיחס בה מחושב לא נכון.

### מה למחוק או לארכב (המלצה בלבד, שום דבר לא נמחק)

| נכס | גודל | למה | פעולה מומלצת |
|---|---|---|---|
| `research/samples/fsd50k/raw` | 24.7 GB | התכונות כבר חולצו ל-`*_features.npz` | לארכב לכונן חיצוני. אפשר להוריד שוב מ-Zenodo 4060432 ולאמת עם `dev_md5.txt` |
| `research/samples/fsd50k/cache` | 2.3 GB (67,400 קבצים) | כפול ל-`*_features.npz`: ‏`load_features` קורא קודם את הקובץ המאוחד | למחוק אחרי אישור. זה גם יאיץ סריקות וגיבויים |
| `research/samples/fleurs_he/dev.tar.gz` | 153 MB | כפול ל-`dev/*.wav` שחולצו ממנו | להשאיר רק אחד מהשניים |
| `research/models/asr/...zipformer-en-20M...` | 44 MB | המודל נבדק ונפסל (לפי `speech_local.py`) | למחוק או לארכב |
| `research/samples/israel/derived/crime_by_stat_area_summary.csv` | 222 KB | זהה בייט-לבייט ל-`research/datasets/...` | להשאיר את העותק שבמאגר (`research/datasets`) |
| `research/samples/israel/derived/pedestrian_accidents_2020-2024.csv` | 1.5 MB | כפול ל-geojson, והבנייה קוראת רק את ה-geojson | אופציונלי |
| `research/samples/news_intel/news_intel_*T1034/T1043/T1154.json` ו-`news_intel/cache` | ~0.7 MB | צילומים ישנים | לנקות לפני הריצה הבאה |
| `community.db` (שורש המאגר) | 37 KB | 73 רשומות incidents, אף קוד לא קורא אותו, ייתכן מידע אישי | להעביר ל-`data/` או למחוק, **אחרי אישור המשתמש** |
| `first_aid/**/*.md`, ‏`CLAUDE_SUPPLEMENT_PROMPT.md` | ~150 KB | נארזים באפליקציה בלי שימוש | לא למחוק. להחריג מ-`LifeGuard.spec` ומ-`firstAidIncludes` ב-Gradle |
| `server/server_data/lifeguard_server.db-wal` | 4.1 MB | גדול פי 8 מה-DB | checkpoint בכיבוי מסודר של השרת |

**חיסכון אפשרי: כ-27.2 GB בדיסק**, בלי לאבד יכולת לאמן מחדש או לשחזר.

## 2. הטבלה המלאה לפי מנוע

נכס שרלוונטי לכמה מנועים מופיע בכל אחד מהם. העמודה "פסיקה" מתייחסת למנוע של אותה טבלה.

### מנוע מסלול / מסוכנות (safety_intel, risk_factors_layer, context_risk)

14 נכסים · בשימוש: 8, רלוונטי, לא בשימוש: 5, כפול/מיותר: 1

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `models/safety_layers.json.gz` | שכבות רשמיות: מוקדי תאונות הולכי רגל (רשת) + מקלטים ציבוריים | runtime | official_verified | 53 KB | 7207 accidents.cells / 858 shelters | street (~100 m cell); shelters: point | 2026-11-07 | server/safety_intel.py:Layers.load, SafetyIntelMixin._layers; server/national.py (layers.shelters_near) | **in_use** | לחשב את סף המוקד (Layers._hotspot_threshold) בזמן הבנייה ולשמור בקובץ - כ-40% מזמן הטעינה; אינדקס רשת למקלטים במקום סריקה ליניארית של 858 בכל קריאה; לשקול מקלטים כקטגוריה בחיפוש מקום בטוח בזמן התרעה |
| `models/street_lights_grid.json.gz` | רשת עמודי תאורה (~50 מ') - ת"א ובאר שבע | runtime | official_verified | 80 KB | 23824 cells / 135 cover | street (~50 m cell) | 2026-11-07 | context_risk.py:LightGrid.load/_grid/assess; server/safety_intel.py:_light_grid | **in_use** | אותו מקור מופיע גם כשדה lights ב-risk_factors_grid (שאינו נקרא בזמן ריצה) - להשאיר רק כאן; לאמת רישיון GIS ת"א לפני הפצה. עמוד ממופה != עמוד דולק |
| `models/risk_factors_grid.json.gz` | רשת גורמי סיכון סביבתיים (~100 מ') + הקשר אזור סטטיסטי (פשיעה) | runtime | mixed_official_osm | 144 KB | 34157 cells / 459 stat_areas | street (~100 m) + stat-area (crime) | 2026-11-08 | server/risk_factors_layer.py:RiskFactors.load/flags_at/area_context; server/safety_intel.py:_risk_factors | **in_use** | השכבה הכבדה ביותר בזמן ריצה (2.2MB JSON, ~12MB בזיכרון). רק 5 מ-19 השדות נקראים (bar, nightclub, bar_licensed_tlv, night_zone_close, rail_station); 64% מהתאים הם רק עיר+א"ס. לייצא גרסת ריצה מצומצמת: שדות בשימוש + טבלת א"ס בתאים גסים |
| `research/models/clip` | CLIP ViT-B/32 מכומת (ONNX) - ניתוח תמונות רחוב | research_eval | model_weights | 155.9 MB | 5 files | n.a. | 2026-11-06 | research_tools/street_vision.py (מחקר בלבד) | **relevant_not_used** | 156MB, לא בשום מנוע ריצה. אם שכבת תמונות רחוב תיכנס - רק בשרת, לא באפליקציה |
| `research/models/yunet` | YuNet - זיהוי פנים לטשטוש (פרטיות בתמונות רחוב) | research_eval | model_weights | 234 KB | 2 files | n.a. | 2026-11-06 | research_tools/street_vision.py | **relevant_not_used** | נדרש רק אם street_vision יופעל |
| `research/datasets/crime_by_stat_area_summary.csv` | סיכום פשיעה לפי אזור סטטיסטי (2024-2025) | research_output | official_verified | 222 KB | 1126 rows (crime_by_stat_area_summary.csv) | stat-area | 2026-11-06 | research_tools/israel_layer.py (כותב); אף מנוע לא קורא | **redundant** | זהה בייט-לבייט ל-research/samples/israel/derived/crime_by_stat_area_summary.csv; אות הפשיעה בזמן ריצה מגיע מ-risk_factors_grid (stat_areas). להשאיר עותק אחד |
| `research/samples/israel/cache` | מטמון API גולמי: משטרה, למ"ס, GIS ת"א/חיפה, תאורה ומקלטים ב"ש | build_input | generated_cache | 28.6 MB | 65 files | mixed | 2026-11-06 | research_tools/israel_layer.py --offline; research_tools/build_safety_layers.py --offline | **in_use** | קלט לבנייה אופליין בלבד; ~20 עמודי police_crime של 1.48MB - לשמור עד הבנייה הבאה |
| `research/samples/israel/derived/pedestrian_accidents_2020-2024.geojson` | תאונות הולכי רגל עם קואורדינטות 2020-2024 (geojson + csv) | build_input | official_verified | 6.0 MB | 11613 rows (pedestrian_accidents_2020-2024.csv) | point (accident) | 2026-11-06 | research_tools/build_safety_layers.py (ACCIDENTS = ה-geojson) | **in_use** | ה-csv (1.5MB) הוא כפילות של ה-geojson (4.5MB) - הבנייה קוראת רק את ה-geojson |
| `research/samples/israel/derived/streetlights_tel_aviv.csv` | נקודות עמודי תאורה ת"א + באר שבע (csv) | build_input | official_verified | 5.2 MB | 52162 rows (streetlights_tel_aviv.csv); 29867 rows (streetlights_beer_sheva.csv) | point (pole) | 2026-11-06 | research_tools/build_light_grid.py; research_tools/lighting_validation.py | **in_use** |  |
| `research/samples/israel/derived/crime_units_all_2024-2025.csv` | תוצרי פשיעה (יחידות, ערים, א"ס, הצלבה, סיכום בנייה) | research_output | official_verified | 831 KB | 2756 rows (crime_units_all_2024-2025.csv) | stat-area / city | 2026-11-06 | research_tools/israel_layer.py (כותב); research/israel_layer_report.md | **relevant_not_used** | crime_by_stat_area_summary.csv כאן כפול ל-research/datasets; השאר - ראיות לדו"ח |
| `research/samples/risk_factors/cache` | מטמון OSM/עירוני/משטרה לגורמי סיכון (4 ערים) | build_input | generated_cache | 3.7 MB | 39 files | point / stat-area | 2026-11-07 | research_tools/risk_factors.py build --offline; tools/build_safe_places.py (משטרה/בתי חולים מ-OSM) | **in_use** | קלט לבנייה אופליין של שתי שכבות ריצה - לא למחוק |
| `research/samples/risk_factors/(outputs)` | תוצרי אימות גורמי סיכון (פוליגוני א"ס 2011, גורמים לכל א"ס, תוצאות רגרסיה) | research_output | mixed_official_osm | 1.1 MB | 500 rows (stat_areas_2011_factors.csv) | stat-area | 2026-11-08 | research_tools/risk_factors.py validate; research/risk_factors_report.md | **relevant_not_used** | ראיות למשקלות במנוע (risk_factors_report.md) - לשמור |
| `research/samples/news_intel/news_intel_2026-11-08T1157.json` | צילומי שכבת חדשות (4 ריצות, 08/11) - קלט ל-LIFEGUARD_NEWS_FILE | runtime_optional | news_unverified | 247 KB | 21 items | city / street (geocoded, variable) | 2026-11-08 | server/news_layer.py:NewsLayer.from_env (רק אם LIFEGUARD_NEWS_FILE מוגדר); server/safety_intel.py:_news | **relevant_not_used** | ידיעות דועכות תוך ימים - הצילום מ-08/11 כבר לא רלוונטי; 3 מ-4 הצילומים מיותרים. לייצר מחדש בתזמון (cron) רק אחרי אישור מפרסמים |
| `research/samples/street_sanity` | 10 תמונות רחוב (Wikimedia) לבדיקת street_vision | test_fixture | test_fixture | 2.4 MB | 10 .jpg files | n.a. | 2026-11-06 | tests/test_street_vision.py | **in_use** |  |

### חיפוש מקום בטוח (map_module)

5 נכסים · בשימוש: 4, רלוונטי, לא בשימוש: 1

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `models/safety_layers.json.gz` | שכבות רשמיות: מוקדי תאונות הולכי רגל (רשת) + מקלטים ציבוריים | runtime | official_verified | 53 KB | 7207 accidents.cells / 858 shelters | street (~100 m cell); shelters: point | 2026-11-07 | server/safety_intel.py:Layers.load, SafetyIntelMixin._layers; server/national.py (layers.shelters_near) | **relevant_not_used** | לחשב את סף המוקד (Layers._hotspot_threshold) בזמן הבנייה ולשמור בקובץ - כ-40% מזמן הטעינה; אינדקס רשת למקלטים במקום סריקה ליניארית של 858 בכל קריאה; לשקול מקלטים כקטגוריה בחיפוש מקום בטוח בזמן התרעה |
| `research/samples/risk_factors/cache` | מטמון OSM/עירוני/משטרה לגורמי סיכון (4 ערים) | build_input | generated_cache | 3.7 MB | 39 files | point / stat-area | 2026-11-07 | research_tools/risk_factors.py build --offline; tools/build_safe_places.py (משטרה/בתי חולים מ-OSM) | **in_use** | קלט לבנייה אופליין של שתי שכבות ריצה - לא למחוק |
| `safe_places/safe_places_il.json` | רשימה ארצית מובנית: תחנות משטרה, כיבוי ובתי חולים עם מיון | runtime | mixed_official_osm | 53 KB | 322 places | point | 2026-11-08 | map_module.py:_bundled_places/_choose; android SafePlaces.kt (assets); LifeGuard.spec | **in_use** | בתי חולים: 33 בלבד ומ-OSM - לשקול רשימת משרד הבריאות; לציין ייחוס ODbL בממשק |
| `android/app/src/test/resources/helpers_contract.json` | חוזה API מתנדבים/עוזרים לבדיקות אנדרואיד | test_fixture | test_fixture | 63 KB | 1 files | n.a. | 2026-11-08 | android HelpersContractTest.kt, HelpersApiAndOsrmTest.kt | **in_use** |  |
| `android/app/src/test/resources/safe_places_parity.json` | תאימות בחירת מקום בטוח Python<->Kotlin | test_fixture | test_fixture | 68 KB | 1 files | n.a. | 2026-11-08 | android SafePlacesParityTest.kt | **in_use** | לייצר מחדש אחרי כל בנייה של safe_places_il.json |

### זיהוי מצוקה אקוסטי

16 נכסים · בשימוש: 12, כפול/מיותר: 3, רלוונטי, לא בשימוש: 1

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `models/distress_head.npz` | ראש סיווג מצוקה (רגרסיה לוגיסטית מעל YAMNet, lr_ctx_s2 + העשרה) | runtime | model_weights | 40 KB | 3072 weights (w) | n.a. | 2026-11-07 | acoustic_ai_module.py:load (Windows); research_tools/multilingual_eval.py | **in_use** | לאמן מחדש על קטעי CC0+CC-BY בלבד (הרישיון לכל קטע ב-FSD50K metadata) לפני הפצה מסחרית; לא נארז ב-LifeGuard.spec (וגם YAMNet לא) - ב-EXE הזיהוי האקוסטי נופל לעוצמת רעש |
| `research/models/yamnet` | YAMNet (TFLite) - הטמעות קול + מפת מחלקות | runtime | model_weights | 20.2 MB | 3 files | n.a. | 2026-11-05 | acoustic_ai_module.py:load (yamnet_float_embeddings.tflite); research_tools/yamnet_detector.py | **in_use** | להעביר את yamnet_float_embeddings.tflite ל-models/ ולארוז ב-spec; yamnet_classification.tflite (4.1MB) לא מוזכר בקוד - לא לארוז |
| `research/models/distress_lr_fsd50k.npz` | מודל LR מוקדם (benchmark FSD50K) | research_eval | model_weights | 14 KB | 1024 weights (w) | n.a. | 2026-11-06 | research_tools/distress_benchmark.py; research_tools/multilingual_eval.py (השוואה) | **redundant** | הוחלף ע"י models/distress_head.npz; להשאיר רק לשחזור benchmark (14KB) |
| `research/models/testclips` | 3 קטעי ESC-50 + esc50.csv לבדיקות YAMNet | test_fixture | test_fixture | 1.4 MB | 3 .wav files | n.a. | 2026-11-05 | tests/test_yamnet_detector.py | **in_use** | בדיקות בלבד - לא לארוז |
| `research/samples/fsd50k/raw` | FSD50K גולמי (zip מפוצל dev+eval) | raw_dataset | research_audio | 24.68 GB | 51,197 clips (dev 40,966 + eval 10,231) | n.a. | 2026-11-06 | research_tools/distress_benchmark.py (חילוץ חד-פעמי) | **redundant** | 24.7GB - התכונות כבר חולצו (*_features.npz). לארכב לכונן חיצוני (ניתן להורדה מחדש מ-Zenodo, dev_md5.txt לאימות) |
| `research/samples/fsd50k/(features)` | תכונות YAMNet מאוחדות (dev/eval, נקי+רועש+העשרה) | build_input | generated_cache | 2.26 GB | 5 files | n.a. | 2026-11-06 | research_tools/distress_experiments.py (אימון distress_head) | **in_use** | זה מה שנחוץ לאימון מחדש - לשמור (2.3GB) |
| `research/samples/fsd50k/cache` | מטמון תכונות YAMNet לכל קטע (67,400 קבצי npz) | build_input | generated_cache | 2.29 GB | 67400 files | n.a. | 2026-11-06 | research_tools/distress_benchmark.py:extract_features (רק כשאין *_features.npz) | **redundant** | 2.3GB ב-67K קבצים - כפילות של *_features.npz (load_features קורא את הקובץ המאוחד קודם). לארכב/למחוק אחרי אישור; חוסך גם זמן סריקה/גיבוי |
| `research/samples/fsd50k/meta` | FSD50K ground truth + metadata (תוויות ורישיון לכל קטע) | build_input | research_audio | 35.6 MB | 11 files | n.a. | 2020-09-24 | research_tools/distress_benchmark.py:load_index | **in_use** | נחוץ לסינון קטעי NC באימון מחדש מותר-מסחרית - לשמור |
| `research/samples/fleurs_he/dev` | FLEURS עברית dev - קטעי wav + תמלילים + תוצאות הערכת ASR | research_eval | research_audio | 189.7 MB | 328 .wav files | n.a. | 2026-11-06 | research_tools/local_asr_eval.py | **relevant_not_used** | test.tsv - אין לו אודיו כאן (האודיו של test נמצא ב-research_data/audio/fleurs/he_il) |
| `research_data/audio/fleurs` | FLEURS test - he/ar/ru/am/en (שליליים: דיבור רגוע) - בהורדה | research_eval | research_audio | 9.35 GB | 20 files | n.a. | 2026-11-11 | research_tools/multilingual_eval.py (לא במאגר עדיין) | **in_use** | בהורדה פעילה (סוכן אחר) - הגודל ישתנה; בסיום לשקול חילוץ חד-פעמי לתכונות ולארכב את ה-tar |
| `research_data/audio/vivae` | VIVAE - קולות רגש לא-מילוליים (פחד/כאב/כעס) - חיוביים | research_eval | research_audio | 93.5 MB | 1,085 files | n.a. | 2026-11-11 | research_tools/multilingual_eval.py | **in_use** | הערכה בלבד - לא לאמן מודל מסחרי עליו |
| `research_data/audio/cremad` | CREMA-D - דיבור רגשי באנגלית (91 שחקנים) | research_eval | research_audio | 470.8 MB | 7,442 clips | n.a. | 2026-11-11 | research_tools/multilingual_eval.py | **in_use** |  |
| `research_data/audio/resd` | RESD - דיאלוגים רגשיים ברוסית (test) | research_eval | research_audio | 94.6 MB | 280 clips | n.a. | 2026-11-11 | research_tools/multilingual_eval.py | **in_use** |  |
| `research_data/audio/arabic_emotional_demo` | DataHive - דיבור רגשי בערבית (דמו, שעה) | research_eval | research_audio | 257.0 MB | 496 utterances | n.a. | 2026-11-11 | research_tools/multilingual_eval.py | **in_use** | הערכה בלבד; רישוי מסחרי רק מול הספק |
| `research_data/audio/cache` | מטמון תכונות YAMNet לקטעי ההערכה הרב-לשונית | build_input | generated_cache | 234.7 MB | 11315 files | n.a. | 2026-11-11 | research_tools/multilingual_eval.py features | **in_use** | בבנייה פעילה |
| `research_data/audio/scores` | ציונים, תמלולים ותוצאות ההערכה הרב-לשונית + MANIFEST | research_output | generated_cache | 5.5 MB | 26 files | n.a. | 2026-11-11 | research_tools/multilingual_eval.py report | **in_use** | בבנייה פעילה |

### מילות מפתח / תמלול (ASR)

12 נכסים · בשימוש: 9, כפול/מיותר: 2, לא ברור: 1

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `research/models/asr/zipformer2-66m-streaming-he` | מודל תמלול עברית מקומי (Zipformer streaming int8) | runtime | model_weights | 72.3 MB | 5 files | n.a. | 2026-11-06 | speech_local.py (MODELS['he']); voice_trigger_module.py; voice_input.py; first_aid_ui.py | **in_use** | לוודא שהייחוס מוצג במסך אודות; מורד ומאומת (sha1) ע"י speech_local |
| `research/models/asr/sherpa-onnx-streaming-zipformer-en-2023-06-26` | מודל תמלול אנגלית מקומי | runtime | model_weights | 72.7 MB | 5 files | n.a. | 2026-11-06 | speech_local.py (MODELS['en']) | **in_use** |  |
| `research/models/asr/vosk-model-small-streaming-ru` | מודל תמלול רוסית מקומי | runtime | model_weights | 28.6 MB | 5 files | n.a. | 2026-11-06 | speech_local.py (MODELS['ru']) | **in_use** |  |
| `research/models/asr/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17` | מודל אנגלית קטן (נפסל) | research_eval | model_weights | 43.6 MB | 5 files | n.a. | 2026-11-06 | אף קוד (speech_local.py: 'נבדק ונפסל') | **redundant** | מועמד לארכוב/מחיקה (44MB) - הוחלף ע"י en-2023-06-26 |
| `research/models/oww` | openWakeWord: melspectrogram + embedding דיבור (ONNX) | runtime | model_weights | 2.4 MB | 2 files | n.a. | 2026-11-06 | personal_kws.py (OWW_DIRS, מילות מפתח אישיות) | **in_use** | להחליף במחלץ תכונות ברישיון מתירני (למשל הטמעות YAMNet שכבר קיימות, Apache-2.0) לפני שחרור מסחרי |
| `research/samples/fleurs_he/dev.tar.gz` | FLEURS עברית dev - ארכיון מקורי | raw_dataset | research_audio | 152.9 MB | 328 clips | n.a. | 2026-11-06 | research_tools/local_asr_eval.py (חולץ ל-dev/) | **redundant** | כפילות של dev/*.wav שחולצו ממנו (154MB) - להשאיר אחד מהשניים |
| `research/samples/fleurs_he/dev` | FLEURS עברית dev - קטעי wav + תמלילים + תוצאות הערכת ASR | research_eval | research_audio | 189.7 MB | 328 .wav files | n.a. | 2026-11-06 | research_tools/local_asr_eval.py | **in_use** | test.tsv - אין לו אודיו כאן (האודיו של test נמצא ב-research_data/audio/fleurs/he_il) |
| `research/samples/asr_he` | קטע FLEURS יחיד ("לפי המשטרה...") לבדיקת ASR | test_fixture | test_fixture | 233 KB | 1 .wav files | n.a. | 2026-11-06 | tests/test_speech_local.py | **in_use** |  |
| `research/samples/asr_tts` | 30 משפטי מצוקה מסונתזים (Windows TTS) he/en | research_eval | research_audio | 2.1 MB | 30 .wav files | n.a. | 2026-11-06 | research_tools/local_asr_eval.py | **in_use** | דיבור סינתטי - לא מייצג קול אמיתי במצוקה; לא להפיץ |
| `research/samples/asr_sanity` | קטעי en/ru לבדיקת שפיות ASR | research_eval | research_audio | 439 KB | 2 .wav files | n.a. | 2026-11-06 | אף קוד במאגר | **unclear** | לא מוזכר בקוד ומקור/רישיון לא מתועדים - לתעד או לארכב |
| `research_data/audio/fleurs` | FLEURS test - he/ar/ru/am/en (שליליים: דיבור רגוע) - בהורדה | research_eval | research_audio | 9.35 GB | 20 files | n.a. | 2026-11-11 | research_tools/multilingual_eval.py (לא במאגר עדיין) | **in_use** | בהורדה פעילה (סוכן אחר) - הגודל ישתנה; בסיום לשקול חילוץ חד-פעמי לתכונות ולארכב את ה-tar |
| `research_data/audio/scores` | ציונים, תמלולים ותוצאות ההערכה הרב-לשונית + MANIFEST | research_output | generated_cache | 5.5 MB | 26 files | n.a. | 2026-11-11 | research_tools/multilingual_eval.py report | **in_use** | בבנייה פעילה |

### עזרה ראשונה והנחיות חירום

8 נכסים · בשימוש: 6, רלוונטי, לא בשימוש: 1, כפול/מיותר: 1

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `research/models/asr/zipformer2-66m-streaming-he` | מודל תמלול עברית מקומי (Zipformer streaming int8) | runtime | model_weights | 72.3 MB | 5 files | n.a. | 2026-11-06 | speech_local.py (MODELS['he']); voice_trigger_module.py; voice_input.py; first_aid_ui.py | **in_use** | לוודא שהייחוס מוצג במסך אודות; מורד ומאומת (sha1) ע"י speech_local |
| `first_aid/content/first_aid_guides_he.json` | 39 מדריכי עזרה ראשונה בעברית (אופליין) | runtime | curated_content | 89 KB | 39 guides | n.a. | 2026-11-07 | first_aid/library.py:load; android FirstAidLibrary.kt; LifeGuard.spec | **in_use** | medical_review_required=true - חובה סקירה רפואית לפני שחרור. החיפוש (Library.search) כבד יחסית (פי ~3 מטעינת הספרייה כולה לשאילתה) - לשקול מטמון/אינדקס מראש |
| `first_aid/content/search_index_he.json` | אינדקס חיפוש מוכן מראש לעזרה ראשונה | runtime | generated_cache | 15 KB | 1 files | n.a. | 2026-11-07 | רק tools/build_first_aid.py (בדיקת תאימות); library.py בונה אינדקס בזיכרון ולא קורא אותו | **relevant_not_used** | נארז באפליקציה ולא נקרא. או לטעון אותו ב-Library (חוסך בניית אינדקס בכל הפעלה) או להוציא מהאריזה |
| `first_aid/content/sources.json` | מקורות, מילים נרדפות (ערבית/אנגלית), מדיניות הרחבות, הרחבה פעילה | runtime | curated_content | 16 KB | 4 files | n.a. | 2026-11-07 | first_aid/library.py:load; first_aid/supplements.py; android Supplements.kt/FirstAidLibrary.kt | **in_use** |  |
| `first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/first_aid_supplement_he.json` | חבילת הרחבה 1.2.0 - 45 מדריכים נוספים | runtime | curated_content | 91 KB | 45 guides | n.a. | 2026-11-07 | first_aid/supplements.py; android Supplements.kt | **in_use** |  |
| `first_aid/content/first_aid_guides_he.md` | עותקי Markdown קריאים + תיעוד/פרומפט של חבילות העזרה הראשונה | docs | curated_content | 162 KB | 9 files | n.a. | 2026-11-07 | אף קוד ריצה (נארז כי LifeGuard.spec/Gradle אורזים את content/**) | **redundant** | ~150KB של md + CLAUDE_SUPPLEMENT_PROMPT.md נארזים באפליקציה בלי שימוש - להחריג *.md מה-spec ומ-firstAidIncludes ב-Gradle (לשמור במאגר) |
| `content/emergency_guidance_he.json` | הנחיות חירום (10 תרחישים, 12 מקורות) - מרכז הבטיחות | runtime | curated_content | 19 KB | 10 scenarios | n.a. | 2026-11-04 | guidance_engine.py; LifeGuard.spec | **in_use** | review_status: לא נבדק ע"י גורם מוסמך - חובה סקירה לפני שחרור; גרסה 2026.11.04 |
| `android/app/src/test/resources/first_aid_parity.json` | תאימות עזרה ראשונה Python<->Kotlin | test_fixture | test_fixture | 12 KB | 1 files | n.a. | 2026-11-07 | android FirstAidParityTest.kt | **in_use** | אין סקריפט מחולל ב-tools/ (בניגוד לשני האחרים) - להוסיף tools/build_first_aid_parity.py |

### שכבת חדשות

4 נכסים · מיושן: 2, בשימוש: 2

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `research/samples/news_intel/news_intel_2026-11-08T1157.json` | צילומי שכבת חדשות (4 ריצות, 08/11) - קלט ל-LIFEGUARD_NEWS_FILE | runtime_optional | news_unverified | 247 KB | 21 items | city / street (geocoded, variable) | 2026-11-08 | server/news_layer.py:NewsLayer.from_env (רק אם LIFEGUARD_NEWS_FILE מוגדר); server/safety_intel.py:_news | **stale** | ידיעות דועכות תוך ימים - הצילום מ-08/11 כבר לא רלוונטי; 3 מ-4 הצילומים מיותרים. לייצר מחדש בתזמון (cron) רק אחרי אישור מפרסמים |
| `research/samples/news_intel/gazetteer.json.gz` | גזטיר מקומות (OSM) + מאגר פריטים מצטבר של news_intel | build_input | generated_cache | 183 KB | 2 files | city / street | 2026-11-08 | research_tools/news_intel.py (GAZETTEER_PATH, STORE_PATH) | **in_use** |  |
| `research/samples/news_intel/labels_dev_2026-11-08.json` | תיוג ידני לאימות news_intel (dev + held-out) | research_eval | estimate | 25 KB | 2 files | n.a. | 2026-11-08 | python -m research_tools.news_intel eval --labels | **in_use** |  |
| `research/samples/news_intel/cache` | מטמון HTTP של news_intel (RSS, GDELT, robots, Overpass, Nominatim) | build_input | generated_cache | 526 KB | 32 files | n.a. | 2026-11-08 | research_tools/news_intel.py (מטמון) | **stale** | מטמון חדשות ישן - ניתן לנקות לפני הריצה הבאה |

### ארצי / התרעות (מקלטים)

3 נכסים · בשימוש: 3

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `models/safety_layers.json.gz` | שכבות רשמיות: מוקדי תאונות הולכי רגל (רשת) + מקלטים ציבוריים | runtime | official_verified | 53 KB | 7207 accidents.cells / 858 shelters | street (~100 m cell); shelters: point | 2026-11-07 | server/safety_intel.py:Layers.load, SafetyIntelMixin._layers; server/national.py (layers.shelters_near) | **in_use** | לחשב את סף המוקד (Layers._hotspot_threshold) בזמן הבנייה ולשמור בקובץ - כ-40% מזמן הטעינה; אינדקס רשת למקלטים במקום סריקה ליניארית של 858 בכל קריאה; לשקול מקלטים כקטגוריה בחיפוש מקום בטוח בזמן התרעה |
| `research/samples/israel/cache` | מטמון API גולמי: משטרה, למ"ס, GIS ת"א/חיפה, תאורה ומקלטים ב"ש | build_input | generated_cache | 28.6 MB | 65 files | mixed | 2026-11-06 | research_tools/israel_layer.py --offline; research_tools/build_safety_layers.py --offline | **in_use** | קלט לבנייה אופליין בלבד; ~20 עמודי police_crime של 1.48MB - לשמור עד הבנייה הבאה |
| `content/emergency_guidance_he.json` | הנחיות חירום (10 תרחישים, 12 מקורות) - מרכז הבטיחות | runtime | curated_content | 19 KB | 10 scenarios | n.a. | 2026-11-04 | guidance_engine.py; LifeGuard.spec | **in_use** | review_status: לא נבדק ע"י גורם מוסמך - חובה סקירה לפני שחרור; גרסה 2026.11.04 |

### לא שייך לאף מנוע (מצב ריצה / ראיות)

| נתיב | תיאור | תפקיד | סוג | גודל | רשומות | דיוק | נבנה | בשימוש ע"י (קוד) | פסיקה | המלצה |
|---|---|---|---|---|---|---|---|---|---|---|
| `research/datasets/neighborhood_crime_signal.csv` | טבלת אות פשיעה שכונתי שסופקה ע"י המשתמש (19 שורות) | research_eval | user_report | 8 KB | 19 rows (neighborhood_crime_signal.csv) | neighbourhood (estimated boundary) | 2026-11-05 | research_tools/israel_layer.py:crosscheck_user_table | **not_relevant** | לא להזין למנוע - הדו"ח (§3.2) מצא שהיחס בטבלה אינו יחס לחציון העיר; משמש רק להצלבה |
| `research/samples/israel/(probes)` | דגימות בדיקת API (first5/count) לתיעוד data_inventory_israel.md | docs | official_verified | 180 KB | 34 files | n.a. | 2026-11-05 | אף קוד (ראיות לתיעוד) | **not_relevant** | ראיות בלבד (קטן מאוד) - אפשר לארכב יחד עם הדו"ח |
| `research/samples/fsd50k/(logs+results)` | יומני ותוצאות benchmark/holdout של FSD50K | research_output | generated_cache | 427 KB | 8 files | n.a. | 2026-11-07 | אף קוד (ראיות ל-acoustic_benchmark_fsd50k.md, acoustic_model_holdout.md) | **not_relevant** | ראיות - קטן, לשמור |
| `community.db` | מסד קהילתי ישן בשורש המאגר (incidents) | runtime_state | runtime_state | 37 KB | memberships=0, incidents=73, acknowledgements=0 | n.a. | 2026-11-04 | אף קוד במאגר (gitignored ע"י *.db) | **not_relevant** | לא בשימוש. להעביר ל-data/ או למחוק אחרי אישור המשתמש; לעולם לא לארוז |
| `server/server_data` | מצב ריצה של שרת הפיתוח המקומי (DB, WAL, מפתח הצפנה, לוג) | runtime_state | runtime_state | 4.6 MB | not opened (runtime state) | n.a. | 2026-11-11 | server/lifeguard_server.py, server/data_sources.py (מטמון) | **not_relevant** | לא דאטהסט - לא נפתח. ה-WAL (4.1MB) גדול פי 8 מה-DB: checkpoint בכיבוי השרת; data.key לעולם לא לארוז/לשתף |

## 3. דגלי רישיון ומה אסור לארוז

**לא מסחרי (NC). חוסם שחרור מסחרי של מה שנגזר מהם:**
- `models/distress_head.npz`: **נמצא ב-git ונטען באפליקציה**. אומן על FSD50K שכולל קטעים ב-CC-BY-NC וב-Sampling+. בתוך המודל יש `license_note`: "non-commercial research use".
- `research/models/oww`: קובצי המודל של openWakeWord הם CC BY-NC-SA 4.0. הם נטענים ע"י `personal_kws.py` (מילות מפתח אישיות).
- ESC-50 (`testclips`): לבדיקות בלבד.
- VIVAE ו-DataHive Arabic demo: להערכה בלבד.

**Share-alike (ODbL / BY-SA). מותר שימוש מסחרי, אבל מאגר נגזר חייב לצאת באותו רישיון ועם ייחוס:**
- `safe_places_il.json`: **נארז באפליקציה**. צריך להציג "© OpenStreetMap contributors".
- `risk_factors_grid`, הגזטיר של news_intel, CREMA-D, מקלטי ירושלים, חלק מתמונות `street_sanity`.

**רישיון לא אומת:**
- שרתי ה-GIS של תל אביב (תאורה, מקלטים, gisn) ושל חיפה.
- זו הסיבה ש-`street_lights_grid`, `safety_layers` ו-`risk_factors_grid` נמצאים ב-gitignore ונבנים מקומית. כך צריך להישאר עד שהרישיון יאומת.

**רישיון לא ידוע:** `neighborhood_crime_signal.csv`, ‏`asr_sanity`, ‏`asr_tts` (פלט TTS של Microsoft).

**זכויות יוצרים:** פריטי חדשות. לפי `news_intel_report.md`, צריך אישור בכתב מכל מפרסם לפני הפעלה בייצור.

**ייחוס חובה:**
- מודל ה-ASR העברי (CC-BY-4.0, Yakov Kolani / ivrit.ai).
- FLEURS (CC-BY-4.0).
- הלמ"ס, משטרת ישראל, data.gov.il.

**סקירה מקצועית חסרה:** `first_aid_guides_he.json` וחבילת ההרחבה מסומנים `medical_review_required=true`, ו-`emergency_guidance_he.json` מסומן "לא נבדק ע"י גורם מוסמך".

**אסור שייכנס לאפליקציה או ל-build לעולם:**
- `server/server_data/` (כולל `data.key` ומסד המשתמשים).
- `community.db`.
- `data/`.
- `research_data/**` ו-`research/samples/**`.
- `research/models/oww` (בגלל NC).
- `research/models/clip` (156 MB, מחקר בלבד).

| נתיב | רישיון | דגל | נארז באפליקציה? |
|---|---|---|---|
| `models/safety_layers.json.gz` | הלמ"ס 2.1 + data.gov.il Open (מסחרי מותר, ייחוס); ירושלים ODbL; GIS ת"א - רישיון השרת לא אומת (גם חיפה) | **unverified (TLV/Haifa GIS); share-alike (Jerusalem ODbL)** | server only (gitignored - build locally) |
| `models/street_lights_grid.json.gz` | ב"ש: data.gov.il Open; GIS ת"א - רישיון השרת לא אומת | **unverified (TLV GIS)** | server + Windows (gitignored - build locally) |
| `models/risk_factors_grid.json.gz` | OSM ODbL 1.0 (share-alike למאגר נגזר); ת"א gisn - לא אומת; משטרה/הלמ"ס data.gov.il Open | **share-alike (ODbL); unverified (TLV gisn)** | server only (gitignored - build locally) |
| `models/distress_head.npz` | נגזר מ-FSD50K (CC0/CC-BY/CC-BY-NC/Sampling+); license_note במודל: non-commercial research use | **NC-derived; tracked in git** | in git; not in LifeGuard.spec |
| `research/models/distress_lr_fsd50k.npz` | נגזר מ-FSD50K (כולל CC-BY-NC) | **NC-derived** | no |
| `research/models/asr/zipformer2-66m-streaming-he` | CC-BY-4.0 | **attribution required** | downloaded on demand (sha1-verified) |
| `research/models/oww` | קוד Apache-2.0; קובצי המודל CC BY-NC-SA 4.0 (personal_kws.py, acoustic_inventory.md) | **NC + share-alike - blocks commercial release** | no (gitignored) - must not ship commercially |
| `research/models/testclips` | CC BY-NC 3.0 | **NC (tests only)** | no |
| `research/datasets/neighborhood_crime_signal.csv` | unknown | **unknown licence** | no (tracked in git) |
| `research/samples/israel/cache` | לפי המקור (data.gov.il Open / CBS 2.1 / GIS לא אומת) | **unverified (TLV/Haifa GIS)** | no (gitignored, research) |
| `research/samples/israel/derived/streetlights_tel_aviv.csv` | ב"ש Open; GIS ת"א - רישיון השרת לא אומת | **unverified (TLV GIS)** | no (gitignored, research) |
| `research/samples/risk_factors/cache` | OSM ODbL 1.0 (share-alike למאגר נגזר); ת"א gisn לא אומת | **share-alike (ODbL); unverified (TLV gisn)** | no (gitignored, research) |
| `research/samples/risk_factors/(outputs)` | הלמ"ס + OSM ODbL 1.0 (share-alike למאגר נגזר) | **share-alike (ODbL)** | no (gitignored, research) |
| `research/samples/news_intel/news_intel_2026-11-08T1157.json` | זכויות יוצרים של המפרסמים; נדרש אישור בכתב לפני ייצור (news_intel_report.md) | **copyright - needs publisher permission** | no |
| `research/samples/news_intel/gazetteer.json.gz` | OSM ODbL 1.0 (share-alike למאגר נגזר); פריטים: זכויות מפרסמים | **share-alike (ODbL); copyright (items)** | no (gitignored, research) |
| `research/samples/news_intel/cache` | לפי המקור | **copyright (RSS)** | no (gitignored, research) |
| `research/samples/fsd50k/raw` | CC-BY (דאטהסט); לכל קטע: CC0 / CC-BY / CC-BY-NC / Sampling+ | **NC + Sampling+ clips** | no |
| `research/samples/fsd50k/(features)` | נגזר מ-FSD50K (כולל NC) | **NC-derived** | no |
| `research/samples/fsd50k/cache` | נגזר מ-FSD50K (כולל NC) | **NC-derived** | no |
| `research/samples/fleurs_he/dev.tar.gz` | CC-BY-4.0 | **attribution required** | no |
| `research/samples/fleurs_he/dev` | CC-BY-4.0 | **attribution required** | no |
| `research/samples/asr_he` | CC-BY-4.0 | **attribution required** | no |
| `research/samples/asr_tts` | unknown (פלט TTS של Microsoft) | **unknown licence** | no |
| `research/samples/asr_sanity` | unknown | **unknown licence** | no |
| `research/samples/street_sanity` | CC BY-SA 4.0 / CC BY 3.0 לפי תמונה | **share-alike (some images)** | no |
| `research_data/audio/fleurs` | CC-BY-4.0 | **attribution required** | no |
| `research_data/audio/vivae` | CC-BY-NC-4.0 | **NC** | no |
| `research_data/audio/cremad` | ODbL + DbCL | **share-alike (ODbL)** | no |
| `research_data/audio/arabic_emotional_demo` | CC-BY-NC-4.0 | **NC** | no |
| `research_data/audio/cache` | נגזר (לפי הדאטהסטים) | **NC-derived (partly)** | no |
| `safe_places/safe_places_il.json` | data.gov.il Open; OSM ODbL 1.0 (share-alike למאגר נגזר) | **share-alike (ODbL)** | yes (Windows EXE + Android assets) |
| `first_aid/content/first_aid_guides_he.json` | תוכן הפרויקט; מקורות מצוטטים | **medical review pending** | yes |
| `first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/first_aid_supplement_he.json` | תוכן הפרויקט; מקורות מצוטטים | **medical review pending** | yes |
| `content/emergency_guidance_he.json` | תוכן הפרויקט; מקורות מצוטטים | **professional review pending** | yes |
| `community.db` | n.a. (ייתכן מידע אישי) | **possible personal data** | never |
| `server/server_data` | n.a. (נתוני משתמשים + data.key) | **secret key + user data** | never |

## 4. איך לבנות מחדש כל נכס נגזר

כל הפקודות רצות משורש המאגר, עם `.venv\Scripts\python.exe`. רובן צריכות רשת. אפשר להוסיף `--offline` כדי לבנות מה-cache המקומי.

סדר מומלץ לבניית שכבות הריצה:
1. `python -m research_tools.israel_layer build`: מייצר את קבצי ה-derived (תאונות, עמודי תאורה, פשיעה).
2. `python -m research_tools.build_light_grid`.
3. `python -m research_tools.build_safety_layers`.
4. `python -m research_tools.risk_factors build`, ואז `python -m research_tools.risk_factors validate`.
5. `python -m tools.build_safe_places`, ואז `python -m tools.build_safe_places_parity`.
6. `python -m tools.build_helpers_contract`.
7. `python -m tools.build_data_catalog`.

| נכס | פקודה |
|---|---|
| `models/safety_layers.json.gz` | `python -m research_tools.build_safety_layers [--offline]` |
| `models/street_lights_grid.json.gz` | `python -m research_tools.israel_layer build (lights) && python -m research_tools.build_light_grid` |
| `models/risk_factors_grid.json.gz` | `python -m research_tools.risk_factors build [--offline]` |
| `models/distress_head.npz` | `python -m research_tools.distress_experiments --holdout lr_ctx_s2` |
| `research/models/yamnet` | `הורדה ידנית מ-Kaggle (research/acoustic_inventory.md)` |
| `research/models/distress_lr_fsd50k.npz` | `python -m research_tools.distress_benchmark --fsd50k research/samples/fsd50k` |
| `research/models/asr/zipformer2-66m-streaming-he` | `speech_local מוריד ומאמת` |
| `research/models/asr/sherpa-onnx-streaming-zipformer-en-2023-06-26` | `speech_local מוריד ומאמת` |
| `research/models/asr/vosk-model-small-streaming-ru` | `speech_local מוריד ומאמת` |
| `research/models/oww` | `הורדה ידנית (openWakeWord releases)` |
| `research/models/clip` | `הורדה ידנית (research/acoustic_inventory.md / street_vision.py)` |
| `research/models/yunet` | `הורדה ידנית` |
| `research/models/testclips` | `הורדה ידנית מ-ESC-50` |
| `research/datasets/crime_by_stat_area_summary.csv` | `python -m research_tools.israel_layer build` |
| `research/samples/israel/cache` | `python -m research_tools.israel_layer build (רשת)` |
| `research/samples/israel/derived/pedestrian_accidents_2020-2024.geojson` | `python -m research_tools.israel_layer build` |
| `research/samples/israel/derived/streetlights_tel_aviv.csv` | `python -m research_tools.israel_layer build` |
| `research/samples/israel/derived/crime_units_all_2024-2025.csv` | `python -m research_tools.israel_layer build` |
| `research/samples/risk_factors/cache` | `python -m research_tools.risk_factors build (רשת)` |
| `research/samples/risk_factors/(outputs)` | `python -m research_tools.risk_factors build && python -m research_tools.risk_factors validate` |
| `research/samples/news_intel/news_intel_2026-11-08T1157.json` | `python -m research_tools.news_intel run [--nominatim]` |
| `research/samples/news_intel/gazetteer.json.gz` | `python -m research_tools.news_intel gazetteer` |
| `research/samples/news_intel/labels_dev_2026-11-08.json` | `python -m research_tools.news_intel label-template` |
| `research/samples/news_intel/cache` | `נבנה מחדש אוטומטית בריצת news_intel run` |
| `research/samples/fsd50k/raw` | `הורדה מ-Zenodo 4060432` |
| `research/samples/fsd50k/(features)` | `python -m research_tools.distress_experiments (בונה מה-cache/raw)` |
| `research/samples/fsd50k/cache` | `python -m research_tools.distress_benchmark` |
| `research/samples/fsd50k/meta` | `חילוץ FSD50K.metadata.zip / ground_truth.zip` |
| `research/samples/fsd50k/(logs+results)` | `python -m research_tools.distress_benchmark / distress_experiments --holdout` |
| `research/samples/fleurs_he/dev.tar.gz` | `הורדה מ-HF google/fleurs` |
| `research/samples/fleurs_he/dev` | `python -m research_tools.local_asr_eval` |
| `research/samples/asr_he` | `python -m research_tools.local_asr_eval --export-test-clip` |
| `research/samples/asr_tts` | `python -m research_tools.local_asr_eval` |
| `research/samples/street_sanity` | `ראו sources.json` |
| `research_data/audio/fleurs` | `research_tools/multilingual_eval.py (MANIFEST.json)` |
| `research_data/audio/vivae` | `MANIFEST.json` |
| `research_data/audio/cremad` | `MANIFEST.json` |
| `research_data/audio/resd` | `MANIFEST.json` |
| `research_data/audio/arabic_emotional_demo` | `MANIFEST.json` |
| `research_data/audio/cache` | `python -m research_tools.multilingual_eval features` |
| `research_data/audio/scores` | `python -m research_tools.multilingual_eval asr/report` |
| `safe_places/safe_places_il.json` | `python -m tools.build_safe_places [--no-overpass]` |
| `first_aid/content/first_aid_guides_he.json` | `python -m tools.build_first_aid --from <pack>` |
| `first_aid/content/search_index_he.json` | `python -m tools.build_first_aid` |
| `first_aid/content/sources.json` | `python -m tools.build_first_aid --from <pack>` |
| `first_aid/content/supplements/lifeguard_first_aid_supplement_02/1.2.0/first_aid_supplement_he.json` | `tools/build_first_aid.py (חבילת הרחבה)` |
| `content/emergency_guidance_he.json` | `עריכה ידנית` |
| `android/app/src/test/resources/first_aid_parity.json` | `לא מתועד (נוצר מ-first_aid/library.py)` |
| `android/app/src/test/resources/helpers_contract.json` | `python -m tools.build_helpers_contract` |
| `android/app/src/test/resources/safe_places_parity.json` | `python -m tools.build_safe_places_parity` |
