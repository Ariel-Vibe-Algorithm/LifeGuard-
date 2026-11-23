# -*- coding: utf-8 -*-
"""
contacts_import.py
ייבוא אנשי קשר מקובץ שיוצא מהטלפון / Google Contacts / Outlook (vCard ‎.vcf או CSV).

ב-Windows אין לאפליקציית שולחן גישה לספר הכתובות של הטלפון, ולכן "אנשי קשר מהמכשיר" נתמך
דרך קובץ ייצוא. הקובץ נקרא מקומית בלבד - שום דבר ממנו לא נשלח לשרת. רק המספר שהמשתמש בוחר
להזמין נשלח (והשרת שומר ממנו hash בלבד).
"""

import csv
import io
import re

MAX_CONTACTS = 5000


def _clean_phone(value):
    value = (value or "").strip()
    digits = re.sub(r"\D", "", value)
    if not 8 <= len(digits) <= 15:
        return None
    return ("+" if value.startswith("+") else "") + digits


def _unfold(text):
    # vCard: שורה שמתחילה ברווח/טאב היא המשך של הקודמת (RFC 6350 §3.2)
    return re.sub(r"\r?\n[ \t]", "", text)


def _vcard_value(line):
    return line.split(":", 1)[1].strip() if ":" in line else ""


def _decode_qp(value, params):
    if "QUOTED-PRINTABLE" not in params.upper():
        return value
    import quopri
    charset = "utf-8"
    m = re.search(r"CHARSET=([\w-]+)", params, re.I)
    if m:
        charset = m.group(1)
    try:
        return quopri.decodestring(value.encode("ascii", "ignore")).decode(charset, "replace")
    except LookupError:
        return value


def parse_vcf(text):
    out = []
    for block in re.split(r"(?im)^BEGIN:VCARD\s*$", _unfold(text)):
        name, phones = "", []
        for line in block.splitlines():
            key, _, _ = line.partition(":")
            head = key.split(";")[0].upper()
            head = head.split(".")[-1]  # "item1.TEL" (ייצוא של Apple)
            params = key[len(key.split(";")[0]):]
            if head == "FN":
                name = _decode_qp(_vcard_value(line), params) or name
            elif head == "N" and not name:
                parts = _decode_qp(_vcard_value(line), params).split(";")
                name = " ".join(p for p in (parts[1:2] + parts[:1]) if p).strip()
            elif head == "TEL":
                p = _clean_phone(_vcard_value(line).replace("tel:", ""))
                if p and p not in phones:
                    phones.append(p)
        for p in phones:
            out.append({"name": name or p, "phone": p})
            if len(out) >= MAX_CONTACTS:
                return out
    return out


def parse_csv(text):
    """CSV עם כותרות (Google: 'Name' / 'Phone 1 - Value'; Outlook: 'First Name', 'Mobile Phone')."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    fields = reader.fieldnames
    name_cols = [f for f in fields if re.fullmatch(r"(?i)\s*(name|full name|display name|שם|שם מלא)\s*", f or "")]
    first = next((f for f in fields if re.fullmatch(r"(?i)\s*(first name|given name|שם פרטי)\s*", f or "")), None)
    last = next((f for f in fields if re.fullmatch(r"(?i)\s*(last name|family name|שם משפחה)\s*", f or "")), None)
    phone_cols = [f for f in fields if re.search(r"(?i)phone|mobile|tel|טלפון|נייד", f or "")
                  and not re.search(r"(?i)type|label", f or "")]
    out = []
    for row in reader:
        name = next((row[c].strip() for c in name_cols if (row.get(c) or "").strip()), "")
        if not name:
            name = " ".join(x for x in ((row.get(first) or "").strip() if first else "",
                                        (row.get(last) or "").strip() if last else "") if x)
        seen = set()
        for c in phone_cols:
            for raw in re.split(r"\s*:::\s*|[;,]", row.get(c) or ""):  # Google מצרף כמה מספרים עם ' ::: '
                p = _clean_phone(raw)
                if p and p not in seen:
                    seen.add(p)
                    out.append({"name": name or p, "phone": p})
        if len(out) >= MAX_CONTACTS:
            break
    return out


_OUTLOOK_PHONE_FIELDS = ("MobileTelephoneNumber", "PrimaryTelephoneNumber", "HomeTelephoneNumber",
                         "Home2TelephoneNumber", "BusinessTelephoneNumber", "Business2TelephoneNumber",
                         "CarTelephoneNumber", "OtherTelephoneNumber")
OL_FOLDER_CONTACTS = 10
OL_CONTACT_CLASS = 40


def outlook_items_to_contacts(items):
    """ממיר פריטי אנשי קשר של Outlook (אובייקטי COM) לרשימת {name, phone}."""
    out = []
    for it in items:
        try:
            if getattr(it, "Class", OL_CONTACT_CLASS) != OL_CONTACT_CLASS:  # רשימות תפוצה וכו'
                continue
            name = (getattr(it, "FullName", "") or getattr(it, "CompanyName", "") or "").strip()
            seen = set()
            for field in _OUTLOOK_PHONE_FIELDS:
                p = _clean_phone(getattr(it, field, "") or "")
                if p and p not in seen:
                    seen.add(p)
                    out.append({"name": name or p, "phone": p})
        except Exception:  # פריט פגום/נעול - מדלגים
            continue
        if len(out) >= MAX_CONTACTS:
            break
    out.sort(key=lambda c: c["name"])
    return out


def outlook_available():
    try:
        import win32com.client  # noqa: F401
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Outlook.Application"))
        return True
    except Exception:
        return False


class OutlookUnavailable(Exception):
    pass


def load_outlook():
    """אנשי הקשר של Outlook הקלאסי (כולל אנשי קשר שמסונכרנים מהטלפון דרך חשבון Microsoft/Exchange).
    קריאה מקומית בלבד.

    לא מפעילים את Outlook בעצמנו: בבדיקה על מחשב אמיתי, הפעלה אוטומטית פתחה את אשף ההגדרה של
    Outlook 2016 (אין פרופיל) והקריאה נתקעה. לכן מתחברים רק ל-Outlook קלאסי שכבר פתוח.
    Outlook "החדש" (olk.exe) לא תומך בגישה תכנותית בכלל - שם משתמשים בייצוא קובץ."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise OutlookUnavailable("pywin32 לא מותקן")
    pythoncom.CoInitialize()
    try:
        try:
            app = win32com.client.GetActiveObject("Outlook.Application")
        except Exception:
            raise OutlookUnavailable(
                "Outlook הקלאסי לא פתוח. אם את/ה משתמש/ת ב-Outlook החדש - הוא לא מאפשר גישה לאנשי קשר; "
                "ייצא/י אותם לקובץ (people.live.com ← ייצוא) ובחר/י 'מקובץ אנשי קשר'.")
        folder = app.GetNamespace("MAPI").GetDefaultFolder(OL_FOLDER_CONTACTS)
        return outlook_items_to_contacts(list(folder.Items))
    finally:
        pythoncom.CoUninitialize()


def load_file(path):
    with open(path, "rb") as fh:
        raw = fh.read(5 * 1024 * 1024)
    # utf-16 רק עם BOM (Outlook) - אחרת כל קובץ באורך זוגי "מתפענח" לזבל
    encodings = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1255", "latin-1")
    for enc in encodings:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if path.lower().endswith(".vcf") or "BEGIN:VCARD" in text[:2000].upper():
        contacts = parse_vcf(text)
    else:
        contacts = parse_csv(text)
    contacts.sort(key=lambda c: c["name"])
    return contacts
