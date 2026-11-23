# -*- coding: utf-8 -*-
"""
emergency_numbers_module.py
זיהוי מספרי החירום הנכונים לפי המדינה שבה המשתמש נמצא בפועל - במקום להניח שכל אחד
יודע/זוכר את המספר המקומי (ובמיוחד קריטי בחו"ל, שם דווקא אנשים הכי פגיעים).

מקור המדינה: location_module (שמחזיר country_code מ-ip-api). אם לא ניתן לזהות -
מוצג 112, שהוא מספר החירום הבינלאומי הנפוץ ביותר ועובד ברוב מדינות העולם (כולל ניתוב
אוטומטי למוקד המקומי במדינות רבות, וגם ממכשיר נעול/בלי SIM ברוב הרשתות).

** מגבלה חשובה **: הטבלה נכונה למיטב הידע בזמן הכתיבה, אך מספרי חירום משתנים לעיתים
ואינם אחידים בתוך אותה מדינה (למשל מספרים שונים למשטרה/אמבולנס/כיבוי). זהו כלי עזר -
לא תחליף לבדיקה מקומית. בכל ספק, 112 הוא הניחוש הבטוח ביותר ברוב העולם.
"""

# מבנה: קוד מדינה (ISO-2) -> {"name": שם בעברית, "police": משטרה, "ambulance": אמבולנס,
#                              "fire": כיבוי, "general": מספר כללי אם קיים}
EMERGENCY_NUMBERS = {
    "IL": {"name": "ישראל", "police": "100", "ambulance": "101", "fire": "102", "general": None},
    "US": {"name": "ארה\"ב", "police": "911", "ambulance": "911", "fire": "911", "general": "911"},
    "CA": {"name": "קנדה", "police": "911", "ambulance": "911", "fire": "911", "general": "911"},
    "GB": {"name": "בריטניה", "police": "999", "ambulance": "999", "fire": "999", "general": "999"},
    "AU": {"name": "אוסטרליה", "police": "000", "ambulance": "000", "fire": "000", "general": "000"},
    "NZ": {"name": "ניו זילנד", "police": "111", "ambulance": "111", "fire": "111", "general": "111"},
    "RU": {"name": "רוסיה", "police": "102", "ambulance": "103", "fire": "101", "general": "112"},
    "IN": {"name": "הודו", "police": "100", "ambulance": "102", "fire": "101", "general": "112"},
    "CN": {"name": "סין", "police": "110", "ambulance": "120", "fire": "119", "general": None},
    "JP": {"name": "יפן", "police": "110", "ambulance": "119", "fire": "119", "general": None},
    "KR": {"name": "דרום קוריאה", "police": "112", "ambulance": "119", "fire": "119", "general": None},
    "BR": {"name": "ברזיל", "police": "190", "ambulance": "192", "fire": "193", "general": None},
    "MX": {"name": "מקסיקו", "police": "911", "ambulance": "911", "fire": "911", "general": "911"},
    "AR": {"name": "ארגנטינה", "police": "911", "ambulance": "107", "fire": "100", "general": "911"},
    "ZA": {"name": "דרום אפריקה", "police": "10111", "ambulance": "10177", "fire": "10111", "general": "112"},
    "EG": {"name": "מצרים", "police": "122", "ambulance": "123", "fire": "180", "general": None},
    "TR": {"name": "טורקיה", "police": "155", "ambulance": "112", "fire": "110", "general": "112"},
    "AE": {"name": "איחוד האמירויות", "police": "999", "ambulance": "998", "fire": "997", "general": "999"},
    "TH": {"name": "תאילנד", "police": "191", "ambulance": "1669", "fire": "199", "general": "191"},
    "CH": {"name": "שווייץ", "police": "117", "ambulance": "144", "fire": "118", "general": "112"},
    "NO": {"name": "נורווגיה", "police": "112", "ambulance": "113", "fire": "110", "general": "112"},
    "UA": {"name": "אוקראינה", "police": "102", "ambulance": "103", "fire": "101", "general": "112"},
}

# כל מדינות האיחוד האירופי + EFTA שבהן 112 הוא המספר האחיד לכל שירותי החירום
_EU_112_COUNTRIES = {
    "AT": "אוסטריה", "BE": "בלגיה", "BG": "בולגריה", "HR": "קרואטיה", "CY": "קפריסין",
    "CZ": "צ'כיה", "DK": "דנמרק", "EE": "אסטוניה", "FI": "פינלנד", "FR": "צרפת",
    "DE": "גרמניה", "GR": "יוון", "HU": "הונגריה", "IE": "אירלנד", "IT": "איטליה",
    "LV": "לטביה", "LT": "ליטא", "LU": "לוקסמבורג", "MT": "מלטה", "NL": "הולנד",
    "PL": "פולין", "PT": "פורטוגל", "RO": "רומניה", "SK": "סלובקיה", "SI": "סלובניה",
    "ES": "ספרד", "SE": "שוודיה", "IS": "איסלנד", "LI": "ליכטנשטיין",
}
for _code, _name in _EU_112_COUNTRIES.items():
    EMERGENCY_NUMBERS.setdefault(_code, {
        "name": _name, "police": "112", "ambulance": "112", "fire": "112", "general": "112"
    })

_FALLBACK = {
    "name": "לא זוהה מיקום", "police": "112", "ambulance": "112", "fire": "112", "general": "112"
}


def get_emergency_numbers(country_code):
    """
    מחזיר dict מספרי חירום למדינה. אם המדינה לא מוכרת/לא זוהתה - מחזיר 112 כברירת מחדל
    בטוחה (המספר הבינלאומי הנפוץ ביותר), עם סימון is_fallback=True כדי שה-UI יוכל להבהיר
    שזה ניחוש ולא ודאות.
    """
    code = (country_code or "").strip().upper()
    data = EMERGENCY_NUMBERS.get(code)
    if data is None:
        return {**_FALLBACK, "country_code": code or None, "is_fallback": True}
    return {**data, "country_code": code, "is_fallback": False}


def format_primary_number(numbers):
    """
    מחזיר את המספר הבודד הכי רלוונטי להצגה בולטת במסך (משטרה במצב חירום אישי,
    או המספר הכללי אם הוא מאחד את כל השירותים).
    """
    return numbers.get("general") or numbers.get("police") or "112"


def format_summary_line(numbers):
    """שורת סיכום קצרה להצגה מתחת למספר הראשי, כשיש מספרים נפרדים לשירותים שונים."""
    police = numbers.get("police")
    ambulance = numbers.get("ambulance")
    fire = numbers.get("fire")
    if police == ambulance == fire:
        return "כל שירותי החירום"
    parts = []
    if police:
        parts.append(f"משטרה {police}")
    if ambulance:
        parts.append(f"אמבולנס {ambulance}")
    if fire:
        parts.append(f"כיבוי {fire}")
    return " · ".join(parts)
