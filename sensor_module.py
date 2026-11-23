# -*- coding: utf-8 -*-
"""Common sensor evidence interface for desktop and future mobile adapters.

Desktop build has no built-in accelerometer/gyroscope in most PCs. Android/iOS
adapters should call report_sensor_signal() from native sensor callbacks. Events
are short-lived and are not treated as proof of danger by themselves.
"""

import threading
import time

_ALLOWED = {
    "impact", "fall", "immobility", "rapid_motion", "abrupt_stop",
    "device_separation", "manual_wearable_sos",
}
_LOCK = threading.RLock()
_EVENTS = []
_DEFAULT_TTL = 20.0


def report_sensor_signal(signal, confidence=0.5, timestamp=None, metadata=None):
    signal = str(signal or "").strip().lower()
    if signal not in _ALLOWED:
        raise ValueError(f"Unsupported sensor signal: {signal}")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    event = {
        "signal": signal,
        "confidence": confidence,
        "timestamp": float(timestamp or time.time()),
        "metadata": dict(metadata or {}),
    }
    with _LOCK:
        _EVENTS.append(event)
        cutoff = time.time() - 120
        _EVENTS[:] = [item for item in _EVENTS if item["timestamp"] >= cutoff]
    return event


def get_recent_sensor_signals(window_seconds=None):
    window_seconds = float(window_seconds or _DEFAULT_TTL)
    cutoff = time.time() - window_seconds
    with _LOCK:
        return [dict(item) for item in _EVENTS if item["timestamp"] >= cutoff]


def clear_sensor_signals():
    with _LOCK:
        _EVENTS.clear()


def supported_signals():
    return tuple(sorted(_ALLOWED))
