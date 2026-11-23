# -*- coding: utf-8 -*-
"""מיקום LifeGuard: GPS/NMEA אמיתי, עם fallback מסומן במפורש למיקום IP גס.

חשוב: אין בשום מצב יצירת קואורדינטות מדומות. מיקום IP אינו GPS ואסור להציגו
כמיקום מדויק או להשתמש בו לניווט/למיקום חי של אירוע חירום.

בגרסת desktop אפשר לחבר מקלט GNSS חיצוני דרך USB/Serial ולהגדיר
LIFEGUARD_GPS_PORT (למשל COM5 או /dev/ttyUSB0). במובייל יש לחבר בעתיד
adapter ייעודי ל-Android Fused Location / iOS Core Location.
"""

import glob
import os
import threading
import time
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except Exception:
    requests = None
    HAS_REQUESTS = False

try:
    import serial
    HAS_PYSERIAL = True
except Exception:
    serial = None
    HAS_PYSERIAL = False

try:
    from serial.tools import list_ports
except Exception:
    list_ports = None

_GPS_LOCK = threading.RLock()
_GPS_READER_THREAD = None
_GPS_STOP = threading.Event()
_GPS_LATEST = None
_GPS_PORT_IN_USE = None
_IP_CACHE = None
_IP_CACHE_AT = 0.0
_GPS_LOG_CALLBACK = None
_GPS_MISSING_DEPENDENCY_LOGGED = False


def _nmea_checksum_ok(sentence: str) -> bool:
    sentence = (sentence or "").strip()
    if not sentence.startswith("$") or "*" not in sentence:
        return False
    body, checksum = sentence[1:].rsplit("*", 1)
    try:
        calculated = 0
        for char in body:
            calculated ^= ord(char)
        return calculated == int(checksum[:2], 16)
    except (ValueError, TypeError):
        return False


def _nmea_coord(value: str, hemisphere: str) -> Optional[float]:
    """Converts NMEA ddmm.mmmm / dddmm.mmmm to decimal degrees."""
    if not value or hemisphere not in ("N", "S", "E", "W"):
        return None
    try:
        raw = float(value)
        degrees = int(raw // 100)
        minutes = raw - degrees * 100
        if not 0 <= minutes < 60:
            return None
        coord = degrees + minutes / 60.0
        if hemisphere in ("S", "W"):
            coord = -coord
        return coord
    except (TypeError, ValueError):
        return None


def parse_nmea_sentence(sentence: str):
    """Parses validated GGA or RMC sentence; returns a GPS fix dict or None."""
    sentence = (sentence or "").strip()
    if not _nmea_checksum_ok(sentence):
        return None
    fields = sentence[1:].split("*")[0].split(",")
    if not fields:
        return None
    kind = fields[0][-3:]
    try:
        if kind == "GGA" and len(fields) >= 10:
            quality = int(fields[6] or 0)
            satellites = int(fields[7] or 0)
            hdop = float(fields[8]) if fields[8] else None
            if quality <= 0:
                return None
            lat = _nmea_coord(fields[2], fields[3])
            lon = _nmea_coord(fields[4], fields[5])
            altitude = float(fields[9]) if fields[9] else None
            if lat is None or lon is None:
                return None
            # HDOP is an indicator, not a guaranteed accuracy measurement.
            estimated_accuracy = round(hdop * 5.0, 1) if hdop is not None else None
            return {
                "lat": lat, "lon": lon, "city": "GPS fix",
                "country_code": None, "country_name": None,
                "source": "GNSS/NMEA",
                "quality": "precise", "is_precise": True, "available": True,
                "accuracy": (f"HDOP {hdop:.1f}; אומדן בלבד" if hdop is not None else "GPS; דיוק לא ידוע"),
                "accuracy_m_estimate": estimated_accuracy,
                "satellites": satellites, "fix_quality": quality,
                "altitude_m": altitude, "timestamp": time.time(),
            }
        if kind == "RMC" and len(fields) >= 7 and fields[2] == "A":
            lat = _nmea_coord(fields[3], fields[4])
            lon = _nmea_coord(fields[5], fields[6])
            if lat is None or lon is None:
                return None
            return {
                "lat": lat, "lon": lon, "city": "GPS fix",
                "country_code": None, "country_name": None,
                "source": "GNSS/NMEA-RMC",
                "quality": "precise", "is_precise": True, "available": True,
                "accuracy": "GPS; דיוק לא ידוע", "accuracy_m_estimate": None,
                "satellites": None, "fix_quality": 1,
                "altitude_m": None, "timestamp": time.time(),
            }
    except (ValueError, IndexError):
        return None
    return None


def _candidate_ports():
    import config
    configured = (getattr(config, "GPS_SERIAL_PORT", "") or os.environ.get("LIFEGUARD_GPS_PORT", "")).strip()
    if configured:
        return [configured]
    if not HAS_PYSERIAL or list_ports is None:
        return []
    candidates = []
    try:
        for port in list_ports.comports():
            descriptor = f"{port.description or ''} {port.manufacturer or ''} {port.hwid or ''}".lower()
            if any(marker in descriptor for marker in ("gps", "gnss", "u-blox", "ublox", "nmea")):
                candidates.append(port.device)
    except Exception:
        pass
    return candidates


def _gps_reader_loop(port_name, baudrate):
    global _GPS_LATEST, _GPS_PORT_IN_USE
    try:
        with serial.Serial(port_name, baudrate=baudrate, timeout=1) as stream:
            with _GPS_LOCK:
                _GPS_PORT_IN_USE = port_name
            while not _GPS_STOP.is_set():
                try:
                    line = stream.readline().decode("ascii", errors="ignore").strip()
                    if not line:
                        continue
                    fix = parse_nmea_sentence(line)
                    if fix:
                        with _GPS_LOCK:
                            _GPS_LATEST = fix
                except Exception:
                    # A malformed NMEA line must not terminate the reader.
                    continue
    except Exception as exc:
        with _GPS_LOCK:
            _GPS_PORT_IN_USE = None
        callback = _GPS_LOG_CALLBACK
        if callable(callback):
            callback(f"⚠️ מקלט GPS לא זמין ב-{port_name}: {exc}")
    finally:
        with _GPS_LOCK:
            _GPS_PORT_IN_USE = None


def _ensure_gps_reader(log_callback=None):
    global _GPS_READER_THREAD, _GPS_LOG_CALLBACK, _GPS_MISSING_DEPENDENCY_LOGGED
    import config
    _GPS_LOG_CALLBACK = log_callback or _GPS_LOG_CALLBACK
    if not HAS_PYSERIAL:
        if log_callback and not _GPS_MISSING_DEPENDENCY_LOGGED and config.GPS_SERIAL_PORT:
            log_callback("⚠️ הוגדר GPS_SERIAL_PORT אך pyserial אינו מותקן; התקן pyserial כדי לקרוא GPS")
            _GPS_MISSING_DEPENDENCY_LOGGED = True
        return
    with _GPS_LOCK:
        if _GPS_READER_THREAD and _GPS_READER_THREAD.is_alive():
            return
        ports = _candidate_ports()
        if not ports:
            return
        port_name = ports[0]
        baudrate = int(getattr(config, "GPS_BAUDRATE", 9600))
        _GPS_STOP.clear()
        _GPS_READER_THREAD = threading.Thread(
            target=_gps_reader_loop, args=(port_name, baudrate),
            name="LifeGuard-GPS", daemon=True,
        )
        _GPS_READER_THREAD.start()
        if log_callback:
            log_callback(f"📡 מנסה להתחבר למקלט GNSS ב-{port_name} ({baudrate} baud)")


def get_gps_fix(max_age_seconds=None):
    import config
    max_age_seconds = max_age_seconds or getattr(config, "GPS_MAX_FIX_AGE_SECONDS", 10)
    with _GPS_LOCK:
        fix = dict(_GPS_LATEST) if _GPS_LATEST else None
    if not fix or time.time() - fix.get("timestamp", 0) > max_age_seconds:
        return None
    return fix


def _get_ip_location(log_callback=None):
    """Returns approximate IP geolocation or unavailable; never fabricates coordinates."""
    global _IP_CACHE, _IP_CACHE_AT
    import config
    cache_ttl = getattr(config, "IP_LOCATION_CACHE_SECONDS", 600)
    with _GPS_LOCK:
        if _IP_CACHE and time.time() - _IP_CACHE_AT < cache_ttl:
            return dict(_IP_CACHE)
    if not (HAS_REQUESTS and getattr(config, "ALLOW_IP_LOCATION_FALLBACK", True)):
        return _unavailable_location("IP fallback disabled")
    headers = {"User-Agent": config.HTTP_USER_AGENT}
    errors = []
    # באג קודם: נעשה שימוש ב-https://ip-api.com שהשכבה החינמית שלו *לא* תומכת ב-HTTPS
    # (מחזיר 403 תמיד) - כך שמיקום IP ומספר החירום המקומי מעולם לא עבדו. כעת: כמה ספקי
    # HTTPS בלתי תלויים לפי הסדר; הראשון שמחזיר קואורדינטות תקינות נבחר.
    for name, url, parser in _IP_PROVIDERS:
        try:
            response = requests.get(url, headers=headers, timeout=4)
            response.raise_for_status()
            parsed = parser(response.json())
            if not parsed:
                errors.append(f"{name}: תשובה ללא קואורדינטות")
                continue
            lat, lon, city, cc, country = parsed
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                errors.append(f"{name}: קואורדינטות לא חוקיות")
                continue
            location = {
                "lat": lat, "lon": lon, "city": city or "לא ידוע",
                "country_code": (cc or "").upper() or None, "country_name": country,
                "accuracy": "מיקום משוער לפי כתובת IP — אינו GPS",
                "accuracy_m_estimate": None, "source": f"{name} (approximate)",
                "quality": "coarse", "is_precise": False, "available": True,
                "timestamp": time.time(),
            }
            with _GPS_LOCK:
                _IP_CACHE, _IP_CACHE_AT = dict(location), time.time()
            return location
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
    if log_callback:
        log_callback(f"⚠️ מיקום IP לא זמין ({'; '.join(errors)})")
    return _unavailable_location("No precise or approximate location provider available")


def _parse_ipwhois(data):
    if data.get("success") and data.get("latitude") is not None:
        return (float(data["latitude"]), float(data["longitude"]), data.get("city"),
                data.get("country_code"), data.get("country"))
    return None


def _parse_ipapi_co(data):
    if data.get("latitude") is not None and not data.get("error"):
        return (float(data["latitude"]), float(data["longitude"]), data.get("city"),
                data.get("country_code"), data.get("country_name"))
    return None


def _parse_geojs(data):
    if data.get("latitude") not in (None, "", "nil"):
        return (float(data["latitude"]), float(data["longitude"]), data.get("city"),
                data.get("country_code"), data.get("country"))
    return None


_IP_PROVIDERS = (
    ("ipwho.is", "https://ipwho.is/", _parse_ipwhois),
    ("ipapi.co", "https://ipapi.co/json/", _parse_ipapi_co),
    ("geojs.io", "https://get.geojs.io/v1/ip/geo.json", _parse_geojs),
)


def _unavailable_location(reason=""):
    return {
        "lat": None, "lon": None, "city": "מיקום לא זמין",
        "country_code": None, "country_name": None,
        "accuracy": "לא זמין — אין קואורדינטות מאומתות", "accuracy_m_estimate": None,
        "source": "unavailable", "quality": "unavailable", "is_precise": False,
        "available": False, "reason": reason, "timestamp": time.time(),
    }


# ---------------- שירות המיקום של Windows (Wi-Fi / סלולר / GPS מובנה) ----------------
# בלי זה מחשב בלי GPS חיצוני קיבל רק מיקום IP (נמדד: עשרות ק"מ מהמיקום האמיתי) - ולכן "מקום בטוח לא עובד".
# System.Device.Location של .NET (מובנה ב-Windows, בלי התקנה) משתמש בשירות המיקום של Windows: נמדד ±200 מ'.
# דורש: הגדרות ← פרטיות ← מיקום: "שירותי מיקום" + "אפשר לאפליקציות שולחן עבודה". אחרת - חוזרים ל-IP.
WINLOC_TTL_S = 20
WINLOC_TIMEOUT_S = 15
WINLOC_MAX_ACC_M = 3000            # מעבר לזה זה כבר לא טוב יותר מ-IP
_WINLOC_CACHE = {"at": 0.0, "loc": None}
_WINLOC_LOCK = threading.Lock()
_WINLOC_PS = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High)
$null = $w.TryStart($false, [TimeSpan]::FromSeconds(6))
$end = (Get-Date).AddSeconds(8)
while (($w.Status -ne 'Ready') -and ((Get-Date) -lt $end)) { Start-Sleep -Milliseconds 200 }
$c = $w.Position.Location
if ($w.Permission -ne 'Granted') { 'DENIED' } elseif ($c.IsUnknown) { 'UNKNOWN' } else {
  '{0};{1};{2}' -f $c.Latitude.ToString([cultureinfo]::InvariantCulture), $c.Longitude.ToString([cultureinfo]::InvariantCulture), $c.HorizontalAccuracy.ToString([cultureinfo]::InvariantCulture) }
$w.Stop()
"""


WINLOC_REUSE_S = 120               # תוצאה עד 2 דק' משמשת בזמן שמרעננים ברקע
_WINLOC_REFRESHING = threading.Event()


def _fetch_windows_location(log_callback=None, runner=None):
    out = None
    try:
        if runner is None:
            import subprocess
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINLOC_PS],
                               capture_output=True, text=True, timeout=WINLOC_TIMEOUT_S,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = (r.stdout or "").strip().splitlines()
            raw = lines[-1].strip() if lines else ""
        else:
            raw = runner()
        if raw == "DENIED" and log_callback:
            log_callback("ℹ️ שירות המיקום של Windows כבוי או חסום לאפליקציות שולחן עבודה "
                         "(הגדרות ← פרטיות ← מיקום) - משתמשים במיקום IP משוער")
        parts = raw.split(";")
        if len(parts) == 3:
            lat, lon, acc = (float(x) for x in parts)
            if -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0) and 0 < acc <= WINLOC_MAX_ACC_M:
                out = {"lat": lat, "lon": lon, "city": "מיקום Windows", "country_code": None,
                       "country_name": None, "accuracy": f"שירות המיקום של Windows (Wi-Fi) · ±{acc:.0f} מ׳",
                       "accuracy_m_estimate": round(acc), "source": "windows_location", "quality": "wifi",
                       # GPS-מדויק רק בדיוק של GPS; Wi-Fi של מאות מטרים הוא "משוער טוב", לא GPS
                       "is_precise": acc <= 50, "available": True, "timestamp": time.time()}
    except Exception as exc:  # noqa: BLE001 - שירות חסר / PowerShell לא זמין -> חוזרים ל-IP
        if log_callback:
            log_callback(f"ℹ️ שירות המיקום של Windows לא זמין ({type(exc).__name__}) - משתמשים במיקום IP")
    with _WINLOC_LOCK:
        # כישלון רגעי לא מוחק תוצאה קודמת - היא נשארת עם חותמת הזמן המקורית שלה (והגיל שלה נבדק בשימוש)
        _WINLOC_CACHE.update(at=time.time(), loc=out if out is not None else _WINLOC_CACHE.get("loc"))
    return out


def _windows_location(log_callback=None, runner=None, wait=False):
    """
    -> dict מיקום (source='windows_location') או None. **לא חוסם**: הפעלת שירות המיקום לוקחת ~3-7 שנ', ו-SOS
    לעולם לא מחכה לה. מטמון טרי (20 שנ') -> מוחזר; ישן -> רענון ברקע, ובינתיים התוצאה האחרונה (עד 2 דק')
    או None (ואז IP / GPS). wait=True / runner (בדיקות, טעינה מוקדמת ברקע) - סינכרוני.
    """
    if os.name != "nt" or os.environ.get("LIFEGUARD_DISABLE_WINDOWS_LOCATION"):
        return None
    with _WINLOC_LOCK:
        fresh = time.time() - _WINLOC_CACHE["at"] < WINLOC_TTL_S
        cached = _WINLOC_CACHE["loc"]
    if fresh:
        return dict(cached) if cached else None
    if wait or runner is not None:
        out = _fetch_windows_location(log_callback, runner)
        return dict(out) if out else None
    if not _WINLOC_REFRESHING.is_set():
        _WINLOC_REFRESHING.set()

        def bg():
            try:
                _fetch_windows_location(log_callback)
            finally:
                _WINLOC_REFRESHING.clear()
        threading.Thread(target=bg, name="LG-WinLocation", daemon=True).start()
    if cached and time.time() - cached["timestamp"] <= WINLOC_REUSE_S:
        return dict(cached)
    return None


def prefetch_windows_location():
    """בהפעלת האפליקציה (ב-thread): מחמם את המטמון, כדי שב-SOS יהיה מיקום Wi-Fi מיד."""
    return _windows_location(wait=True)


_LAST_KNOWN = {"precise": None, "any": None}


def _remember(location):
    if not location or not location.get("available"):
        return
    with _GPS_LOCK:
        _LAST_KNOWN["any"] = dict(location)
        if location.get("is_precise"):
            _LAST_KNOWN["precise"] = dict(location)


def get_last_known(precise_only=False):
    """המיקום האחרון שהיה זמין (עם timestamp המקורי שלו!), או None. לא מוצג כמיקום נוכחי."""
    with _GPS_LOCK:
        loc = _LAST_KNOWN["precise" if precise_only else "any"]
        return dict(loc) if loc else None


def get_location(log_callback=None, allow_ip_fallback=True):
    """
    Returns newest GNSS fix; otherwise explicitly approximate IP location; else unavailable.
    כשאין מיקום עדכני - מצורף 'last_known' (מיקום אחרון + חותמת זמן מקורית) כדי שהממשק
    יוכל להציג "מיקום אחרון ידוע לפני X דקות" - ולעולם לא כמיקום נוכחי.
    """
    _ensure_gps_reader(log_callback=log_callback)
    fix = get_gps_fix()
    if fix:
        _remember(fix)
        return fix
    win = _windows_location(log_callback=log_callback)       # Wi-Fi של Windows - הרבה יותר טוב מ-IP
    if win:
        _remember(win)
        return win
    if allow_ip_fallback:
        loc = _get_ip_location(log_callback=log_callback)
    else:
        loc = _unavailable_location("GPS fix unavailable")
    if loc.get("available"):
        _remember(loc)
    else:
        last = get_last_known()
        if last:
            loc["last_known"] = last
    return loc


# ספים לסימון מיקום "לא עדכני". GPS זז מהר - 30 שניות כבר ישן לניווט; IP הוא תמיד גס.
STALE_GPS_SECONDS = 30
STALE_ANY_SECONDS = 15 * 60


def describe_location(location, now=None):
    """
    מחזיר תיאור אמינות אחיד לממשק/התראות:
    {"confidence": "verified"|"estimated"|"stale"|"unavailable", "age_seconds": float|None,
     "label": טקסט קצר בעברית, "is_current": bool}
    'verified' = GNSS עדכני; 'estimated' = IP; 'stale' = ישן מהסף; 'unavailable' = אין.
    """
    now = now or time.time()
    if not location or not location.get("available"):
        last = (location or {}).get("last_known")
        if last:
            age = max(0.0, now - (last.get("timestamp") or now))
            return {"confidence": "stale", "age_seconds": age, "is_current": False,
                    "label": f"אין מיקום עדכני. מיקום אחרון ידוע: לפני {format_age(age)} "
                             f"({'GPS' if last.get('is_precise') else 'IP משוער'}) - ייתכן שאינו נכון כעת"}
        return {"confidence": "unavailable", "age_seconds": None, "is_current": False,
                "label": "מיקום לא זמין - אין קואורדינטות מאומתות"}
    age = max(0.0, now - (location.get("timestamp") or now))
    if location.get("is_precise"):
        if age > STALE_GPS_SECONDS:
            return {"confidence": "stale", "age_seconds": age, "is_current": False,
                    "label": f"מיקום GPS ישן (לפני {format_age(age)}) - לא בהכרח המיקום הנוכחי"}
        est = location.get("accuracy_m_estimate")
        acc = f"±{est:.0f} מ׳ (אומדן)" if est else "דיוק לא ידוע"
        return {"confidence": "verified", "age_seconds": age, "is_current": True,
                "label": f"GPS עדכני · {acc} · לפני {format_age(age)}"}
    if location.get("source") == "windows_location":
        acc = location.get("accuracy_m_estimate")
        if age > STALE_ANY_SECONDS:
            return {"confidence": "stale", "age_seconds": age, "is_current": False,
                    "label": f"מיקום Windows ישן (לפני {format_age(age)})"}
        return {"confidence": "estimated", "age_seconds": age, "is_current": True,
                "label": f"מיקום לפי Wi-Fi (שירות המיקום של Windows) · ±{acc or '?'} מ׳ - לא GPS"}
    if age > STALE_ANY_SECONDS:
        return {"confidence": "stale", "age_seconds": age, "is_current": False,
                "label": f"מיקום IP משוער וישן (לפני {format_age(age)})"}
    return {"confidence": "estimated", "age_seconds": age, "is_current": False,
            "label": f"משוער לפי IP בלבד (אזור {location.get('city', 'לא ידוע')}) - לא מיקום מדויק"}


def format_age(seconds):
    seconds = int(max(0, seconds or 0))
    if seconds < 60:
        return f"{seconds} שנ׳"
    if seconds < 3600:
        return f"{seconds // 60} דק׳"
    return f"{seconds // 3600} שע׳ {(seconds % 3600) // 60} דק׳"


def location_sources_status():
    """לבדיקת תקינות: אילו מקורות מיקום קיימים בפועל במחשב הזה."""
    import config
    ports = _candidate_ports() if HAS_PYSERIAL else []
    with _GPS_LOCK:
        port_in_use = _GPS_PORT_IN_USE
    return {
        "pyserial": HAS_PYSERIAL,
        "gps_port_configured": bool(getattr(config, "GPS_SERIAL_PORT", "")),
        "gps_candidate_ports": ports,
        "gps_port_in_use": port_in_use,
        "gps_fix": get_gps_fix(),
        "ip_fallback_enabled": bool(getattr(config, "ALLOW_IP_LOCATION_FALLBACK", True)),
    }


def build_live_map_link(lat, lon):
    """Builds a map URL only for valid numeric coordinates; caller must label coarse data."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"


def format_location_for_alert(location):
    """Human-readable and explicit precision marker for SOS messages."""
    if not location or not location.get("available"):
        return "📍 מיקום לא זמין — יש ליצור קשר טלפוני ולברר מיקום"
    if location.get("is_precise"):
        estimate = location.get("accuracy_m_estimate")
        accuracy = f" (אומדן דיוק ±{estimate} מ׳ לפי HDOP)" if estimate else " (דיוק לא ידוע)"
        return f"📍 מיקום GPS מאומת{accuracy}"
    return f"⚠️ מיקום IP משוער בלבד באזור {location.get('city', 'לא ידוע')} — אין להסתמך עליו לניווט"


def stop_gps_reader():
    """Stops the optional serial reader (used by tests and orderly shutdown)."""
    _GPS_STOP.set()
