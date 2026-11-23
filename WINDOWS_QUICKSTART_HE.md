# LifeGuard — הפעלה ב-Windows

## דרישות
- Windows 10/11 ‏64-bit.
- Python 3.11 ‏64-bit מ-python.org; לסמן Add Python to PATH.
- מיקרופון והרשאות Windows עבור יכולות שמע.

## הפעלה ראשונה
1. חלץ את תיקיית lifeguard למיקום קבוע, למשל C:\LifeGuard.
2. לחץ פעמיים על INSTALL_WINDOWS.bat.
3. לאחר סיום לחץ פעמיים על RUN_LIFEGUARD_WINDOWS.bat.

## יצירת EXE
במחשב Windows, אחרי ההתקנה, לחץ פעמיים על BUILD_WINDOWS_EXE.bat.
היישום ייבנה אל dist\LifeGuard\LifeGuard.exe. יש להפיץ את כל תיקיית dist\LifeGuard.

## מגבלות חשובות
- לא ניתן לבנות EXE תקין ל-Windows מתוך סביבת Linux זו; יש להריץ את תהליך הבנייה במחשב Windows.
- PyAudio אופציונלי ותלוי בגלגל התקנה תואם לגרסת Python.
- שיתוף התרעות/מיקום/מדיה בין מחשבים דורש backend מאובטח ופרוס; הממשק המקומי אינו שרת ענן.
- יש לבצע בדיקות במחשב היעד. אין להסתמך על התוכנה כאמצעי חירום יחיד.
