# פריסת שרת LifeGuard בענן

בלי פריסה, השרת רץ רק במחשב שלך. אז אנשי קשר במחשבים ובטלפונים אחרים לא יקבלו התראות. המדריך הזה מקים את השרת על VPS, עם HTTPS אוטומטי.

## מה צריך
- **שרת לינוקס קטן** (VPS) עם Docker. מספיק 1 vCPU ו-1GB, למשל Hetzner, DigitalOcean או Lightsail.
- **דומיין** עם רשומת A שמצביעה לכתובת ה-IP של השרת, למשל `lifeguard.example.com`.
- **פורטים 80 ו-443 פתוחים.** Caddy מנפיק תעודת Let's Encrypt לבד.

## התקנה
```bash
git clone <המאגר שלך> lifeguard && cd lifeguard      # או העתקת התיקייה
cp deploy/.env.example deploy/.env
nano deploy/.env        # למלא: LIFEGUARD_DOMAIN, LIFEGUARD_DATA_KEY, LIFEGUARD_ADMIN_TOKEN
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
curl https://lifeguard.example.com/v1/health
```
`LIFEGUARD_DATA_KEY` מצפין מידע רפואי, טלפונים ומפתח Push. **שמור/י עותק שלו מחוץ לשרת.** בלי המפתח אי אפשר לשחזר את הנתונים המוצפנים.

**שכבת תאונות הולכי רגל ומקלטים** (`models/safety_layers.json.gz`) לא נמצאת ב-git ונבנית מקומית. אחרי עדכון קוד
שנוגע במוקדי התאונות (למשל כלל v2, 2026-11-15), בונים מחדש רק את שכבת התאונות, בלי פנייה לרשת:
```bash
python -m research_tools.build_safety_layers --accidents-only
```
בלי השלב הזה השרת ממשיך לעבוד עם הקובץ הקיים, אבל הקובץ כולל גם תאונות עם עיגון לא מדויק.

## חיבור האפליקציות
- **Windows:** יוצרים קובץ `server_url.txt` ליד `main.py`, ובו שורה אחת: `https://lifeguard.example.com`. אפשרות אחרת: משתנה הסביבה `LIFEGUARD_SERVER_URL`. כתובת `http` מחוץ למחשב המקומי נחסמת בכוונה.
- **טלפון:** פותחים את `https://lifeguard.example.com/app/` ובוחרים "הוסף למסך הבית".
  - **Android:** ב-Chrome, Push עובד מיד.
  - **iPhone:** ב-Safari, נדרש iOS 16.4 ומעלה, ו-Push עובד רק אחרי "הוסף למסך הבית".

## אימות זהות מתנדבים (חובה, ידני)
מאז 2026-11-15 **כל** מתנדב/ת, גם מי שכבר היה/ה רשום/ה, מקבל/ת קריאות רק אחרי אימות זהות. מתנדבים קיימים מקבלים
הודעה אחת אחרי העדכון, ועד שיאומתו לא יישלחו אליהם קריאות. כדאי לעדכן אותם מראש.
- **בדיקה:** `https://lifeguard.example.com/admin/identity.html`. הדף מבקש את `LIFEGUARD_ADMIN_TOKEN` (לא את סיסמת לוח
  הבקרה) ושומר אותו רק בזיכרון הלשונית. משווים את הסלפי לתמונה בת"ז ואת השם, ומאשרים או דוחים עם הערה.
- **אחרי כל החלטה התמונות נמחקות מהשרת.** בקשה שלא נבדקה תוך 30 יום נמחקת אוטומטית. כל צפייה והחלטה נרשמות ביומן.
- אין כאן זיהוי פנים אוטומטי ואין בדיקה מול משרד הפנים: האימות הוא השוואה ידנית בלבד.

## אישור מתנדבים בארגון (ידני)
```bash
docker compose -f deploy/docker-compose.yml exec lifeguard python -m server.admin list --status pending
docker compose -f deploy/docker-compose.yml exec lifeguard python -m server.admin verify <username> --note "נבדק מול מד\"א"
docker compose -f deploy/docker-compose.yml exec lifeguard python -m server.admin unsuspend <username>
```
מאשרים רק אחרי בדיקה מול הארגון, לפי מספר התעודה שהמתנדב מסר. מתנדב שקיבל 2 דיווחים משני משתמשים שונים מושעה אוטומטית, עד שמנהל בודק.

## דף הניהול (`/admin/`)
דף סטטיסטיקות, תקינות, מאגרי מידע, "איך המנוע עובד" ו"הערות לשיפור" (בדיקות חיות של טריות וכיסוי הנתונים): `https://lifeguard.example.com/admin/` (מקומית: `http://127.0.0.1:8765/admin/`).
הסיסמה **לא** נמצאת בקוד ולא במאגר - יוצרים אותה בשרת עצמו (נשמר רק hash, PBKDF2 כמו סיסמאות המשתמשים):
```bash
docker compose -f deploy/docker-compose.yml exec lifeguard python -m server.admin set-admin-password --user <שם>
# מקומית:  .venv\Scripts\python.exe -m server.admin set-admin-password --user <שם>
# אוטומציה (בלי הקלדה):  ... set-admin-password --user <שם> --password-stdin < קובץ-סוד
python -m server.admin list-admins            # מי מוגדר
python -m server.admin remove-admin --user <שם>
```
- **סיסמה חזקה חובה בשרת שחשוף לאינטרנט:** לפחות 12 תווים אקראיים (לא רק ספרות). הנעילה (5 ניסיונות כושלים לשם+כתובת ב-15 דקות, 20 לכתובת) מאטה ניחוש אבל לא מונעת אותו לגמרי. הכלי מזהיר על סיסמה חלשה.
- החלפת סיסמה מנתקת מיד את כל הכניסות הקיימות של החשבון. כניסה תקפה 8 שעות ונשמרת רק בלשונית הדפדפן.
- כל כניסה, יציאה וכישלון נרשמים ביומן הביקורת (`actor = admin:<שם>`).
- הלוח מציג **סיכומים מצטברים בלבד** - בלי שמות, הודעות, מיקומים או טלפונים. כניסה בשם וסיסמה פותחת **רק** את לוח הבקרה. נתיבי הניהול האחרים (`/v1/admin/volunteers` וכו', שבהם יש שמות משתמש ופעולות) דורשים את `LIFEGUARD_ADMIN_TOKEN` של השרת, כך שסיסמת דף שנוחשה לא חושפת אותם.

## SMS (אופציונלי, בתשלום)
- **צריך** חשבון Twilio, מספר שולח (או Messaging Service), ושירות Verify. ממלאים את משתני `LIFEGUARD_TWILIO_*` ב-`.env` ומריצים `up -d` שוב.
- **מה זה מוסיף:**
  - איש קשר שהאפליקציה שלו לא אישרה קבלה תוך דקה מקבל SMS עם קישור מיקום. זה קורה רק אם המספר שלו אומת ב-SMS.
  - אימות טלפון. אחרי אימות, הזמנה לפי טלפון לא דורשת אישור זהות נוסף.
  - שליחת קוד הזמנה ב-SMS ישירות מהשרת.
- **בלי ההגדרה הזו** האפליקציה מציגה "SMS לא מוגדר", ולעולם לא "נשלח".
- ⚠️ **לא נבדק מול Twilio אמיתי**, כי אין חשבון. נבדקה הלוגיקה בשרת מול ספק בדיקה. בפעם הראשונה כדאי לשלוח SMS בדיקה למספר שלך.

## זמן הגעה בהליכה למתנדבים (אופציונלי)
בלי הגדרה, השרת מעריך את זמן ההגעה לפי קו אוויר כפול מקדם עיקוף של 1.3. כדי לחשב לפי רחובות אמיתיים מריצים שרת OSRM **שלנו**. אסור להשתמש בשירות ניתוב ציבורי, כי הוא היה מקבל את המיקום של אדם במצוקה.

```bash
mkdir -p deploy/osrm && cd deploy/osrm
curl -LO https://download.geofabrik.de/asia/israel-and-palestine-latest.osm.pbf   # ODbL, ~110MB
docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-extract -p /opt/foot.lua /data/israel-and-palestine-latest.osm.pbf
docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-partition /data/israel-and-palestine-latest.osrm
docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-customize /data/israel-and-palestine-latest.osrm
```

אחר כך מוסיפים ל-`deploy/.env` את השורה `LIFEGUARD_OSRM_URL=http://osrm:5000`, ומפעילים:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile routing up -d
```

אם שרת הניתוב לא עונה תוך 1.5 שניות, השרת חוזר אוטומטית להערכה. חירום אף פעם לא מחכה לניתוב.

## השכמת אנדרואיד דרך FCM (מומלץ מאוד)

**למה זה חשוב:** בלי FCM, טלפון שמערכת ההפעלה הקפיאה או סגרה בו את האפליקציה (חיסכון סוללה, Doze) לא יקבל
קריאת חירום, עד שהטלפון יתעורר מעצמו.

**פרטיות:** ההודעה דרך Google היא השכמה בלבד: "יש עדכון", בלי שם, בלי מיקום ובלי תוכן. האפליקציה מתעוררת
ושולפת את הפרטים מהשרת שלנו.

**התקנה:**
1. פותחים פרויקט חינמי ב-https://console.firebase.google.com.
2. **בשביל השרת:**
   - Project settings ← Service accounts ← "Generate new private key".
   - שומרים את הקובץ בשם `deploy/secrets/firebase-service-account.json`. **הקובץ לא נכנס ל-git.**
3. מוסיפים ל-`deploy/.env` את השורה:
   `LIFEGUARD_FCM_SERVICE_ACCOUNT=/run/lifeguard-secrets/firebase-service-account.json`
   ומפעילים מחדש את השרת: `docker compose up -d`.
4. **בשביל האפליקציה:**
   - Project settings ← General ← "Add app" ← Android, עם שם החבילה `org.lifeguard.app`.
   - את הערכים מעתיקים ל-`android/firebase.properties`. ההסבר נמצא ב-`android/firebase.properties.example`.
   - בונים מחדש את ה-APK.
5. **בדיקה:**
   - `https://<הדומיין>/v1/health` צריך להראות `"fcm": "active"`.
   - בטלפון: "בדיקת מערכת" צריכה להראות "השכמה: פעיל".

## אייפון / אייפד / מק (אפליקציית הדפדפן)

אין אפליקציה מקורית לאפל. משתמשי אפל משתמשים באפליקציית הדפדפן: Safari, ואז שיתוף ← "הוספה למסך הבית". כדי שזה יעבוד:

- **HTTPS חובה.** בלי HTTPS, iOS לא מתקין את האפליקציה, לא מפעיל service worker ולא שולח התראות. כתובת `http://` ברשת המקומית לא תעבוד.
- **התראות (iOS 16.4 ומעלה, רק מאפליקציה שנוספה למסך הבית):** להגדיר `LIFEGUARD_VAPID_SUBJECT=mailto:<כתובת אמיתית>` או כתובת `https://` של השרת. בלי ההגדרה הזו, שירות ההתראות של אפל עלול לדחות את ברירת המחדל המקומית (`mailto:lifeguard@localhost`).
- **מה לא עובד באייפון** (האפליקציה עצמה מציגה זאת בפאנל "📱 מה עובד באייפון"):
  - מעקב מיקום ברקע.
  - זיהוי נפילה.
  - זיהוי מצוקה כשהאפליקציה סגורה.
  - **החלופה:** ליווי הביתה עם טיימר בשרת. הוא עובד גם כשהטלפון נעול.
- **לא נבדק על אייפון אמיתי.** נבדק רק בהדמיית אייפון ב-Chrome. רשימת הבדיקה לאייפון אמיתי נמצאת ב-ROADMAP_STATUS_HE.md, בסעיף "אפל".

## גיבוי
- הנתונים נמצאים ב-volume `lifeguard-data`, בקובץ `/data/lifeguard_server.db`. לגבות אותו כל יום, למשל:
  `docker compose ... exec lifeguard python -c "import sqlite3;s=sqlite3.connect('/data/lifeguard_server.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"`
- הגיבוי מוצפן חלקית: מידע רפואי וטלפונים מוצפנים, ושאר הנתונים לא. לכן מאחסנים אותו במקום מאובטח.

## מה עדיין לא נבדק
- אין Docker במחשב הפיתוח, ולכן `docker build` לא הורץ בפועל.
- כן נבדק: השרת רץ מתיקייה שמכילה בדיוק את מה שה-Dockerfile מעתיק (`storage.py` ו-`server/`), עם אותם משתני סביבה:
  - האזנה ל-0.0.0.0 מאחורי proxy;
  - המפתח נלקח ממשתנה הסביבה;
  - ה-health תקין;
  - כלי הניהול עובד.
- הגדרות Caddy ו-Let's Encrypt לא נבדקו. זה דורש דומיין אמיתי.
