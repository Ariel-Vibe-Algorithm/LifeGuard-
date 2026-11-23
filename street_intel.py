# -*- coding: utf-8 -*-
"""
street_intel.py - Global Street Intelligence: תשתית בלבד.

מה קיים:
- מודל נתונים (StreetSegment, Observation, SafetyScore) + ולידציה, תואם לטבלאות בשרת
  (street_segments / street_observations / street_scores, מיגרציה 2 בשרת).
- ממשק ScoringEngine שמנוע עתידי יממש, עם כללי "אין ציון בלי ראיות" מובנים.
- overlay_for(): מה שהמפה מציגה. כרגע תמיד "אין נתונים" - ואסור להחזיר ציון שלא חושב.

מה לא קיים (בכוונה): קליטת וידאו/תמונות, עיבוד תמונות לוויין/אוויר, ייבוא נתוני עירייה,
אימות דיווחים, ומנוע הניקוד עצמו. אין שום ציון מוצג למשתמש עד שאלה ייבנו ויאומתו.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

NO_DATA_TEXT = ("אין עדיין נתוני בטיחות לרחובות באזור זה. LifeGuard לא מציג ציוני סיכון משוערים - "
                "הדירוג יופיע רק כשיהיו נתונים מאומתים (דיווחים, תשתיות עירוניות, תמונות).")

OBSERVATION_KINDS = ("user_video", "user_image", "satellite", "aerial", "municipal", "user_report")
VERIFICATION_STATES = ("unverified", "verified", "rejected")

# כללי סף שמנוע עתידי חייב לכבד לפני שמוצג ציון כלשהו
MIN_OBSERVATIONS_FOR_SCORE = 5
MIN_INDEPENDENT_SOURCES = 2
MAX_SCORE_AGE_DAYS = 180


@dataclass
class StreetSegment:
    id: str
    geometry: List[tuple]                  # [(lat, lon), ...] - קטע רחוב (OSM way או דומה)
    source: str                            # "osm" | "municipal" | ...
    source_ref: Optional[str] = None
    name: Optional[str] = None


@dataclass
class Observation:
    id: str
    kind: str                              # אחד מ-OBSERVATION_KINDS
    segment_id: Optional[str]
    lat: float
    lon: float
    observed_at: float
    submitted_by: Optional[str] = None     # None למקורות מוסדיים
    media_ref: Optional[str] = None        # הפניה לאחסון פרטי מוצפן - לא URL ציבורי
    attributes: Dict[str, object] = field(default_factory=dict)   # למשל {"lighting": "poor"}
    verification: str = "unverified"
    consent: Dict[str, object] = field(default_factory=dict)      # הסכמה לשימוש ולשמירה (חובה למשתמשים)

    def validate(self):
        errors = []
        if self.kind not in OBSERVATION_KINDS:
            errors.append("kind לא מוכר")
        if not (-90 <= self.lat <= 90 and -180 <= self.lon <= 180):
            errors.append("קואורדינטות לא תקינות")
        if self.verification not in VERIFICATION_STATES:
            errors.append("verification לא מוכר")
        if self.kind in ("user_video", "user_image", "user_report") and not self.consent.get("accepted"):
            errors.append("תצפית של משתמש דורשת הסכמה מפורשת")
        if self.kind in ("user_video", "user_image") and self.attributes.get("contains_faces") is not False:
            errors.append("מדיה של משתמשים חייבת לעבור טשטוש פנים/לוחיות לפני שמירה")
        return errors


@dataclass
class SafetyScore:
    segment_id: str
    score: Optional[float]                 # None = אין מספיק נתונים. לעולם לא ערך ברירת מחדל.
    confidence: float                      # 0..1
    risk_factors: List[str]
    observation_count: int
    independent_sources: int
    model_version: str
    computed_at: float

    def displayable(self, now):
        """מתי מותר להציג ציון למשתמש. כל תנאי חייב להתקיים."""
        return (self.score is not None
                and self.observation_count >= MIN_OBSERVATIONS_FOR_SCORE
                and self.independent_sources >= MIN_INDEPENDENT_SOURCES
                and now - self.computed_at <= MAX_SCORE_AGE_DAYS * 86400)


class ScoringEngine:
    """ממשק למנוע עתידי. אין מימוש - בכוונה."""

    model_version = None

    def score(self, segment: StreetSegment, observations: List[Observation]) -> SafetyScore:
        raise NotImplementedError("מנוע דירוג הרחובות טרם נבנה - אין להציג ציונים")


def overlay_for(client, lat, lon, radius_km):
    """מה שמסך המפה מציג על שכבת הסיכון. כרגע: אין נתונים (גם אם השרת לא זמין)."""
    d = radius_km / 111.0
    bbox = f"{lon - d:.4f},{lat - d:.4f},{lon + d:.4f},{lat + d:.4f}"
    if client is not None and getattr(client, "token", None):
        try:
            res = client.street_intel(bbox)
            if res.get("segments"):
                # מנוע עתידי: כאן יסוננו רק ציונים displayable() לפני הצגה
                return {"status": res["status"], "segments": [], "message": NO_DATA_TEXT}
        except Exception:
            pass
    return {"status": "no_data", "segments": [], "message": NO_DATA_TEXT}
