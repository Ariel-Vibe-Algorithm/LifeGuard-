# -*- coding: utf-8 -*-
"""
ring.py - "שרשרת צלצול" לאנשי הקשר: הקרוב ביותר מקבל צפצוף; אם לא ענה - עוברים לבא.

למה לא לכולם בבת אחת: התראה לכולם יוצרת "כל אחד חושב שמישהו אחר יענה", ומציפה. צלצול מסודר
לפי קרבה מבטיח שמישהו אחד אחראי בכל רגע, והקרוב ביותר יכול להגיע הכי מהר.

כללים:
- סדר: לפי מרחק מהמיקום האחרון של האירוע (נוכחות עדכנית של איש הקשר, עד FRESH_SECONDS).
  איש קשר שמיקומו לא ידוע - אחרי בעלי מיקום, לפי ותק הקשר (מי שחובר קודם).
- בכל רגע מצלצל רק אחד. אין תגובה אנושית תוך RING_SECONDS -> הצפצוף נעצר אצלו ועוברים לבא.
  (הוא עדיין רואה את האירוע ויכול לענות מאוחר יותר.)
- מישהו קיבל ("אני מטפל/ת") -> השרשרת נעצרת, ושאר אנשי הקשר מקבלים עדכון שקט (המשפחה יודעת).
- כל השרשרת עברה בלי מענה -> במצוקה שקטה עוברים למתנדבים (בגלויה הם כבר מוזעקים במקביל).
"""

import math

RING_SECONDS = 45
FRESH_SECONDS = 6 * 3600


def _dist_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def order_contacts(contacts, incident_loc, now):
    """
    contacts: [{"username", "connected_at", "lat", "lon", "updated_at"}] (lat None = לא ידוע)
    -> רשימת שמות לפי סדר הצלצול.
    """
    def key(c):
        fresh = (c.get("lat") is not None and incident_loc is not None
                 and now - (c.get("updated_at") or 0) <= FRESH_SECONDS)
        d = _dist_km((incident_loc[0], incident_loc[1]), (c["lat"], c["lon"])) if fresh else float("inf")
        return (0 if fresh else 1, d, c.get("connected_at") or 0, c["username"])
    return [c["username"] for c in sorted(contacts, key=key)]
