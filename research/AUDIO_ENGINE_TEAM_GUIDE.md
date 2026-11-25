# מנוע הסכנה הקולי – מדריך לצוות

המסמך מסביר מה השתנה במנוע הקולי של LifeGuard, מה גילינו, ואיך כל חבר/ת צוות יכול/ה להריץ את הכלים בעצמו/ה.

---

## 1. מה גילינו (בקצרה)

- **הקול לבד לא מספיק.** גם אחרי אימון על כ-8,000 הקלטות, המודל הטוב ביותר מזהה כ-15–18% ממקרי המצוקה בהתראת שווא אחת לשעה.
  להגיע ל-70% רק בהורדת הסף יעלה עשרות עד מאות התראות שווא בשעה – לא שמיש באפליקציה שמאזינה כל היום.
- **קול מצוקה השתפר:** 6.2% → 15.5% בלי אף התראת שווא נוספת (הקטגוריה היחידה שעברה את השער). עדיין לא באפליקציה – מחכה לכיול מחדש של כלל "צעקות חוזרות".
- **אזעקה וחבטה:** הזיהוי עלה פי 3, אבל עם הרבה יותר התראות שווא – לא עברו.
- **בכיס הטלפון** הזיהוי נופל מ-52% ל-21% – רק הקלטות אמיתיות מהטלפון יסגרו את הפער.
- **צעקת פחד וצעקת שמחה לא נבדלות** (AUC ≈ 0.54). לכן ההחלטה היא לשאול את המשתמש "הכל בסדר?" ולא להפעיל SOS מצליל.
- **הפתרון הוא מערכת:** הקול אומר מה קורה עכשיו, רמת הסיכון של האזור אומרת מתי להיות ערניים, והמשתמש מאשר.

דוחות מלאים: `research/hazard_v3_freesound.md`, `research/freesound_dataset_summary.md`, `research/hazard_eval_stream.md`.

---

## 2. מה השתנה בקוד

**נכנס ל-main (נבדק):**

| קובץ | מה זה |
|---|---|
| `research_tools/freesound_collect.py` | איסוף מ-Freesound: רק CC0/CC-BY, בלי קטעי FSD50K, פיצול לפי מעלה, כללי ניקוי תיוג (`audit`) |
| `research_tools/hv3_freesound.py` | אימון מחדש + שער לפני/אחרי מחמיר + `promote` עם גיבוי |
| `research_tools/hazard_eval_stream.py` | **סקריפט מדידה אחד לכל המודלים**: סריקת ספים מדויקת, התראות שווא כזרם, רווחי סמך, סף מוחזק לפי מעלה. נכתב ע"י סוכן כותב ואושר ע"י סוכן בודק |
| `tests/test_hazard_eval_stream.py` | בדיקות לסקריפט המדידה (6 עוברות) |
| `research/hazard_v3_freesound.md`, `research/hazard_eval_stream.md`, `research/freesound_dataset_summary.md` | הדוחות |

**לא נכנס עדיין – בענף `wip-audio-checkin` (לא נבדק!):** חיבור הקול ל"הכל בסדר?" (`wellness_check.py`, `engine.py`, `hazard_monitor.py`, `main.py`).
העבודה נעצרה באמצע, והבודק לא אישר אותה. לא למזג בלי בדיקה והרצת `tests/test_wellness_check.py tests/test_risk_module.py`.

**עוד לא נעשה:** שילוב רמת הסכנה של האזור עם המיקום (ערנות גבוהה יותר בלילה באזור בסיכון גבוה – האזור לבד לעולם לא מתריע), ומחקר "2 מתוך 6 חלונות".
המודלים באפליקציה (`models/`) **לא השתנו**.

---

## 3. הכנת סביבה (פעם אחת)

```bash
python -m venv .venv
.venv\Scripts\activate            # ב-Windows
pip install -r requirements.txt
pip install ai-edge-litert scipy soundfile pytest
```

**YAMNet** (לא נמצא בריפו – 16MB, רישיון Apache 2.0):

```bash
mkdir -p research/models/yamnet
curl -L -o yam.tar.gz "https://www.kaggle.com/api/v1/models/google/yamnet/tfLite/tflite/1/download"
tar -xzf yam.tar.gz            # מחלץ 1.tflite (16,096,668 בתים)
mv 1.tflite research/models/yamnet/yamnet_float_embeddings.tflite
curl -L -o research/models/yamnet/yamnet_class_map.csv https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv
```

---

## 4. איסוף הקלטות מ-Freesound

1. כל אחד יוצר **מפתח API משלו**: https://freesound.org/apiv2/apply
2. שומרים אותו כמשתנה סביבה – **אף פעם לא בקובץ בריפו** (הריפו ציבורי):
   ```powershell
   [Environment]::SetEnvironmentVariable("FREESOUND_API_KEY", "<המפתח שלך>", "User")
   ```
3. מורידים את רשימת הקטעים של FSD50K (כדי להחריג קטעים שהמודל הקיים כבר ראה):
   ```bash
   mkdir -p research_data/freesound && cd research_data/freesound
   curl -L -o gt.zip "https://zenodo.org/records/4060432/files/FSD50K.ground_truth.zip?download=1" && unzip gt.zip
   ```
4. מריצים:
   ```bash
   python -I research_tools/freesound_collect.py search     # קטלוג (רק CC0 / CC-BY)
   python -I research_tools/freesound_collect.py audit      # כמה קטעים כל כלל ניקוי תפס
   python -I research_tools/freesound_collect.py download   # הורדה (~8,000 קבצים, ~20 דק')
   python -I research_tools/freesound_collect.py summary    # -> research/freesound_dataset_summary.md
   ```

הנתונים נשמרים ב-`research_data/` – התיקייה **לא נכנסת ל-git** (גודל + רישיונות + ייחוס לכל קטע ב-`catalog.jsonl`).

---

## 5. אימון ושער לפני/אחרי

```bash
python -I research_tools/hv3_freesound.py features   # מאפייני YAMNet לכל חלון (~3 דק')
python -I research_tools/hv3_freesound.py train      # אימון + שער -> research/hazard_v3_freesound.md
python -I research_tools/hv3_freesound.py promote    # רק קטגוריות שעברו את השער נכנסות לאפליקציה
```

**השער** (קטגוריה נכנסת רק אם כולם מתקיימים): לפחות 30 אירועים מ-10 מעלים, שיפור מובהק (McNemar p<0.05 + רווח סמך לפי מעלה),
ואין יותר חלונות שגויים מהמודל הקיים.

⚠️ **קבוצת הבדיקה הנוכחית כבר נוצלה** (רשום ב-`test_log.jsonl`). לפני סבב חדש – לאסוף קבוצת בדיקה חדשה ממעלים חדשים.
צרחה / קול מצוקה לא יקודמו בלי `--allow-voice`, ורק אחרי כיול מחדש של `models/shout_sequence.json`.

---

## 6. הדבר הכי חשוב עכשיו: בדיקת שטח

בלי נתוני שטח אי אפשר לדעת כמה התראות שווא יש באמת. כל חבר/ת צוות מריץ/ה יום אחד (5–10 שעות):

```bash
python -m research_tools.field_test live --out research_data/field/<שם>_day1.jsonl --hours 8
```

- **שום אודיו לא נשמר** – רק ציון ושעה לכל חלון (פרטיות, וגם חוקי: לא מקליטים אנשים אחרים).
- בסוף היום מסמנים בקובץ `labels.csv` אילו התראות היו אמיתיות, ומריצים:
  ```bash
  python -m research_tools.field_test report research_data/field/*.jsonl --labels labels.csv
  ```
- שולחים את קובץ ה-jsonl (לא אודיו) לדניאל.

---

## 7. ההגדרה המוסכמת ל"70% זיהוי"

- **אירוע:** אירוע סכנה מבוים, בהסכמה, 20 שניות ומעלה, מאומת ע"י שני אנשים, מוקלט בטלפון אמיתי (יד / תיק / כיס, רחוב / בית / אוטובוס).
- **זוהה:** שאלת "הכל בסדר?" שקטה או SOS תוך 30 שניות.
- **תקציב:** לכל היותר שאלה אחת לכ-8 שעות, נמדד על 50–100 שעות רקע אמיתי.
- **שתי עמודות, לא מאחדים:** "המודל" (זיהוי פסיבי בלבד, לכל קטגוריה) ו"המערכת" (כולל לחיצה על SOS ומילות מפתח).
