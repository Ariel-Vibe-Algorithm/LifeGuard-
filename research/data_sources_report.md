# מקורות נתונים חיצוניים - סקר חי, תנאים ושילוב (2026-11-11)

> מטרה: לשלב רק מקורות **חינמיים**, אחרי בדיקה של תנאים, מגבלות קצב, רישיון וכיסוי בישראל.
> "שכבה חינמית" ≠ "מותר לייצור מסחרי". שום נתון פשיעה/תאורה/בטיחות לא מומצא; כל נתון מסומן במקור,
> בסוג (רשמי מאומת / פתוח-קהילתי / הערכה / דיווח משתמש / חדשות לא מאומתות) ובאמינות.
>
> הסקר רץ מהמחשב הזה ב-2026-11-11 בין 13:55 ל-14:05 UTC, עם User-Agent כללי `LifeGuard-Research/1.0`,
> בלי פרטים אישיים, בקצב של בקשה לכל 1-1.5 שנ' לכל שירות. לא נרשמנו לשום שירות ולא הפעלנו שום מפתח.
> נקודות בדיקה: תל אביב 32.0853,34.7818; ירושלים 31.7767,35.2345; באר שבע 31.2518,34.7913.
> סקריפטי הסקר: scratchpad של הסשן (probe1/2/3.py). הם לא חלק מהמאגר.

## 1. טבלת הכרעה

| מקור | שמיש בישראל היום? | מפתח? | מסחרי מותר? | מגבלת קצב | ייחוס | ראיה (2026-11-11) |
|---|---|---|---|---|---|---|
| **Overpass** (overpass-api.de + 3 מראות) | **לא אמין**. כל 4 השרתים נכשלו | לא | לא מצוין (unknown). לשימוש כבד: שרת עצמי | ~10,000 בקשות/יום, ~1GB/יום, 429 בחריגה | © OpenStreetMap contributors (ODbL) | overpass-api.de: 502 "at capacity" ב-3/3 + `/api/status` 502. private.coffee ו-kumi.systems: timeout של 30 שנ' ב-3/3. maps.mail.ru: 504, 504, ורק ב"ש 200 (10 אלמנטים, 20.9 שנ'). ניסיון חוזר ב-14:02: אותה תוצאה |
| **OSRM demo** router.project-osrm.org | **לא להליכה** | לא | אסור למכור גישה. לא רלוונטי | "שימוש מופרז אסור" | OSM + OSRM | **הטענה אומתה:** `/route/v1/foot/` ו-`/route/v1/driving/` החזירו מסלול זהה: 3,292 מ', 330 שנ' (35.9 קמ"ש). זה שרת רכב בלבד |
| **OSRM foot** routing.openstreetmap.de (FOSSGIS) | **כן** | לא | לא מצוין (unknown) | 1 בקשה/שנ', בלי scraping | OSM + FOSSGIS/OSRM | `routed-foot`: 200, 2,865 מ', 2,228 שנ' (4.6 קמ"ש = הליכה אמיתית). `routed-car` לאותו זוג: 3,292 מ' / 330 שנ' |
| **data.gov.il CKAN** | **כן** (API אנונימי) | לא | **כן** (תנאי האתר: מסחרי מותר, ציון מקור) | לא מפורסם; דפדוף `datastore_search` | ציון המקור + הגוף המפרסם | `package_search` עבד ל-9 שאילתות (פירוט ב-§3). `datastore_search_sql` על טבלה החזיר 404 HTML, כלומר לא שמיש. דף התנאים מחזיר 403 ללקוח סקריפט (CloudFront) |
| **Mapillary** graph.mapillary.com | **לא נבדק** (אין token) | **כן** (`MAPILLARY_TOKEN`) | **מותנה** (תנאים §12: מסחרי רק למטרות מוגדרות) → unknown | חיפוש 10,000/דק', Entity 60,000/דק', אריחים 50,000/יום | לוגו Mapillary + קישור (§11). לכל תמונה: יוצר + CC BY-SA 4.0 | בלי token: **HTTP 500** (לא 401) `{"error":{"message":"Invalid OAuth 2.0 Access Token","type":"MLYApiException","code":190}}` |
| **Siren** api.siren.co.il | **השירות קיים**. נתוני התרעות לא נבדקו (אין מפתח) | **כן** (`LIFEGUARD_SIREN_KEY`, פרטי `pr_`) | **כן** (תנאים §17). אבל **לא כמקור יחיד/עיקרי** (§3) | מפתח פרטי: 1,000 בקשות / 15 דק'. ציבורי: 400 / 15 דק' לכל IP. Socket.IO: 30 handshakes/דק' | **חובה:** קרדיט גלוי עם קישור פעיל ל-siren.co.il (§9) | `GET /` החזיר 200 `{"status":"ok","name":"Siren API","version":"1.0.0"}`. `GET /active` בלי מפתח: **401** `{"error":"Missing API key"}`. www.siren.co.il: 503 |
| **Open-Meteo** | **כן** | לא | **לא**. ה-API החינמי לא-מסחרי בלבד | <10,000/יום, 5,000/שעה, 600/דק' | CC BY 4.0, "Weather data by Open-Meteo.com" | 200 ב-3/3 נקודות, `current` עדכני (15:45 שעון ישראל): ת"א 21.5°, ירושלים 17.1°, ב"ש 20.8° |
| **Nominatim** | **כן** | לא | לא אסור, אבל המדיניות מזהירה (unknown). לייצור: proxy עם מטמון או מופע עצמי | **מקסימום 1 בקשה/שנ' לכל האפליקציה**. בלי bulk, autocomplete או reverse על רשת. מטמון חובה. UA מזהה | ODbL, ייחוס גלוי | reverse החזיר 200 ב-3/3: ארלוזורוב ת"א, גשר המוגרבים ירושלים, חיים נחמן ביאליק ב"ש. search "דיזנגוף 50 תל אביב" החזיר 3 תוצאות, הראשונה "מגדל דיזנגוף, 50, דיזנגוף" |
| פיקוד העורף alerts.json (הנקודה הציבורית) | **לא מהמחשב הזה** | לא | אין הסכם API | - | - | **403** (דף שגיאה HTML, `<TITLE>ERROR`). לכן נשאר רק דרך מראה מורשית `LIFEGUARD_OREF_ALERTS_URL` |
| GDELT DOC API | **כן, בקצב איטי** | לא | **כן** ("unlimited ... commercial"), עם ציטוט וקישור | **בקשה אחת לכל 5 שנ'** (מתשובת 429) | GDELT Project + קישור | בקשה ראשונה: **429** "Please limit requests to one every 5 seconds" (17 שנ') |
| GIS ת"א, שכבה 543 (עמודי תאורה) | **כן** | לא | לא אומת שרישיון ה-Open Data חל על שרת ה-GIS | - | עיריית ת"א-יפו | 200. **4,679 עמודים** בתיבה של ~1 ק"מ סביב נקודת ת"א |
| GIS ת"א, שכבה 592 (מקלטים) / GIS חיפה (מקלטים) | **כן** | לא | unknown | - | העירייה | 200. ת"א **382**, חיפה **216** |
| data.gov.il street-light-br7 (ב"ש) | **כן** | לא | כן | - | עיריית באר שבע | datastore: **29,867** עמודים עם lat/lon |
| CBS ArcGIS Online | **כן** | לא | כן (רישיון הלמ"ס 2.1) | - | הלמ"ס | 200, 66 שירותים |

**מסקנה בקצרה:**
- שמישים היום, בלי מפתח: data.gov.il, OSRM-foot של FOSSGIS, Nominatim (בכפוף למדיניות), Open-Meteo (לא-מסחרי), שכבות GIS עירוניות.
- דורשים מפתח: Mapillary, Siren.
- לא אמינים היום: Overpass (כל המראות).
- לא שמיש להליכה: OSRM demo.

## 2. מה נבנה בקוד

- **`server/data_sources.py`**:
  - **רישום** של 16 מקורות. לכל מקור: id, name, kind, licence, terms_url, terms_checked_at, rate_limit, key_env (**שם** משתנה הסביבה בלבד), commercial_ok (True/False/"unknown"), attribution, coverage, reliability, cache_ttl_s, usable_in_israel.
  - **עטיפת רשומה אחידה** `record()`: `{source_id, fetched_at, source_updated_at, precision_m, reliability, kind, kind_label, attribution, data}`.
  - **מטמון SQLite**: הקובץ `data_cache.sqlite` ליד מסד הנתונים של השרת (`server/server_data/` או `/data` בדוקר). אפשר להגדיר נתיב אחר ב-`LIFEGUARD_DATA_CACHE`. **לא** בתיקיית data/ של המאגר, ו**בלי** מיגרציה במסד הראשי.
  - **TTL לכל מקור:**

    | TTL | מקורות |
    |---|---|
    | 0 (לא נשמר) | התרעות Oref ו-Siren. התרעה ישנה מסוכנת |
    | 15 דק' | Open-Meteo |
    | 1 יום | OSRM foot |
    | 7 ימים | Overpass, Mapillary |
    | 30 יום | Nominatim (מטמון חובה לפי המדיניות) |
    | ללא תפוגה (None) | שכבות מקומיות |

  - `get(..., allow_stale=True)` מחזיר נתון שפג תוקפו לשימוש offline, מסומן `stale`.
  - `secret(ENV)` קורא מפתח ממשתנה סביבה או מקובץ סוד `ENV_FILE` (docker secret). המפתח לא נכנס לפלט.
- **`GET /v1/data-sources`**:
  - נתיב ציבורי, קריאה בלבד, **בלי קריאות רשת**. בטוח, ולא בנתיב ה-SOS.
  - מחזיר את הרישום ומצב לכל מקור: `not_configured` (חסר מפתח, כתובת או קובץ שכבה), `configured`, או `reachable`/`unreachable` לפי הבדיקה האחרונה. כולל גם `key_present` (bool) ומועד ה-snapshot המקומי.
  - בדיקה חיה רק ביוזמת מנהל/ת: `POST /v1/admin/data-sources/check` (דורש LIFEGUARD_ADMIN_TOKEN), או `python -m server.data_sources --check`. מקור לא מוגדר לא מקבל שום בקשה.
  - Oref, Siren ו-Mapillary מעדכנים את "הבדיקה האחרונה" בעצמם בכל שימוש.
- **`server/mapillary_client.py`**: לקוח בצד השרת בלבד, stdlib, בלי numpy/cv2. התמונה לא מורדת בכלל.
  - **מקור ה-token:** `MAPILLARY_TOKEN` או `MAPILLARY_TOKEN_FILE`. בלי token: `not_configured`, ואפס בקשות.
  - **מה מחזיר:** שתי תצפיות נגזרות, `kind=estimate` ו-`reliability=low`:
    1. כיסוי תמונות סביב נקודה: מספר תמונות, טריות, מרחק לתמונה הקרובה.
    2. עמודי תאורה ש-Mapillary זיהה אוטומטית (`map_features`, `object--street-light`), עם ההערה "היעדר זיהוי ≠ אין תאורה; זיהוי ≠ דולק".
  - **ייחוס:** לכל תמונה: מזהה, יוצר/ת, CC BY-SA 4.0 וקישור, ובנוסף `logo_required`.
  - **אבטחה:** אותם כללים כמו בכלי המחקר. header `OAuth`, בלי מעקב אחרי redirect, bbox קטן מ-0.01 מעלות רבועות, תגובה מוגבלת בגודל, timeout 6 שנ'.
  - **מטמון:** 7 ימים.
  - **נתיב:** `GET /v1/data/mapillary?lat&lon&radius_m`, למשתמש/ת מחובר/ת בלבד, כדי שהמכסה שלנו לא תנוצל.
  - ניתוח פיקסלים (תאורה מהתמונה) נשאר בכלי המחקר `research_tools/mapillary.py`.
- **`server/national.py`, Siren כמקור משלים**:
  - מופעל רק עם `LIFEGUARD_SIREN_KEY` (או `_FILE`), ורק מפתח פרטי `pr_`. מפתח ציבורי `pb_` נדחה, כי תנאים §5 אוסרים אותו בצד שרת.
  - **סקר:** `GET https://api.siren.co.il/active` עם `X-API-Key`, מטמון 5 שנ' בזיכרון. בערך 180 בקשות לכל 15 דק', מתחת למכסה של 1,000. ב-429 יש backoff לפי `Retry-After`.
  - הבקשה רצה במקביל לפיקוד העורף, ולכן לא מעכבת אותו.
  - **ההתרעה הרשמית מחושבת קודם ולא משתנה לעולם.** היא לא משתנה כש-Siren ריק, שונה, מחזיר "endAlert", מחזיר שגיאה או 429 (נבדק בפרמטריזציה).
  - Siren מוצג בבלוק נפרד `alerts.supplementary`, שכולל: `source` "Siren (siren.co.il) - מקור קהילתי, לא רשמי", `official: false`, `attribution_url`, והערה שהמקור המחייב הוא פיקוד העורף.
  - שדה `agreement` מתאר את היחס בין המקורות: `both` / `official_only` / `siren_only` / `none` / `official_unavailable`. ב-`siren_only` וב-`official_unavailable` יש טקסט "בדקו מיד באפליקציית פיקוד העורף".
  - תרגילים (`...Drill`) לא מוצגים.
  - `alerts.primary_source = "oref"`. ההתרעה הרשמית מסומנת `official: true` ו-`source`.
  - **לא מומש:** Socket.IO בזמן אמת. השרת הוא stdlib, ו-polling של 5 שנ' מספיק למקור משלים.
  - **לא נגענו ב-UI** (server/web ו-Windows בבעלות אחרים / אסור לגעת). הבלוק זמין ב-API. כשה-UI יציג אותו, **חובה** קרדיט גלוי עם קישור ל-siren.co.il (§9), ותווית "לא רשמי".

## 3. data.gov.il: אילו מאגרים גיאוגרפיים שמישים

**השאילתות והתוצאות:**

| שאילתה | מאגרים |
|---|---|
| פשיעה | 1 |
| crime | 1 |
| תאונות דרכים | 11 |
| **הולכי רגל** | **0** |
| תאורה | 2 |
| תשתיות | 21 |
| תחבורה ציבורית | 51 |
| רמזורים | 3 |
| מקלטים | 2 |

אין מאגר "הולכי רגל" נפרד. תאונות הולכי רגל מתקבלות רק דרך סינון ה-PUF.

**עם קואורדינטות (נקודה):**

| מאגר | גוף מפרסם | שדות | שורות | הערה |
|---|---|---|---|---|
| `teunot2022`, `2021-puf`, `teunot2019`, `2020-puf`, `2018-puf` | הלמ"ס | X, Y (ITM) + SEMEL_YISHUV | 10,404 (2022) / 11,554 / 12,670 / 10,836 / 12,596 | דוגמה: X=176369, Y=659329 |
| `street-light-br7` | ב"ש | lat, lon | 29,867 | |
| `light-traffics-br7` | ב"ש | lat, lon | 447 | |
| `shelters-br7` | ב"ש | lat, lon | 260 | |
| `sport-br7` | ב"ש | lat, lon | 168 | |
| `traffic_light_junction` | משרד התחבורה | X, Y + CITYCODE | 3,241 | |
| `miutim_risk` (מוקדי סיכון בחברה הערבית) | משרד התחבורה | X, Y | 2,531 | |

**גיאוגרפיה שאפשר לצרף (בלי קואורדינטות):**

| מאגר | שדות לצירוף | שורות | הערה |
|---|---|---|---|
| `crime_records_data` (משטרה) | StatisticAreaKod, municipalKod, PoliceStationKod | ~395K-424K לכל משאב שנתי | עודכן 2026-04-19. **אין קואורדינטות** |
| `accidents_municipal` | CITYCODE | 1,174 | |
| `accid_taz` | TAZAREA, CITYCODE | 2,637 | |
| `urban_intersections` | STREET1/STREET2 | 977 | CC-BY |
| `bph_infra_exist` / `bph_infra_plan` | STREETCODE / STREETNAME | 14,150 / 2,500 | שבילי אופניים |
| `licensing_bus_system` | שמות יישובי מוצא ויעד | | |

**בלי גיאוגרפיה שמישה ב-datastore:**
- `2023-puf`: PUF של 2020-2024. ב-datastore רק המילון, 181 שורות. קובץ הנתונים עצמו לא ב-datastore, וההורדה מ-e.data.gov.il חסומה (ראו data_inventory_israel.md).
- `teunot_2017`, `mokdey_teunot-drachim_2018`, `486`: בלי datastore או בלי שדות מיקום.
- `bus_fleet`, `kli_rechev_ciburiim`, `train_*`, `vehicle_counts`, `sheltersmaaleadumim` (528 שורות, בלי קואורדינטות), `shikumshchunot`.
- סקרי OB (`onboard_*`, `rail_ayalon_*`): **"לא צוין רישיון"**, לכן אסור להניח שמותר.

## 4. מה צריך לעשות (המשתמש/ת / מנהל/ת השרת)

1. **Mapillary (אופציונלי):**
   - נרשמים ב-https://www.mapillary.com/dashboard/developers (חשבון Meta/Mapillary), יוצרים אפליקציה ומעתיקים את ה-Client Token.
   - מגדירים בשרת בלבד: `MAPILLARY_TOKEN` או `MAPILLARY_TOKEN_FILE`.
   - לפני שימוש מסחרי: בדיקה משפטית של §12 בתנאים, ושל share-alike (CC BY-SA) על נתונים נגזרים.
2. **Siren (אופציונלי, משלים בלבד):**
   - פותחים חשבון ב-https://siren.co.il/dashboard?view=signup ומגישים בקשת גישה בדשבורד.
   - אחרי אישור יוצרים מפתח **פרטי** (`pr_...`).
   - מגדירים בשרת: `LIFEGUARD_SIREN_KEY` או `LIFEGUARD_SIREN_KEY_FILE`. לעולם לא בקוד לקוח, ולא במאגר.
   - כשה-UI יציג את הבלוק: קרדיט גלוי עם קישור, ותווית "לא רשמי".
3. **פיקוד העורף:**
   - הנקודה הציבורית החזירה 403 מהמחשב הזה.
   - חיבור רשמי רק דרך מראה מורשית: `LIFEGUARD_OREF_ALERTS_URL`.
   - בלי הגדרה כזו, השרת אומר במפורש "לא מחובר".
4. **Overpass:** לייצור, מופע Overpass עצמי או קובץ OSM של ישראל (Geofabrik). המראות הציבוריות לא אמינות. snapshot של safe_places כבר מובנה, ולכן האפליקציה עובדת בלי Overpass.
5. **Nominatim:** המגבלה (1 בקשה/שנ') היא **לכל האפליקציה**, לא לכל משתמש/ת.
   - manual_location.py ו-map_module.py פונים ישירות מהלקוח. בהיקף ייצור, להעביר דרך proxy בשרת עם מטמון (TTL 30 יום מוגדר ברישום), או להפעיל מופע עצמי.
   - אסור autocomplete. כדאי לוודא שאין חיפוש תוך כדי הקלדה.
6. **Open-Meteo:** החינמי לא-מסחרי. לגרסה מסחרית: תוכנית בתשלום (לא בהיקף, "בלי תוכניות בתשלום") או מופע עצמי (קוד פתוח).
7. **בדיקה חיה תקופתית:** הרצה ראשונה של `python -m server.data_sources --check` ב-2026-11-11:
   - **מגיבים (200):** nominatim, osrm_fossgis_foot, open_meteo, data.gov.il (שלושה מאגרים).
   - **לא זמין:** overpass (502).
   - **דולגו (לא מוגדרים, אפס בקשות):** siren, mapillary.

   פקודות להרצה חוזרת: `python -m server.data_sources --check`, או `POST /v1/admin/data-sources/check`.

## 5. מקורות התנאים (נבדקו 2026-11-11)

**OSM ומשתמשי OSM:**
- Nominatim: https://operations.osmfoundation.org/policies/nominatim/
- Overpass: https://dev.overpass-api.de/overpass-doc/en/preface/commons.html
- OSRM demo: https://github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy
- FOSSGIS: https://routing.openstreetmap.de/about.html

**Mapillary:**
- תנאים (בתוקף מ-15.2.2024): https://www.mapillary.com/terms
- תיעוד ה-API: https://www.mapillary.com/developer/api-documentation

**Siren:**
- תנאים (מרץ 2026): https://siren.co.il/terms
- תמחור (כל התוכניות $0): https://siren.co.il/pricing
- תיעוד: https://docs.siren.co.il/ (access, api/active-alerts, api/errors-and-limits, api/alerts, api/test-mode)
- המפעיל: מפתח יחיד (orielhaim), "פרויקט קהילתי עצמאי". קוד הייחוס: https://github.com/orielhaim/radar (Apache-2.0)

**אחרים:**
- Open-Meteo: https://open-meteo.com/en/terms
- GDELT: https://www.gdeltproject.org/about.html
- data.gov.il ו-הלמ"ס: research/data_inventory_israel.md §9
