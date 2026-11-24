# מניפסט מאגרים - מנוע סכנה v2

נוצר ע"י `research_tools/hv2_data.py` (2026-11-16). המניפסט המלא (רשומה לכל קטע: מקור, רישיון, ייחוס, תוויות, סביבה, מכשיר, סשן, פיצול, אימות) נמצא ב-`research_data/manifests/hazard_v2_manifest.jsonl` - מחוץ למאגר הקוד, כמו האודיו עצמו.

**בדיקות תקינות:** עברו (אין דליפת סשן, אין NC באימון, אין כפילות)

## לפי מקור, פיצול ורישיון

| מקור | פיצול | רישיון | מותר לאימון מסחרי | קטעים |
|---|---|---|---|---|
| esc50 | excluded_overlap | cc-by | לא | 144 |
| esc50 | excluded_overlap | cc-by-nc-3.0 | לא | 486 |
| esc50 | test | cc-by | לא | 256 |
| esc50 | test | cc-by-nc-3.0 | לא | 1114 |
| foleyset | excluded_ambiguous | per-clip unverified (FoleySet record: cc-by-4.0) | לא | 312 |
| foleyset | excluded_ambiguous | zero | כן | 22 |
| foleyset | excluded_overlap | by | כן | 7 |
| foleyset | excluded_overlap | by-nc | לא | 3 |
| foleyset | excluded_overlap | zero | כן | 399 |
| foleyset | test | by | כן | 4 |
| foleyset | test | per-clip unverified (FoleySet record: cc-by-4.0) | לא | 1368 |
| foleyset | test | zero | כן | 254 |
| foleyset | train | by | כן | 26 |
| foleyset | train | by-nc | לא | 1 |
| foleyset | train | per-clip unverified (FoleySet record: cc-by-4.0) | לא | 5698 |
| foleyset | train | sampling+ | לא | 1 |
| foleyset | train | zero | כן | 663 |
| foleyset | val | per-clip unverified (FoleySet record: cc-by-4.0) | לא | 1154 |
| foleyset | val | zero | כן | 88 |
| fsd50k | test | by | כן | 3489 |
| fsd50k | test | by-nc | לא | 1425 |
| fsd50k | test | sampling+ | לא | 403 |
| fsd50k | test | zero | כן | 4914 |
| fsd50k | train | by | כן | 16346 |
| fsd50k | train | by-nc | לא | 3517 |
| fsd50k | train | sampling+ | לא | 1048 |
| fsd50k | train | zero | כן | 11723 |
| fsd50k | val | by | כן | 3671 |
| fsd50k | val | by-nc | לא | 1099 |
| fsd50k | val | sampling+ | לא | 326 |
| fsd50k | val | zero | כן | 3236 |
| gunshot_fit | test | cc-by-4.0 | כן | 460 |
| gunshot_fit | train | cc-by-4.0 | כן | 291 |
| gunshot_fit | val | cc-by-4.0 | כן | 96 |
| nonspeech7k | excluded_overlap | cc-by-4.0 (Zenodo) / cc-by-nc-sa-4.0 (paper) - treated as NC | לא | 304 |
| nonspeech7k | test | cc-by-4.0 (Zenodo) / cc-by-nc-sa-4.0 (paper) - treated as NC | לא | 421 |
| sonyc_ust | test | cc-by-4.0 | כן | 664 |
| sonyc_ust | train | cc-by-4.0 | כן | 13538 |
| sonyc_ust | val | cc-by-4.0 | כן | 4308 |
| tau2020_mobile | test | other-nc (TAU) | לא | 6513 |
| ufrb_esd | test | cc-by-4.0 (record) / underlying sources unknown - test only | לא | 2849 |

## יעדים ושליליים קשים - קטעים שמותרים לאימון (train / val) מול מבחן

| תווית | train | val | test |
|---|---|---|---|
| gunshot | 567 | 140 | 853 |
| explosion | 865 | 120 | 427 |
| fireworks | 321 | 36 | 105 |
| glass_break | 345 | 52 | 155 |
| siren | 521 | 230 | 416 |
| alarm | 947 | 195 | 599 |
| scream | 192 | 26 | 350 |
| impact | 654 | 273 | 628 |
| distress_voice | 636 | 167 | 777 |
| horn | 1138 | 395 | 407 |
| drums_music | 1646 | 1216 | 825 |
| music | 9413 | 2581 | 2152 |
| crowd_cheer | 532 | 155 | 632 |
| children | 66 | 3 | 97 |
| laughter | 623 | 115 | 295 |
| construction | 912 | 233 | 287 |
| traffic | 3345 | 1087 | 1132 |
| speech | 3410 | 1561 | 1058 |
| tv_like | 57 | 1 | 61 |
| door_knock | 750 | 231 | 500 |
| thunder | 342 | 62 | 171 |

## מקורות שנבדקו ולא נכללו

- **AudioSet:** האודיו זמין רק כסרטוני YouTube - לא מורידים (אין הרשאה). משתמשים רק באונטולוגיה (דרך YAMNet).
- **UrbanSound8K:** CC BY-NC (הערכה בלבד), 6 GB, וכולו מ-Freesound - חופף ברובו ל-FSD50K ו-ESC-50. לא הורד.
- **Common Voice:** דורש אישור תנאים בחשבון המשתמש/ת - לא בוצע בשמך. FLEURS (CC BY) כבר קיים - לדיבור בלבד, לא כתחליף לקריאות מצוקה אמיתיות.
- **SONYC v2.3:** הקישור שבבקשה (2590742) הוא רשומת הגרסה; נעשה שימוש ברשומה העדכנית 3966543 (אותו מאגר).

## הערות רישוי
- FSD50K: רישיון לכל קטע. לאימון רק CC0 / CC BY. CC BY-NC ו-Sampling+ - מבחן בלבד.
- **ממצא ביקורת:** hazard_audio_v1 אומן על כל dev של FSD50K, כולל ~6,000 קטעי CC BY-NC / Sampling+. v2 לא.
- Nonspeech7k: סתירה ברישוי (Zenodo: CC BY 4.0; המאמר / DCASE: CC BY-NC-SA) - מבחן בלבד.
- TAU, ESC-50: NC - מבחן בלבד. ירי (Florida Tech) ו-SONYC: CC BY - ייחוס נדרש (בעמודת attribution).
