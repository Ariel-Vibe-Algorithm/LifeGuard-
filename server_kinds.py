# -*- coding: utf-8 -*-
"""סוגי דיווחי רחוב (תואם ל-server/safety_intel.REPORT_KINDS) - לשימוש הממשק בלי לייבא את השרת."""

REPORT_CHOICES = (("dark_street", "רחוב חשוך / תאורה לא עובדת"), ("broken_light", "פנס תקול"),
                  ("harassment", "הטרדה"), ("unsafe_crossing", "מעבר חצייה מסוכן"),
                  ("isolated_area", "אזור מבודד / ללא תנועה בלילה"), ("safe_place", "מקום בטוח / פתוח בלילה"))
