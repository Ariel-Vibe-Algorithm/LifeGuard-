# LifeGuard: מקורות רב-לשוניים ומבוססי וידאו לזיהוי מצוקה אקוסטית

- **תאריך גישה:** 2026-11-05/06 לכל המקורות, אלא אם צוין אחרת.
- **הקשר:** מחקר אקדמי לא מסחרי.
- **המסמך מרחיב את** `research/acoustic_inventory.md`, ואינו חוזר על מה שכבר מופיע שם: YAMNet, FSD50K, ESC-50, UrbanSound8K, DCASE2017 T2, יסודות AudioSet, MIVIA, Common Voice he, ivrit.ai ו-Whisper.
- **שיטה:**
  - כל נתון נלקח ממקור שנפתח בפועל: WebFetch, ‏curl ל-API או לקובץ metadata, או `pdftotext` על PDF רשמי.
  - **מספרים שסומנו "ספירה ישירה"** חושבו על ידי מקובצי ה-CSV/TSV הרשמיים, שהורדו ל-scratchpad (קבצי metadata בלבד, בלי אודיו).
  - מה שלא אומת מסומן **not verified**.
- **הסתייגות:** WebFetch מסכם דפים באמצעות מודל קטן. נתונים קריטיים (ספירות AudioSet, רישיון Dusha, סעיף 19, חוות הדעת של משרד המשפטים, ספירות MSWC) אומתו מול הקובץ הגולמי.
- **אין לראות בסעיף המשפטי ייעוץ משפטי.**

---

## 0. תקציר מנהלים

1. **AudioSet הוא המקור הרב-לשוני הגדול ביותר לצרחה, צעקה ובכי מהעולם האמיתי.**
   - איחוד 7 מחלקות המצוקה הקוליות (Screaming, Shout, Yell, Children shouting, Crying/sobbing, Whimper, Wail/moan): ‏**6,104** קטעים ב-unbalanced, ‏**411** ב-balanced ו-**415** ב-eval (ספירה ישירה). זה כ-16 שעות ב-unbalanced.
   - תת-הסט **Strong labels** (מאי 2021, ‏CC BY 4.0) נותן גבולות זמן מדויקים. לדוגמה, Shout: ‏5,745 אירועים ב-3,467 קטעים ב-train.
   - מגבלות: אין תווית שפה; התוויות weak ורועשות (ב-QA הרשמי Shout ו-Yell קיבלו 5/10 נכונות); והאודיו לא מופץ רשמית.
2. **דאטה אלימות עם אודיו:** XD-Violence הוא היחיד הגדול: 4,754 סרטונים, 217 שעות, מסרטים ומיוטיוב. **אין רישיון בדף**, ויש בו קורבנות אמיתיים.
   - UCF-Crime **חסר אודיו**, לפי מאמר XD-Violence.
   - ב-RLVS הרישיון בקאגל הוא "Data files © Original Authors", ונוכחות אודיו לא אומתה.
3. **דיבור רגשי:** המקורות הנקיים ביותר מבחינת רישיון:
   - CREMA-D (ODbL, ‏7,442 קטעים, 91 שחקנים)
   - Dusha ברוסית (רישיון דמוי BY-SA, כ-256 שעות crowd)
   - MESD בספרדית (CC BY 4.0)
   - RESD ברוסית (MIT)
   - RAVDESS ו-CaFE ‏(CC BY-NC-SA 4.0)
   - TESS ‏(CC BY-NC 4.0)

   **לא נמצא קורפוס רגשי קולי בעברית.**
4. **מילות מצוקה:**
   - MSWC (CC BY 4.0) מכיל את help ‏(4,437 דגימות / 3,035 דוברים), hilfe, ayuda, secours, помогите (6 בלבד), ساعدني (25), النجدة (11). **אין בו עברית ואין אמהרית.**
   - MMS-TTS קיים לעברית, אמהרית, ערבית ורוסית, ‏**CC-BY-NC 4.0**.
   - Piper he_IL (saspeech) מבוסס על SASPEECH, ‏"Custom non-commercial".
5. **יוטיוב (לא ייעוץ משפטי):**
   - תנאי השימוש של YouTube אוסרים הורדה ו-scraping, "except… as permitted by applicable law".
   - סעיף 19 לחוק זכות יוצרים מתיר "שימוש הוגן… למטרות כגון… מחקר".
   - חוות דעת של משרד המשפטים (18.12.2022) קובעת שאימון מכונה יהווה "לרוב שימוש הוגן", אך **מחריגה במפורש את דיני הפרטיות והאתיקה**.

   **שורה תחתונה:** מותר באופן סביר לחקור בגישת AudioSet (מזהים, timestamps ו-embeddings בלבד, בלי הפצה). אבל וידאו של קורבנות אמיתיים מחייב אישור ועדת אתיקה ואמצעי הגנה.

---

## 1. מקורות וידאו ועולם אמיתי

### 1.1 AudioSet לעומק: מקור רב-לשוני מיוטיוב

**מקורות:**
- https://research.google.com/audioset/download.html
- https://research.google.com/audioset/download_strong.html
- קבצי CSV רשמיים: `http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/{balanced_train,eval,unbalanced_train}_segments.csv`
- QA: `.../v1/qa/qa_true_counts.csv`
- Strong: `.../youtube_corpus/strong/audioset_{train,eval}_strong.tsv`

**כותרות ה-CSV** (מילה במילה):

| קובץ | כותרת |
|---|---|
| balanced | `# num_ytids=22160, num_segs=22160, num_unique_labels=527, num_positive_labels=52882` |
| eval | `num_segs=20371`, `num_positive_labels=51804` |
| unbalanced | `num_segs=2041789`, `num_positive_labels=4020212` |

כל הקבצים נוצרו ב-5 במרץ 2017.

#### מספר קטעים (10 s) לכל מחלקה, בספירה ישירה (grep על ה-MID)

| idx (YAMNet) | מחלקה | MID | balanced | eval | unbalanced | QA (true/rated) |
|---|---|---|---|---|---|---|
| 11 | Screaming | /m/03qc9zr | 59 | 61 | 1,126 | 8/10 |
| 6 | Shout | /m/07p6fty | 60 | 60 | 1,248 | **5/10** |
| 9 | Yell | /m/07sr1lc | 60 | 60 | 674 | **5/10** |
| 7 | Bellow | /m/07q4ntr | 60 | 60 | 391 | 5/10 |
| 10 | Children shouting | /t/dd00135 | 60 | 60 | 553 | 8/10 |
| 19 | Crying, sobbing | /m/0463cq4 | 60 | 62 | 1,340 | 9/10 |
| 20 | Baby cry | /t/dd00002 | 60 | 60 | 2,270 | not verified |
| 21 | Whimper | /m/07qz6j3 | 59 | 60 | 1,136 | 8/10 |
| 22 | Wail, moan | /m/07qw_06 | 59 | 60 | **96** | 7/9 |
| 33 | Groan | /m/07s2xch | 60 | 60 | 582 | not verified |
| 39 | Gasp | /m/07s0dtb | 60 | 60 | 209 | not verified |
| 461 | Slap, smack | /m/07ryjzk | 59 | 60 | 751 | not verified |
| 421 | Gunshot | /m/032s66 | 178 | 174 | 3,869 | not verified |
| 0 | Speech | /m/09x0r | 5,735 | 5,324 | 999,421 | 10/10 |

**הערות לטבלה:**
- **איחוד 7 מחלקות המצוקה** (11, 6, 9, 10, 19, 21, 22): ‏balanced ‏411, ‏eval ‏415, ‏unbalanced ‏6,104 קטעים. כלומר כ-1.1 + 1.2 + 17 שעות של קטעי 10 s. בפועל רק חלק מכל קטע מכיל את האירוע.
- **בדיקת עקביות:** דף ה-ontology של Screaming מציג "1,246 annotations in dataset", וזה שווה בדיוק ל-59+61+1,126.
- **QA:** עמודת ה-QA מגיעה מ-`qa_true_counts.csv` (‏`label_id,num_rated,num_true`). Shout, Yell ו-Bellow קיבלו 5 נכונים מתוך 10 שנבדקו, כלומר **רעש תוויות של כ-50%**.
- **"Speech with emotion":** ל-AudioSet **אין מחלקות דיבור רגשי** (כעס, פחד וכדומה). Speech הוא מחלקה כללית, ומכאן הצורך בקורפוסים של סעיף 2.

#### Strong labels (גבולות זמן מדויקים, מאי 2021, CC BY 4.0)

**מהדף הרשמי:**
- train: ‏103,463 קטעים ו-934,821 אירועים.
- eval: ‏16,996 קטעים.
- בסך הכול "456 distinct labels".

**ספירה ישירה מה-TSV** (עמודות `segment_id, start_time_seconds, end_time_seconds, label`):

| מחלקה | train: אירועים / קטעים | eval: אירועים / קטעים |
|---|---|---|
| Shout | 5,745 / 3,467 | 1,112 / 665 |
| Screaming | 1,371 / 842 | 277 / 155 |
| Crying, sobbing | 1,324 / 524 | 244 / 94 |
| Baby cry | 1,767 / 492 | 201 / 64 |
| Children shouting | 209 / 137 | 78 / 45 |
| Wail, moan | 182 / 75 | 59 / 31 |
| Bellow | 61 / 40 | 43 / 31 |
| Yell | 55 / 33 | 49 / 24 |
| Whimper | 59 / 27 | 41 / 17 |

**משמעות:** ה-strong labels מאפשרים לחתוך חלונות של 0.96 s בדיוק סביב האירוע. זה קריטי לאימון ראש על embeddings של YAMNet, כי עם weak labels של 10 s רוב החלונות "חיוביים" הם בעצם רקע.

#### דרכי השגה ורישיון

1. **Features רשמיים** (`features.tar.gz`):
   - "128-dimensional audio features extracted at 1Hz", מ-"VGGish", בפורמט TFRecord (SequenceExample), אחרי PCA וכימות.
   - 12,228 קבצים. גודל: `Content-Length: 2588881044` (כ-2.4 GB), ‏`Last-Modified: 05 Mar 2017`.
   - **אלה אינם embeddings של YAMNet** (‏1024-d). אי אפשר לאמן עליהם ראש ל-YAMNet. הם שימושיים רק ל-baseline נפרד או לסינון קטעים.
2. **אודיו:** לא מופץ רשמית ("YouTube video IDs with start and end times"). יש להוריד מיוטיוב, ראו סעיף 4.
3. **מראות ב-HF** (נבדקו ב-API ב-2026-11-05). כולן **הפצה חוזרת לא רשמית של אודיו מיוטיוב**. תג ה-`cc-by-4.0` שבהן חל על התוויות של Google, לא על האודיו:

   | מראה | תג רישיון | הורדות |
   |---|---|---|
   | `agkphysics/AudioSet` | cc-by-4.0 | 60,371 (ראו גם inventory) |
   | `CLAPv2/audioset_strong` | ללא | |
   | `enyoukai/AudioSet-Strong` | ללא | |
   | `chikuwa-AI/AudioSet_balanced_video` / `_unbalanced_video` | ללא | |
   | `jsutphen/audioset-full` | ללא | |
   | `danjacobellis/audioset_opus_24kbps` | ללא | |
   | `quinnlue/audioset-opus-fbanks` | cc-by-4.0 | |

   כמה קטעים זמינים בכל מראה: not verified, פרט ל-agkphysics (ראו inventory).
4. **רישיון:**
   - התוויות: "Creative Commons Attribution 4.0 International (CC BY 4.0)".
   - ה-ontology: ‏"CC BY-SA 4.0".
   - ה-strong labels: ‏CC BY 4.0.
5. **שפות:** ב-AudioSet אין תווית שפה. הרב-לשוניות היא תכונה של יוטיוב, והתפלגות השפות **not verified**. כדי לבנות סט מבחן לפי שפה צריך להריץ LID (למשל `facebook/mms-lid-126`) על הקטעים שהושגו.

### 1.2 דאטהסטים של אלימות וקטטות עם אודיו

| דאטהסט | אודיו? | גודל | מקור / שפות | אמיתי או מבוים | רישיון / גישה (כפי שנראה) | הערות אתיות |
|---|---|---|---|---|---|---|
| **XD-Violence** https://roc-ng.github.io/XD-Violence/ ‏(arXiv 2007.04687, ECCV 2020) | **כן**: "untrimmed videos with audio signals". יש גם features של VGGish להורדה | 4,754 סרטונים, 217 שעות. 2,405 אלימים ו-2,349 לא אלימים. train ‏3,954, ‏test ‏800 (500 אלימים) | "collected from both movies and YouTube… a total of 91 movies". השפות not verified (רב-לשוני ככל הנראה) | מעורב: סרטים מבוימים לצד in-the-wild (CCTV, טלפונים, מצלמות רכב) | **אין רישיון או תנאים בדף.** הורדה מ-Baidu, ‏AliyunDrive ו-OneDrive | כולל abuse ו-fighting אמיתיים מיוטיוב. תוויות weak ברמת הסרטון, ו-temporal רק ב-test. המאמר עצמו מזהיר שב-Violent-Flows וב-CCTV-Fights "quite a few videos are silent or only contain background music" |
| **UCF-Crime** https://www.crcv.ucf.edu/projects/real-world/ | **לא**: "The UCF-Crime dataset … lacks audio" (מאמר XD-Violence) | "128 hours… 1900 long and untrimmed real-world surveillance videos, with 13 realistic anomalies" | CCTV | אמיתי | הדף הרשמי לא מציג רישיון (not verified). נגזרות בקאגל מוצגות כ-CC BY-NC-SA, ‏CC0 וכדומה, ואינן מחייבות | **לא שימושי לאודיו** |
| **RLVS** (Kaggle `mohamedmustafa/real-life-violence-situations-dataset`) | **not verified**: מאמרים מדווחים שחילצו ממנו אודיו, אבל לא נבדקו הקבצים | Kaggle API: "1000 videos containing real street fight and 1000 video from other classes", ‏totalBytes ‏1,948,708,410 | YouTube | אמיתי | Kaggle API: ‏**"Data files © Original Authors"**, כלומר אין רישיון פתוח | קטטות רחוב אמיתיות, אנשים מזוהים |
| **VSD / MediaEval** https://www.interdigital.com/data_sets/violent-scenes-dataset | כן. מושגי אודיו ("gunshots, explosions, screams") מתויגים רק ב-18 סרטים | 32 סרטים ו-86 סרטוני YouTube (2014). לפי טבלת XD: ‏25 סרטונים, ‏35 שעות | סרטי הוליווד (אנגלית ברובם) | מבוים | "for scientific research only". בקשה במייל vsdmanagement@interdigital.com. את הסרטים עצמם צריך לרכוש | מבוים, ללא קורבנות אמיתיים. ההגדרה הנוחה ביותר: תיוג scream כ-"non-verbal screams and effort noise" |
| **Dutch aggression corpora** (Lefter, Rothkrantz ואחרים, TU Delft / TNO) | כן | Trains: ‏21 תרחישים, "total length of the audio-visual recordings is 43 minutes", ‏3 רמות אגרסיה. יש גם NAA ו-Stress at Service Desk | הולנדית | מבוים ("semi-professional actors") | **לא נמצא נתיב הורדה ציבורי או רישיון**, not verified. כנראה בפנייה לחוקרים | אחת מהשפות שאינן ביעד. שימושי כמבחן cross-lingual לאגרסיה מילולית |
| **"SAD" (aggression corpus)** | | | | | **לא נמצא** דאטהסט בשם הזה בחיפוש | |
| **Kaggle: screaming** (חיפוש API "scream") | כן | `whats2000/human-screaming-detection-dataset`: ‏MIT, ‏4.47–5.68 GB (בשני ה-endpoints מופיעים ערכים שונים)<br>`kinglyknight/scream-dataset`: ‏Apache 2.0, ‏1.63 GB<br>`aananehsansiam/audio-dataset-of-scream-and-non-scream`: ‏Unknown, ‏349 MB<br>`sanzidaakterarusha/scream-dataset`: ‏© Original Authors, ‏168 MB<br>`afisarsy/raw-audio-of-accident-and-crime-detection`: ‏MIT, ‏600 MB | ברובם YouTube או AudioSet | מעורב | **תג הרישיון בקאגל הוא הצהרת המעלה בלבד.** ברובם נגזרת של יוטיוב, כך שהרישיון בפועל מפוקפק | איכות ו-provenance לא ידועים. מומלץ רק כ-eval משני ולא לאימון |

---

## 2. קורפוסים של דיבור רגשי (כעס, פחד, עצב)

**ייעוד:** "angry shouting", "pleading" ו-"crying speech", וגם hard negatives (דיבור נלהב או שמח בקול רם).

**כמעט כולם acted**, ולכן צפוי domain shift מול מצוקה אמיתית. ראו סעיף 5.

| קורפוס | שפה | גודל | דוברים | רגשות רלוונטיים | acted/natural | רישיון (כפי שנראה) | גישה |
|---|---|---|---|---|---|---|---|
| **CREMA-D** https://github.com/CheyneyComputerScience/CREMA-D | en | "7,442 original clips". ‏EmoBox: ‏5.3 שעות | 91 ("48 male and 43 female", גילאי 20–74, מגוון אתני) | Anger, Fear, Sad (Low/Medium/High) | acted, ‏12 משפטים | "Open Database License" ו-Database Contents License | GitHub (git-lfs) |
| **RAVDESS** https://zenodo.org/records/1188976 | en (צפון אמריקאית) | 7,356 קבצים, 24.8 GB. אודיו דיבור: 1,440 קבצים (zip של 208.5 MB) | 24 (12 נשים, 12 גברים) | angry, fearful, sad | acted | "Creative Commons Attribution-NonCommercial-ShareAlike 4.0" | Zenodo |
| **TESS** https://borealisdata.ca/dataset.xhtml?persistentId=doi:10.5683/SP2/E8H2MF | en | 2,800 גירויים | 2 נשים (בנות 26 ו-64) | anger, fear, sadness | acted | CC BY-NC 4.0 | Borealis |
| **IEMOCAP** https://sail.usc.edu/iemocap/ | en | "approximately 12 hours" | "10 actors" | anger, sadness ועוד | acted (scripted או improvised) | טופס release. הרישיון ב-PDF נפרד שלא נקרא (not verified) | טופס אלקטרוני |
| **MSP-Podcast** https://lab-msp.com/MSP/MSP-Podcast.html | en | "264,705 speaking turns (409 hours)", גרסה 2.0 | not verified | anger, sadness, fear, contempt ועוד | **natural** (פודקאסטים) | "Academic License (free of cost)". נדרשת חתימת מוסד. תנאי השימוש המסחרי סותרים בין המקורות (not verified) | PDF חתום למעבדה |
| **Dusha** (SberDevices) https://github.com/salute-developers/golos/tree/master/dusha | ru | crowd: כ-201,850 קבצים, ‏255.7 שעות, ‏2,068 דוברים<br>podcast: כ-102,113 קבצים, ‏90.9 שעות<br>crowd.tar: ‏28 GB | 8,308 בסך הכול | Sadness, Anger (וגם Positive ו-Neutral). **אין fear** | crowd: acted (קריאה)<br>podcast: natural | `golos/license/en_us.pdf`: "Public license with attribution and conditions reserved", "a reworking of a Creative Commons… (Attribution-ShareAlike 4.0)". **מתיר שימוש, כולל מסחרי, עם ייחוס ו-share-alike.** הוחלתו על Dusha מוסקת מכך שזה הרישיון היחיד ב-repo | "we can't provide audio files of podcasts": רק features עבור podcast |
| **RESD** https://huggingface.co/datasets/Aniemore/resd | ru | 1,396 קטעים (train ‏1,116, ‏test ‏280), ‏485 MB. כ-3.5 שעות לפי Kaggle | not verified | anger, fear, sadness, disgust ועוד | acted (דיאלוגים מאולתרים) | MIT | HF |
| **ANAD** (Kaggle `suso172/arabic-natural-audio-dataset`) | ar (דיאלקטים) | 1,384 יחידות של 1 s | not verified | angry, happy, surprised | **natural**: "Eight videos of live calls… from online Arabic talk shows" | CC BY-NC-SA 4.0 (תג בקאגל, הועלה על ידי המחברים ב-2017) | Kaggle |
| **BAVED** (Kaggle `a13x10/basic-arabic-vocal-emotions-dataset`) | ar | 1,935 הקלטות, 97.8 MB | 61 (45 גברים, 16 נשים) | 3 רמות עוצמה (לא רגשות מפורשים) | acted, ‏7 מילים | "Database: Open Database, Contents: © Original Authors" | Kaggle |
| **KSUEmotions** (LDC2017S12) https://catalog.ldc.upenn.edu/LDC2017S12 | ar (MSA) | "approximately five hours" | 23 (תימן, סעודיה, סוריה) | anger, sadness | acted | LDC User Agreement, **בתשלום** (הסכום not verified) | LDC |
| **EYASE** | ar (מצרית) | not verified (נמצאו 579 ו-461 במקורות שונים) | not verified | anger, sadness | semi-natural (סדרות טלוויזיה) | **not verified**. מקור אחד אומר "available from the dataset authors upon reasonable request". מראה בקאגל `mohammedhassanain/eyase-dataset` עם רישיון "Unknown" | פנייה למחברים |
| **ASED** https://github.com/Ethio2021/ASED_V1 | am (אמהרית) | 2,474 הקלטות. ‏EmoBox: ‏2.1 שעות | 65 (25 נשים, 40 גברים) | Fearful, Sad, Angry, Happy, Neutral | acted, נשפט על ידי 8 שופטים | **אין קובץ LICENSE** (GitHub API: `license: None`). ה-README מבקש לצטט את Retta et al. 2022 | GitHub |
| **CaFE** https://zenodo.org/records/1478765 | fr (קנדית) | כ-2.2 GB (‏192 kHz ו-48 kHz). ‏EmoBox: ‏936 קטעים, 1.2 שעות | 12 (6 גברים, 6 נשים) | 6 רגשות, ברמות Faible ו-Fort | acted | "Creative Commons Attribution-NonCommercial-ShareAlike 4.0" | Zenodo |
| **MESD** https://data.mendeley.com/datasets/cy34mh68j9/5 | es (מקסיקנית) | ‏EmoBox: ‏862 קטעים, 0.2 שעות | 5 מבוגרים ו-6 ילדים | anger, fear, sadness | acted, מילים בודדות | **CC BY 4.0** | Mendeley |
| **Emo-DB** http://emodb.bilderbar.info | de | "about 500 utterances". ‏EmoBox: ‏535 | 10 שחקנים | angry, anxious/fearful, sad | acted | **לא מופיע רישיון בדף** (not verified). בקאגל: "© Original Authors" | אתר |
| **ASVP-ESD** | רב-לשוני | כבר מופיע ב-inventory | | scream, pain, panic | | | |
| **EmoBox** (Interspeech 2024) https://github.com/emo-box/EmoBox | 14 שפות, 32 דאטהסטים | 262,020 קטעים, 294.4 שעות (כולל MSP-Podcast). כוללים ASED (אמהרית), CaFE ו-Oreau (צרפתית), MESD (ספרדית), RESD (רוסית), EmoDB ו-PAVOQUE (גרמנית) | 3,510 | | | **אין עמודת רישיון.** "some of them require license or registration" | כלי לחלוקות אחידות, לא דאטה |
| **עברית** | he | **לא נמצא** קורפוס דיבור רגשי בעברית. נמצאו רק HeMotion (טקסט), VoxKnesset (דיבור כנסת, 2,300 שעות, ללא רגש, רישיון not verified) ו-HebDB (לא רגשי, not verified) | | | | | |

**מסקנה מעשית לרגש:**
- **לאימון ראש "angry shout" או "fear/plead":**
  - CREMA-D (ODbL), ‏MESD (CC BY) ו-Dusha (BY-SA-like) הם הנקיים ביותר.
  - RAVDESS, ‏CaFE, ‏TESS ו-ANAD מתאימים ל-NC בלבד, וזה תואם את הפרויקט.
- **natural:** רק MSP-Podcast (אנגלית), Dusha podcast (רק features), ANAD (ערבית, 1,384 שניות) ו-EYASE (גישה לא ודאית).
- **חשוב:** angry speech ב-acted corpora הוא לרוב דיבור רגיל בעוצמה בינונית, לא צעקת מצוקה. יש לסנן לפי intensity גבוהה: High ב-CREMA-D, ‏strong ב-RAVDESS, ‏Fort ב-CaFE. ייתכן שעדיף להשתמש בהם כ-**hard negatives** ("כעס בלי סכנה") מאשר כפוזיטיבים.

---

## 3. מילות מצוקה רב-לשוניות (KWS)

### 3.1 MLCommons MSWC

**מקור:** https://huggingface.co/datasets/MLCommons/ml_spoken_words. ‏license: ‏`cc-by-4.0` (מה-API), גרסה "version 1.0".

**50 שפות** (רשימת `data/splits/` ב-repo), ביניהן ar, ru, fr, es, de, en. **אין he ואין am.**

**ספירה ישירה** מקבצי `splits/<lang>/{train,dev,test}.csv` (עמודות `LINK,WORD,VALID,SPEAKER,GENDER`), בפורמט מופעים / דוברים ייחודיים:

| שפה | מילים |
|---|---|
| en | help 4,437 / 3,035; stop 2,398 / 1,911; police 1,728 / 1,237; please 4,445 / 2,460; emergency 466 / 362; save 1,241 / 1,000; scream 149 / 145. **"no" חסר** |
| ru | помогите 6 / 5; помощь 183 / 75; полиция 8 / 8; нет 809 / 231; пожалуйста 126 / 41. **חסרות:** помоги, стоп, хватит, спасите, отпусти |
| ar (אוצר מילים של 1,656 בלבד) | ساعدني 25 / 19; النجدة 11 / 10; مساعدة 5 / 4; الشرطة 19 / 15; توقف 12 / 8; فضلك 208 / 64. **חסרות:** لا, أرجوك |
| fr | aide 204 / 139; aidez 8 / 8; secours 116 / 78; police 338 / 214; arrête 106 / 80; arrêtez 44 / 40; non 3,439 / 2,052; pitié 94 / 71 |
| es | ayuda 300 / 182; ayúdame 8 / 8; socorro 40 / 22; policía 236 / 139; basta 64 / 36. **חסרים:** auxilio, "no" |
| de | hilfe 622 / 397; polizei 574 / 400; nein 1,531 / 1,145; aufhören 115 / 89; stopp 31 / 31 |

**הסתייגויות:**
- המילים נחתכו מ-Common Voice, כלומר **קריאה רגועה** ולא צעקה. זה domain shift גדול ל-"הצילו!" צעוק.
- מילים קצרות (כמו no ו-لا) חסרות.

### 3.2 Google Speech Commands

https://huggingface.co/datasets/google/speech_commands

- רישיון "Creative Commons BY 4.0".
- "105,829 audio files".
- 35 מילים: yes, no, up, down, left, right, on, off, **stop**, go, ספרות, bed, bird ועוד.
- **אין "help"**. שימושי רק ל-"stop" ול-"no", וכ-negatives ו-background noise.

### 3.3 TTS ו-MMS

| משאב | רישיון | גודל | שפות רלוונטיות |
|---|---|---|---|
| `facebook/mms-tts-heb`, `-amh`, `-ara`, `-rus` | **cc-by-nc-4.0** (HF API) | כ-36.3M פרמטרים כל אחד (safetensors total) | he, am, ar, ru |
| `facebook/mms-1b-all` (ASR) | cc-by-nc-4.0 | "1 billion parameters… fine-tuned… on 1162 languages", כולל `heb` ו-`amh` | ASR רב-לשוני. **כבד מדי למכשיר.** מתאים ל-alignment אוטומטי ולחיתוך מילים מקורפוסים (למשל ivrit.ai ו-FLEURS) |
| `facebook/mms-lid-126` | cc-by-nc-4.0 | כ-966M | זיהוי שפה לתיוג קטעי AudioSet לפי שפה |
| Piper `he_IL-saspeech-medium` | MODEL_CARD: "License: See dataset URL". הדאטה SASPEECH (https://www.openslr.org/134): **"Custom non-commercial"**, "free for use for non-commercial purposes", ואסור "for commercial or broadcast needs" או "for political needs" | 63,221,984 בתים (onnx), דובר אחד (שאול אמסטרדמסקי), 22,050 Hz | he. **זה הקול העברי היחיד ב-Piper** |
| Piper `ar_JO/kareem`, `ru_RU/{denis,dmitri,irina,ruslan}` | רישיון כל קול not verified | | ar, ru |

**מסקנה ל-KWS:**
- **עברית ואמהרית:** אין דאטה אמיתי מוכן. השילוב המומלץ:
  - (א) TTS: MMS-TTS ו-Piper saspeech, שניהם NC. **הבעיה: TTS לא צועק.**
  - (ב) הקלטות מתנדבים בהסכמה, כולל צעקה.
  - (ג) חיתוך מילים בעזרת forced alignment מ-ivrit.ai, FLEURS ו-Common Voice.
- **אנגלית, ערבית, רוסית, צרפתית, ספרדית וגרמנית:** MSWC כבסיס, ועליו augmentation של pitch, עוצמה ו-reverb. ברוסית ובערבית מעט מאוד מופעים של "הצילו" (6 ו-11–25).

---

## 4. איסוף עצמי מיוטיוב: ניתוח משפטי ואתי (**לא ייעוץ משפטי**)

### 4.1 מה נקרא

1. **YouTube Terms of Service** (https://www.youtube.com/static?template=terms, ‏"Effective as of January 5, 2022"; לא נמצאה גרסה מאוחרת יותר בדף שנקרא).
   - Permissions and Restrictions, סעיף 1: אסור "access, reproduce, download, distribute… any Content", למעט "as specifically permitted by the Service", ‏"with prior written permission from YouTube and, if applicable, the respective rights holders", או "as permitted by applicable law".
   - סעיף 3: אסור "using any automated means (such as robots, botnets or scrapers)", באותם חריגים.
2. **חוק זכות יוצרים, התשס"ח-2007**, סעיף 19. הנוסח מוויקיטקסט (`action=raw`), שמפנה ל-ס"ח תשס"ח 38 (https://fs.knesset.gov.il/17/law/17_lsr_300007.pdf):
   > (א) שימוש הוגן ביצירה מותר למטרות כגון אלה: לימוד עצמי, מחקר, ביקורת, סקירה, דיווח עיתונאי, הבאת מובאות, או הוראה ובחינה על ידי מוסד חינוך.
   > (ב) … יישקלו, בין השאר, כל אלה: (1) מטרת השימוש ואופיו; (2) אופי היצירה שבה נעשה השימוש; (3) היקף השימוש, מבחינה איכותית וכמותית, ביחס ליצירה בשלמותה; (4) השפעת השימוש על ערכה של היצירה ועל השוק הפוטנציאלי שלה.

   רלוונטיים גם סעיף 22 (שימוש אגבי) וסעיף 26 (העתקה זמנית), שנקראו באותו מקור.
3. **משרד המשפטים, "חוות דעת: שימושים בתכנים מוגנים בזכויות יוצרים לצורך למידת מכונה"**, 18 בדצמבר 2022. מקור: https://www.gov.il/BlobFolder/legalinfo/machine-learning/he/machine-learning.pdf (‏30 עמודים, נקרא ב-pdftotext).
   > "מלבד מקרים חריגים, שימוש בתכנים מוגנים בזכויות יוצרים לצורך אימון מכונה חוסה תחת הסדרי השימושים המותרים… שימוש בתכנים מוגנים לשם אימון מכונה יהווה לרוב שימוש הוגן… ככל שהנתונים המשמשים ללמידה נמחקים בתום התהליך, יחול גם סעיף 26."
   > "חוות דעת זו אינה משתרעת על הגבלות לשימוש בתכנים שמקורם בדינים אחרים לבר מזכויות יוצרים, כגון דיני הפרטיות, חסיונות, בטחון המדינה, אתיקה."

   על תנאי שימוש של פלטפורמות היא אומרת: "עלולים לסכל את הפרשנות דכאן באמצעות תנאי שימוש האוסרים איסוף מידע". לצד זה: "ישנו בסיס איתן לסברה כי תניה האוסרת על איסוף מידע באופן קטגורי תוחזק כתניה מקפחת" (חוק החוזים האחידים). חוות הדעת אינה מחייבת בית משפט.
4. **נוהג המחקר (AudioSet):** Google מפיצה רק "YouTube video IDs with start and end times", תוויות ו-features נגזרים, ולא אודיו.
5. **YouTube Creative Commons** (https://support.google.com/youtube/answer/2797468): "other creators can reuse your work subject to the terms of the CC BY license". ייחוס נדרש: כותרת, יוצר, URL ו-"is licensed under CC BY".

### 4.2 ניתוח (מסקנות שלי, לא ממקור)

- **זכויות יוצרים (ישראל):** אימון מסווג על אודיו לצורך מחקר לא מסחרי, בלי הפצה, נראה כ-"שימוש הוגן" לפי סעיף 19 וחוות הדעת. מחיקת האודיו בסוף התהליך מחזקת את הטענה (סעיף 26).
- **חוזה (ToS):** הורדה עוקפת את התנאים של YouTube. החריג "as permitted by applicable law" וטענת התניה המקפחת אינם ודאיים. **הסיכון החוזי קיים**, גם אם נמוך במחקר לא מסחרי. הפצה חוזרת של אודיו (כמו מראות HF) מגדילה את הסיכון באופן משמעותי.
- **פרטיות ואתיקה (זה העיקר):** סרטוני תקיפה וקטטות אמיתיים מציגים קורבנות ועוברי אורח שלא הסכימו. לעיתים מדובר בקטינים, ולעיתים במידע רגיש לפי תיקון 13 לחוק הגנת הפרטיות (ראו inventory). חוות הדעת **מחריגה במפורש** את הפרטיות והאתיקה.

### 4.3 אמצעי הגנה מומלצים

1. **אישור ועדת אתיקה (IRB/Helsinki מוסדית) לפני האיסוף**, כולל הגדרת סוגי תוכן אסורים: למשל קטינים ואלימות מינית.
2. **שמירה מינימלית:**
   - שומרים רשימת `(video_id, start, end, label)` ו-embeddings של YAMNet (1024-d לכל 0.48 s).
   - האודיו הגולמי נשמר זמנית בלבד, באחסון מוצפן ומוגבל, ונמחק אחרי חילוץ ה-embeddings (תואם את ההיגיון של סעיף 26).
   - **לא שומרים וידאו.**
3. **אין הפצה** של אודיו או קטעים. משתפים רק IDs ו-timestamps, בדומה ל-AudioSet.
4. **אין זיהוי:** אין ASR מלא, אין speaker-ID, ואין face/person ID. התיוג נעשה רק על הסיגנל האקוסטי.
5. **רווחת המתייגים:** חשיפה לתוכן טראומטי. יש להגביל שעות, לאפשר opt-out ולתת תמיכה.
6. **העדפת חלופה נקייה:**
   - YouTube עם פילטר Creative Commons.
   - Freesound עם רישיון לכל קטע (CC0/BY/BY-NC), כמו ב-FSD50K.
   - Wikimedia Commons: חיפוש API ‏`scream filetype:audio` החזיר 233 תוצאות, ו-`crying filetype:audio` החזיר 921. אלה ספירות חיפוש, לא קטעים מאומתים.
   - הקלטות מבוימות עם שחקנים בעברית, ערבית, רוסית ואמהרית, בהסכמה. **זה המקור היחיד ל-"הצילו" צעוק בעברית.**

---

## 5. המלצה: תוכנית רכישה לפי סדר עדיפות

### (א) אימון ראש על embeddings של YAMNet: scream / cry / angry-shout / fight מול hard negatives

1. **AudioSet strong labels (CC BY 4.0), כבר עכשיו.**
   - מורידים את ה-TSV (כבר נבדק) ושולפים את הקטעים של Screaming, Shout, Crying/sobbing, Children shouting, Wail ו-Whimper. בסך הכול כ-5,000 קטעי train (ראו 1.1).
   - חותכים חלונות לפי גבולות הזמן ומחלצים embeddings.
   - **הנגטיבים מגיעים מאותם קטעים** (מחוץ לגבולות האירוע) ומהמחלקות Speech, Laughter, Cheering, Children playing ו-Television.
   - האודיו מושג רק בכפוף לסעיף 4.3.
2. **AudioSet unbalanced:** ‏6,104 קטעים לאיחוד מחלקות המצוקה (כ-17 שעות weak). משמשים ל-pretraining בתוויות weak עם MIL או max-pooling, ולא כ-ground truth: ב-Shout וב-Yell ה-QA הוא 5/10.
3. **FSD50K ו-ESC-50** (כבר ב-inventory): הליבה הנקייה מבחינת רישיון.
4. **רגש: CREMA-D ‏(ODbL), ‏Dusha crowd (BY-SA-like, ‏28 GB), ‏MESD ‏(CC BY), ‏RAVDESS ו-CaFE (NC).**
   - Anger ו-Fear בעוצמה גבוהה משמשים כ-"angry_shout" ו-"plead".
   - שאר הדיבור משמש כ-hard negatives.
5. **XD-Violence: רק לאחר אישור אתיקה.**
   - שימוש עדיף: ה-features של VGGish שכבר מופצים, ל-baseline.
   - או חילוץ embeddings של YAMNet מאודיו הסרטונים, עם מחיקה מיידית.
   - הרישיון לא ידוע. **לא להפיץ שום נגזרת.**

### (ב) סט מבחן רב-לשוני (held-out)

1. **AudioSet strong eval:**
   - Shout: ‏665 קטעים; Screaming: ‏155; Crying: ‏94; Children shouting: ‏45 (מה-TSV).
   - מתייגים שפה עם `mms-lid-126` (NC) ומדווחים ביצועים לפי שפה.
   - **לא לערבב** עם קטעי train מאותו video_id.
2. **מבחן cross-corpus לרגש:** אימון על שפות א' ומבחן על שפות ב'. לדוגמה, מבחן על ASED (אמהרית), ANAD (ערבית natural), RESD (רוסית), CaFE (צרפתית) ו-MESD (ספרדית). יש להשתמש בחלוקות EmoBox כדי שהתוצאות יהיו ברות השוואה.
3. **הקלטות מבוימות בעברית ובערבית**, בהסכמה ובמכשירי היעד (שולחן, כיס, תיק). זה הרכיב היחיד שמכסה עברית אמיתית. **אין תחליף ציבורי.**
4. **False alarms לשעה:** כמו ב-inventory, ובנוסף שעות TV וסרטים בשפות היעד כ-negatives קשים.

### (ג) מילות מצוקה רב-לשוניות

1. **MSWC (CC BY 4.0):** en, de, fr, es לרוב, ו-ru ו-ar במעט (ראו 3.1).
2. **סינתזה:** MMS-TTS (he, am, ar, ru; ‏NC) ו-Piper saspeech (he; NC), עם augmentation.
3. **הקלטות מתנדבים** של "הצילו", "עזרה", "תעזבו אותי", "די", "помогите", "النجدة" ו-"እርዱኝ" (האחרונה **not verified** לשונית), בטון רגיל ובצעקה.
4. **Speech Commands (CC BY 4.0):** רק ל-"stop", ל-"no" ול-background.

### סיכונים עיקריים

- **Acted מול real:** כמעט כל קורפוסי הרגש מבוימים. צעקת שחקן באולפן שונה מצרחת אימה אמיתית מטלפון בכיס.
  - **הפחתה:** מבחן נפרד על דאטה אמיתי (AudioSet strong eval) ו-re-recording דרך טלפון.
- **רעש תוויות:** בתוויות weak של AudioSet, ‏Shout ו-Yell קיבלו 5/10 ב-QA.
  - **הפחתה:** strong labels, ובדיקה ידנית של מדגם.
- **כיסוי שפות ומבטאים:** אין דאטה רגשי בעברית. בערבית מעט natural (ANAD: ‏1,384 שניות). באמהרית ASED בלבד (acted, ללא רישיון). ב-AudioSet השפות לא ידועות.
- **רישיון:** רוב החלופות NC (RAVDESS, CaFE, TESS, ANAD, MMS, SASPEECH).
  - **מודל שאומן עליהן לא יעבור למוצר מסחרי בלי אימון מחדש.**
  - יש לתעד provenance לכל קטע.
- **אתיקה:** XD-Violence, ‏RLVS ויוטיוב כוללים קורבנות אמיתיים. ראו 4.3.

---

## 6. מה לא אומת

- **שפות:** התפלגות השפות ב-AudioSet וב-XD-Violence.
- **זמינות:** כמה קטעי AudioSet זמינים כיום, פרט למראה agkphysics (ראו inventory).
- **רישיונות ותנאים:**
  - XD-Violence (אין בדף), UCF-Crime (הדף הרשמי), RLVS (נוכחות אודיו).
  - Emo-DB, ‏ASED (אין LICENSE), ‏EYASE.
  - IEMOCAP (ה-PDF לא נקרא).
  - MSP-Podcast לשימוש מסחרי (המקורות סותרים).
  - קולות Piper בערבית וברוסית.
- **גישה:** קורפוסי האגרסיה ההולנדיים (אין נתיב הורדה ציבורי). דאטהסט בשם "SAD" לא נמצא.
- **החלה:** החלת רישיון `golos/license` על Dusha היא הסקה, אין קובץ נפרד ל-Dusha.
- **מחיר:** KSUEmotions ב-LDC.
- **ספירות:**
  - ספירת התוצאות ב-Freesound (הדף לא החזיר מספר).
  - רמת הכיסוי האמיתית של פילטר CC ביוטיוב.
- **משפט:**
  - האם קיימת גרסה מעודכנת ל-YouTube ToS אחרי 5 בינואר 2022.
  - תחולת חוק הגנת הפרטיות (תיקון 13) על embeddings נגזרים. נדרשת חוות דעת משפטית.
