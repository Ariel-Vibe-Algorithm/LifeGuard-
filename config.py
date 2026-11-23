# -*- coding: utf-8 -*-
"""
config.py
כל ההגדרות של המערכת במקום אחד.
ערוך את הקובץ הזה כדי להתאים את המערכת אליך - מספרי חירום, מפתחות API וכו'.
"""

import os

# ============ הגדרות טריגר ============
TRIGGER_HOTKEY = "ctrl+alt+s"      # קיצור מקלדת שמדמה את "מילת הקוד" (טריגר גלובלי)
COUNTDOWN_SECONDS = 5                # ספירה לאחור לפני הפעלת SOS מלא
AUDIO_CHUNK_SECONDS = 30             # אורך כל לולאת הקלטה

# ============ כיבוי מלא של התוכנה (נפרד מביטול אירוע!) ============
# קיצור מקלדת לכיבוי מלא; הכיבוי מאומת באמצעות סיסמת החשבון של המשתמש.
QUIT_HOTKEY = "ctrl+alt+q"          # קיצור מקלדת לכיבוי מלא של התוכנה
PANIC_HOTKEY = "ctrl+alt+p"          # עקיפה מיידית של הכל - ללא הערכת סיכון, ללא ספירה לאחור

# ============ מצב פיתוח בלבד! ============
# DEV_MODE=True מוסיף כפתור סגירה מיידית לצורכי פיתוח בלבד. להשאיר False בשימוש אמיתי.
# כפתור הפיתוח עוקף את אימות הסיסמה - לכן ברירת המחדל היא False
# לפני שהתוכנה יוצאת מבדיקות ומועברת לשימוש אמיתי בשטח!
DEV_MODE = False

# הערה: אין כאן יותר EMERGENCY_CONTACTS גלובלי - אנשי קשר מנוהלים כעת דרך auth_module.py
# (נשמרים לכל משתמש בנפרד, ב-data/users.json, מוזנים בטופס ההרשמה). ראה auth_module.get_contacts().

# ============ שרת LifeGuard (הערוץ היחיד להתראות חירום) ============
# Telegram / WhatsApp / SMS / אימייל הוסרו. כל התראה עוברת דרך שרת LifeGuard ונדחפת לאפליקציות של
# אנשי קשר מהימנים (שאישרו) ומתנדבים זמינים. מחוץ למחשב המקומי חובה https.
def _server_url():
    """סדר עדיפות: משתנה סביבה LIFEGUARD_SERVER_URL ← קובץ server_url.txt ליד התוכנה ← שרת מקומי.
    לשרת בענן: לכתוב בקובץ את הכתובת, למשל https://lifeguard.example.com (http נחסם מחוץ למחשב)."""
    if os.environ.get("LIFEGUARD_SERVER_URL"):
        return os.environ["LIFEGUARD_SERVER_URL"].strip()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_url.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            url = fh.read().strip()
        if url:
            return url
    except OSError:
        pass
    return "http://127.0.0.1:8765"


SERVER_URL = _server_url()
PRESENCE_UPDATE_SECONDS = 120   # עדכון מיקום (לא בזמן אירוע) לשרת - לחישוב מתנדבים קרובים
SHARE_MEDICAL_DEFAULT = False   # מידע רפואי נשלח למגיבים רק אם המשתמש הפעיל זאת

# ============ מיקום בזמן אמת (GPS Loop + Live Location) ============
GPS_UPDATE_INTERVAL_SECONDS = 5          # כל כמה שניות מעדכנים מיקום בזמן אירוע פעיל (היה 10, קוצר)

# ============ טיימר בטיחות ("ליווי הביתה") ============
# קובעים "אני אמור/ה להגיע תוך X דקות"; אם לא מאשרים הגעה בזמן - המערכת מפעילה אוטומטית
# אזעקה (רועשת או שקטה, לפי בחירת המשתמש בעת ההפעלה).
SAFETY_TIMER_PRESET_MINUTES = [5, 10, 15, 20, 30, 45, 60]  # אפשרויות מהירות בממשק
SAFETY_TIMER_DEFAULT_MINUTES = 20
# כמה זמן לפני תום הטיימר להתריע למשתמש שהזמן עומד להיגמר (כדי לתת הזדמנות להאריך/לאשר
# לפני שמופעלת אזעקה אמיתית) - זה מה שמונע התראות שווא מטיימר שנשכח.
SAFETY_TIMER_WARNING_BEFORE_SECONDS = 60
# הודעה לאנשי הקשר בהפעלת הטיימר (אופציונלי - מיידע אותם מראש שמישהו בדרך)
SAFETY_TIMER_NOTIFY_CONTACTS_ON_START = False
# ליווי הביתה משותף: כל כמה שניות לשלוח מיקום לשרת בזמן שהטיימר פעיל (העוקבים רואים מיקום חי)
ESCORT_LOCATION_SECONDS = 60
# בדיקת מוכנות: תזכורת אם הבדיקה האחרונה ישנה מ-X ימים
READINESS_REMINDER_DAYS = 30

# ============ הסלמה למתנדבים (מתבצעת בשרת) ============
# אם איש הקשר הראשון לא מאשר קבלה תוך X דקות - שולחים אוטומטית גם לבא בתור, וכן הלאה.
# סדר אנשי הקשר = הסדר שבו הוזנו בהרשמה/הגדרות (הראשון = הכי בכיר בשרשרת).
ESCALATION_ENABLED = True
ESCALATION_ACK_TIMEOUT_MINUTES = 2      # כמה להמתין לאישור לפני הסלמה לבא בתור

# ============ נתיבי אחסון מקומי ============
import sys as _sys
# באג שתוקן: ב-EXE שנבנה ב-PyInstaller (onefile) __file__ מצביע לתיקייה זמנית שנמחקת בכל
# סגירה - כל המשתמשים, האירועים וההקלטות היו נמחקים. ב-EXE הנתונים נשמרים ליד קובץ ה-EXE.
if getattr(_sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(_sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIO_DIR = os.path.join(DATA_DIR, "audio_recordings")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
LOG_DIR = os.path.join(DATA_DIR, "logs")
EVENTS_DB_FILE = os.path.join(DATA_DIR, "events_log.json")

for _d in (DATA_DIR, AUDIO_DIR, PHOTOS_DIR, LOG_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception as _e:
        print(f"⚠️ אזהרה: לא הצלחתי ליצור תיקיית נתונים {_d}: {_e}")
        print("בדוק שיש לך הרשאות כתיבה בתיקייה שבה נמצא הפרויקט (הימנע מ-Program Files/OneDrive מוגבל).")

# ============ הערכת רמת סכנה (Risk Assessment) ============
# ** חשוב: זו הערכה היוריסטית בלבד, לא אבחון מדעי! חובה לכייל בשטח לפני הסתמכות עליה. **
RISK_ASSESSMENT_ENABLED = True       # False = תמיד לדלג ישר ל-SOS מלא בלי הערכה (הכי בטוח, הכי פחות "חכם")

RISK_ASSESS_NOISE_SECONDS = 1.5       # כמה זמן למדוד רעש סביבתי
RISK_ASSESS_SPEECH_SECONDS = 3.0      # כמה זמן להאזין לזיהוי מילים (רץ במקביל למדידת הרעש)

# מילים/ביטויים שנחשבים סימן למצוקה - הוסף/ערוך לפי הצורך (עברית/אנגלית מעורב נתמך)
# research/multilingual_audio_eval.md (המלצות 3-5):
# - "הצילו" / "תצילו אותי" נוספו: 0 התראות שווא על 7.6 שעות דיבור רגיל בעברית/ערבית/רוסית/אנגלית (recall לא נמדד).
# - "stop" כמילה בודדת הוסר (המקור הגדול ביותר לטעויות ASR - גם בדיבור רוסי במודל האנגלי); נשאר "stop it".
# - מילים כלליות (DANGEROUS_KEYWORDS_GENERIC) נאמרות גם בדיבור רגיל ("משטרה", "help") - משקל נמוך יותר מתחינה.
DANGEROUS_KEYWORDS = [
    "הצילו", "תצילו אותי",
    "עזרה", "תעזרו לי", "משטרה", "תפסיק", "תפסיקי", "תעזוב אותי", "תעזבי אותי",
    "אל תיגע בי", "אל תיגעי בי", "תרד ממני", "לא לא לא", "מישהו יעזור",
    "help", "help me", "police", "stop it", "let me go", "don't touch me",
]
DANGEROUS_KEYWORDS_GENERIC = {"עזרה", "משטרה", "תפסיק", "תפסיקי", "help", "police"}

# סף עוצמת רעש (RMS, טווח 0.0-1.0 בערך) שמעליו נחשב "רעש חזק" (צעקה, שבירה, מכות).
# ערך התחלתי בלבד - יש לכייל אצלך בבית מול רעשים אמיתיים (טלוויזיה, שכנים, מזגן וכו') כדי למנוע false positive.
LOUD_NOISE_RMS_THRESHOLD = 0.15

# ניקוד סיכון - כל גורם שזוהה מוסיף נקודות; הסכום הכולל קובע את הרמה הסופית
RISK_SCORE_LOUD_NOISE = 1             # רעש לבדו לעולם לא מפעיל SOS אוטומטי
# מודל AI לצלילי מצוקה (acoustic_ai_module): ראיה חזקה יותר מעוצמה בלבד, אבל אותו מקור (מיקרופון) -
# לבדו מגיע לכל היותר ל-MEDIUM (חלון אימות), לא ל-SOS אוטומטי
RISK_SCORE_ACOUSTIC_AI = 3
ACOUSTIC_AI_THRESHOLD_LOGIT = 6.0     # ברירת מחדל שמרנית; הסף בפועל נלקח מכרטיס המודל
RISK_SCORE_PER_KEYWORD = 2            # תחינה מפורשת ("הצילו", "תעזוב אותי"); לבדה לא מספיקה להסלמה אוטומטית
RISK_SCORE_PER_GENERIC_KEYWORD = 1    # מילה כללית ("משטרה", "help") - נאמרת גם בדיבור רגיל
RISK_SCORE_KEYWORDS_CAP = 4
RISK_SCORE_RAPID_RETRIGGER = 1        # טריגר חוזר הוא רק אות תומך

RISK_THRESHOLD_MEDIUM = 3             # MEDIUM = חלון אימות קצר; לא מפעיל SOS מיידי
RISK_THRESHOLD_HIGH = 7               # HIGH דורש גם מספר מקורות ראיה בלתי תלויים

RAPID_RETRIGGER_WINDOW = 20            # שניות - טריגר חוזר בתוך חלון זה נחשב "retrigger מהיר"

LIVE_MONITOR_INTERVAL_SECONDS = 1.2    # קצב דגימה במצב "ניטור חי" לכיול (לא בזמן אירוע אמיתי)

# ============ מקום בטוח + ניווט (Map & Navigation) ============
SAFE_PLACE_SEARCH_RADIUS_M = 5000       # רדיוס חיפוש למקום בטוח קרוב (מטרים)

# קטגוריות מקומות בטוחים לחיפוש, לפי סדר עדיפות (הראשונה שנמצאת בה תוצאה - נבחרת).
# "tags" הם תגיות OpenStreetMap (למקרה שמשתמשים ב-Overpass). "google_type" הוא הסוג המקביל
# ב-Google Places API (למקרה שמוגדר GOOGLE_MAPS_API_KEY) - לא לכל הקטגוריות יש מקבילה ישירה
# בגוגל (למשל "בית כנסת" ספציפית ליהדות אין ב-Google Places, רק "בית תפילה" כללי).
SAFE_PLACE_CATEGORIES = [
    {"id": "police",           "label": "תחנת משטרה",              "tags": {"amenity": "police"},                                      "google_type": "police"},
    {"id": "hospital",         "label": "בית חולים",                 "tags": {"amenity": "hospital"},                                    "google_type": "hospital"},
    {"id": "fire_station",     "label": "תחנת כיבוי אש",             "tags": {"amenity": "fire_station"},                                "google_type": "fire_station"},
    # מקומות תפילה = עדיפות אחרונה בקרבת מקום (רק כשאין מקום בטוח אחר קרוב), ולפי העדפת המשתמש/ת
    # (worship_pref.py). google_type=None: ב-Google יש רק "place_of_worship" כללי, בלי דת - לא מציעים משם.
    {"id": "synagogue",        "label": "בית כנסת",                   "tags": {"amenity": "place_of_worship", "religion": "jewish"},      "google_type": None, "worship": True},
    {"id": "mosque",           "label": "מסגד",                       "tags": {"amenity": "place_of_worship", "religion": "muslim"},      "google_type": None, "worship": True},
    {"id": "church",           "label": "כנסייה",                     "tags": {"amenity": "place_of_worship", "religion": "christian"},   "google_type": None, "worship": True},
    {"id": "community_centre", "label": "מתנ\"ס / מרכז קהילתי",     "tags": {"amenity": "community_centre"},                            "google_type": None},
    {"id": "pharmacy",         "label": "בית מרקחת",                 "tags": {"amenity": "pharmacy"},                                    "google_type": "pharmacy"},
    {"id": "fuel",             "label": "תחנת דלק (פתוחה 24/7 לרוב)", "tags": {"amenity": "fuel"},                                        "google_type": "gas_station"},
    {"id": "supermarket",      "label": "סופרמרקט",                  "tags": {"shop": "supermarket"},                                    "google_type": "supermarket"},
]

# ============ Google Maps API (חלופה אמינה יותר ל-OpenStreetMap - אופציונלי) ============
# ה-API הציבורי של OpenStreetMap (Overpass/OSRM) חינמי אבל משותף לכל העולם ולעיתים חסום/עמוס.
# אם יש לך מפתח Google Maps API (יש רמה חינמית נדיבה - כ-$200 קרדיט חודשי, מכסה אלפי בקשות),
# המערכת תשתמש בו כברירת מחדל הראשונה, ותיפול חזרה ל-OpenStreetMap רק אם אין מפתח/הבקשה נכשלת.
# הוראות קבלת מפתח: console.cloud.google.com -> Enable "Places API" + "Directions API" -> Credentials.
GOOGLE_MAPS_API_KEY = os.environ.get("LIFEGUARD_GOOGLE_MAPS_KEY", "")

# ============ הגדרות רשת כלליות ============
# שרתי Overpass API מבקשים במפורש User-Agent תיאורי (לא ברירת המחדל של requests) כחלק
# מכללי השימוש ההוגן שלהם - זה גם עוזר להימנע מחסימה/הגבלה כ"תעבורת בוט חשודה".
HTTP_USER_AGENT = "LifeGuardEngine-PersonalSafetyApp/1.0 (non-commercial hobby project)"


# ============ משתמשים והרשמה (Auth) ============
USERS_DB_FILE = os.path.join(DATA_DIR, "users.json")
MAX_LOGIN_ATTEMPTS = 5                  # נסיונות כניסה שגויים לפני נעילה זמנית
LOGIN_LOCKOUT_SECONDS = 60              # משך נעילה זמנית של החשבון

# שדות legacy בלבד: נשמרים לתאימות עם פרופילים מגרסאות קודמות.
SECURITY_QUESTIONS = [
    "מה השם הפרטי של חיית המחמד הראשונה שלך?",
    "מה שם בית הספר היסודי שלמדת בו?",
    "מהי העיר בה נולדת?",
]

MAX_CONTACTS_PER_USER = 5                # מספר מקסימלי של אנשי קשר להרשמה
RELATIONSHIP_OPTIONS = ["הורה", "בן/בת זוג", "אח/אחות", "חבר/ה קרוב/ה", "עמית/ה לעבודה", "אחר"]

# ============ תנאי שימוש - חובה לאשר בהרשמה ============
TERMS_OF_SERVICE_TEXT = """זוהי גרסת פיילוט/פיתוח ולא מוצר מסחרי מוגמר. קרא/י בעיון:

1. אין ערבות: המערכת אינה מבטיחה זיהוי מדויק של מצבי סכנה, שליחה מוצלחת של התראות,
   או קבלת עזרה בזמן. תקלות תוכנה, רשת, סוללה או חומרה עלולות למנוע פעולה תקינה.
2. לא תחליף לשירותי חירום: במצב סכנה אמיתי, התקשר/י תמיד גם למשטרה/מוקד חירום (100)
   בנוסף לשימוש באפליקציה - אין להסתמך על האפליקציה בלבד.
3. זיהוי אוטומטי (רעש/מילים/פנים) הוא היוריסטי ועלול לטעות (גם לפספס מצב אמיתי וגם
   להיבהל מרעש רגיל) - יש לכייל ולבדוק לפני הסתמכות.
4. הקלטות ותמונות נשמרות מקומית על המחשב שלך בלבד. בזמן אירוע חירום נשלחים לשרת LifeGuard
   ולמי שמגיב לאירוע: שמך, מיקומך ומיקומך החי, סוג וחומרת האירוע - ומידע רפואי רק אם בחרת
   לשתף אותו. לצורך התאמת מתנדבים נשלח לשרת גם מיקומך מעת לעת. אנשי קשר מקבלים התראות
   רק אחרי שאישרו את הקשר; מתנדב רואה מיקום מדויק רק אחרי שקיבל את האירוע. זיהוי דיבור
   (מילות הקוד ומילות מצוקה) נעשה במכשיר בלבד; אודיו נשלח ל-Google רק אם הפעלת זאת במפורש.
   אתה אחראי/ת לאבטחת המחשב עצמו.
5. קוד מצוקה שקט / קוד כפייה (אם הוגדר): עשוי להשאיר תיעוד ושידור פעילים גם כשהמסך נראה
   כאילו האירוע בוטל. יש להבין את ההשלכות ולבחור אנשי קשר אמינים. קוד כפייה אינו מבטיח
   הסתרה ממערכת ההפעלה, ממי שבודק את ההרשאות או מכשלי רשת/חשמל.
6. באחריותך ליידע את אנשי הקשר שהוספת שהם עשויים לקבל התראות אוטומטיות ממך.
7. השימוש באפליקציה על אחריותך הבלעדית. המפתח/ים אינם נושאים באחריות לנזק ישיר או
   עקיף הנובע משימוש, אי-שימוש, או תקלה במערכת."""


# ============ מצלמה אוטומטית (Camera Auto-Capture) ============
CAMERA_ENABLED = True                       # מתג ראשי - כבה כדי להשבית צילום לגמרי
CAMERA_CAPTURE_INTERVAL_SECONDS = 8          # כל כמה שניות מנסה לצלם פריים חדש
CAMERA_MAX_PHOTOS_PER_EVENT = 15             # תקרה - מונע מילוי דיסק/ספאם באירוע ארוך
CAMERA_DARK_BRIGHTNESS_THRESHOLD = 35         # ממוצע בהירות פריים (0-255) - מתחת לזה = "חושך", לא מצלם
CAMERA_FLAT_TEXTURE_THRESHOLD = 8             # סטיית תקן נמוכה בפריים = תמונה אחידה (עדשה מכוסה/בד כיס), לא מצלם

# ============ מילת קוד קולית (Voice Trigger) ============
VOICE_TRIGGER_LANGUAGE = "he-IL"               # שפת זיהוי הדיבור הראשית (מודל מקומי; ראה speech_local.py)

# ============ זיהוי דיבור במכשיר (speech_local.py) ============
# כל תמלול (מילות הקוד הסודיות ומילות המצוקה) נעשה מקומית במחשב - האודיו לא יוצא מהמכשיר.
# מודלים: python speech_local.py --download he en ru  (נשמרים ב-research/models/asr/ או models/asr/).
# CLOUD_SPEECH_FALLBACK=True הוא הסכמה מפורשת לשלוח אודיו ל-Google *רק* כשאין מודל מקומי.
# שים/י לב: במצב זה גם מילות הקוד הסודיות (ביטול/שקט/כפייה) נשלחות לענן. ברירת מחדל: כבוי.
CLOUD_SPEECH_FALLBACK = False
# חיסכון בחשמל + דיוק: ברגיל זיהוי דיבור מקומי בלבד; בזמן סכנה (הערכה/ספירה/SOS/מעקב שקט) גם Google
# במקביל, לדיוק נוסף. כיבוי גלובלי כאן, או אישי בהגדרות (cloud_speech_in_danger = "0").
CLOUD_ASSIST_IN_DANGER = True
LOCAL_SPEECH_NUM_THREADS = 2                    # ליבות CPU לכל מודל (RTF≈0.12 נמדד עם 2)

# משך הקלטה בכפתור "בדוק בקול" האופציונלי בהרשמה (למילת הקוד הרגילה ולביטוי האזעקה השקטה) -
# אין ביטוי-ברירת-מחדל כפוי; המשתמש בוחר ומקליד את הניסוח בעצמו, והבדיקה היא הצעה, לא חובה.
VOICE_TEST_RECORD_SECONDS = 4.0

# ============ אזהרה קולית מרתיעה (Voice Warning) ============
VOICE_WARNING_ENABLED = True                     # מתג ראשי - כבה כדי להשבית לגמרי
VOICE_WARNING_INTERVAL_SECONDS = 15               # כל כמה שניות משמיעים את הודעת האזהרה מחדש
VOICE_WARNING_DANGEROUS_LEVELS = ("MEDIUM", "HIGH")  # רק ברמות האלה מופעל אוטומטית בזמן SOS


# ============ GPS/GNSS אמיתי (מקלט חיצוני בגרסת Desktop) ============
# יש להגדיר LIFEGUARD_GPS_PORT=COM5 (Windows) או /dev/ttyUSB0 (Linux/macOS).
# ללא מקלט GPS אמיתי, המערכת תסמן fallback לפי IP כמשוער בלבד ולא תמציא קואורדינטות.
GPS_SERIAL_PORT = os.environ.get("LIFEGUARD_GPS_PORT", "")
GPS_BAUDRATE = int(os.environ.get("LIFEGUARD_GPS_BAUDRATE", "9600"))
GPS_MAX_FIX_AGE_SECONDS = 10
ALLOW_IP_LOCATION_FALLBACK = True
IP_LOCATION_CACHE_SECONDS = 600

# ============ הערכת סכנה רב-חיישנית ============
# רעש לבדו אינו יכול להוביל להפעלת SOS אוטומטית. HIGH דורש שילוב ראיות.
RISK_ASSESS_MAX_WAIT_SECONDS = 4.0
RISK_HIGH_MIN_INDEPENDENT_SOURCES = 2
SENSOR_EVIDENCE_WINDOW_SECONDS = 20
SENSOR_MIN_CONFIDENCE = 0.65
SENSOR_SIGNAL_WEIGHTS = {
    "impact": 2,
    "fall": 3,
    "immobility": 2,
    "rapid_motion": 1,
    "abrupt_stop": 2,
    "device_separation": 1,
    "manual_wearable_sos": 10,
}


RISK_MEDIUM_COUNTDOWN_SECONDS = 3
