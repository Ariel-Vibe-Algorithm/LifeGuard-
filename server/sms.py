# -*- coding: utf-8 -*-
"""
sms.py - ספק SMS לשרת: גיבוי SMS לאנשי קשר שלא אישרו קבלה באפליקציה, שליחת קוד הזמנה,
ואימות מספר טלפון.

ספק מובנה: Twilio (Messaging + Verify) דרך REST, בספרייה הסטנדרטית בלבד.
מוגדר רק כשיש משתני סביבה:
  LIFEGUARD_TWILIO_ACCOUNT_SID, LIFEGUARD_TWILIO_AUTH_TOKEN,
  LIFEGUARD_TWILIO_FROM (מספר/Messaging Service SID לשליחה),
  LIFEGUARD_TWILIO_VERIFY_SID (לאימות טלפון - אופציונלי).
בלי הגדרה: NullSmsProvider - כל ניסיון מחזיר "not_configured" ולעולם לא מדווח "נשלח".
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class SmsError(Exception):
    pass


class NullSmsProvider:
    name = "none"
    configured = False
    can_verify = False

    def send(self, to, text):
        raise SmsError("ספק SMS לא מוגדר בשרת")

    def start_verification(self, to):
        raise SmsError("אימות SMS לא מוגדר בשרת")

    def check_verification(self, to, code):
        raise SmsError("אימות SMS לא מוגדר בשרת")


class TwilioSmsProvider:
    name = "twilio"
    configured = True

    def __init__(self, sid, token, sender, verify_sid=None, timeout=10):
        self.sid, self.token, self.sender, self.verify_sid, self.timeout = sid, token, sender, verify_sid, timeout
        self.can_verify = bool(verify_sid)

    def _post(self, url, fields):
        data = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("message", "")
            except Exception:
                msg = ""
            raise SmsError(f"Twilio {e.code}: {msg}"[:200])
        except (urllib.error.URLError, OSError) as e:
            raise SmsError(f"Twilio לא זמין: {e}"[:200])

    def send(self, to, text):
        key = "MessagingServiceSid" if self.sender.startswith("MG") else "From"
        res = self._post(f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                         {"To": to, key: self.sender, "Body": text})
        return res.get("sid")

    def start_verification(self, to):
        if not self.verify_sid:
            raise SmsError("Twilio Verify לא מוגדר")
        res = self._post(f"https://verify.twilio.com/v2/Services/{self.verify_sid}/Verifications",
                         {"To": to, "Channel": "sms"})
        return res.get("status")

    def check_verification(self, to, code):
        if not self.verify_sid:
            raise SmsError("Twilio Verify לא מוגדר")
        res = self._post(f"https://verify.twilio.com/v2/Services/{self.verify_sid}/VerificationCheck",
                         {"To": to, "Code": code})
        return res.get("status") == "approved"


def from_env():
    sid = os.environ.get("LIFEGUARD_TWILIO_ACCOUNT_SID")
    token = os.environ.get("LIFEGUARD_TWILIO_AUTH_TOKEN")
    sender = os.environ.get("LIFEGUARD_TWILIO_FROM")
    if sid and token and sender:
        return TwilioSmsProvider(sid, token, sender, os.environ.get("LIFEGUARD_TWILIO_VERIFY_SID"))
    return NullSmsProvider()
