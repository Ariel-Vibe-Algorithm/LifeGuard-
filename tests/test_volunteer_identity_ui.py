# -*- coding: utf-8 -*-
"""ממשק Windows לאימות זהות מתנדב/ת: הכנת תמונה (הקטנה + הסרת EXIF/מיקום), בדיקות לפני שליחה, הצגת סטטוס."""
import io
from types import SimpleNamespace

from PIL import Image

import features_ui as FU
from test_ui_navigation import shared_root  # noqa: F401


def _photo_with_gps(tmp_path):
    im = Image.new("RGB", (4000, 3000), (120, 30, 200))
    exif = Image.Exif()
    exif[0x8825] = {2: (32.0, 5.0, 0.0), 4: (34.0, 47.0, 0.0)}    # GPSInfo
    exif[0x010F] = "PhoneMaker"
    p = tmp_path / "id.jpg"
    im.save(p, "JPEG", exif=exif)
    return str(p)


def test_prepare_photo_resizes_and_strips_exif(tmp_path):
    raw = FU.prepare_photo(_photo_with_gps(tmp_path))
    assert raw[:3] == b"\xff\xd8\xff"
    with Image.open(io.BytesIO(raw)) as im:
        assert max(im.size) == 1600
        assert len(im.getexif()) == 0                                # אין GPS / יצרן
    assert b"PhoneMaker" not in raw


def test_upload_window_validates_before_sending(shared_root, tmp_path):  # noqa: F811
    sent = []
    client = SimpleNamespace(submit_identity=lambda *a: sent.append(a) or {"status": "pending"})
    app = SimpleNamespace(client=client, root=shared_root)
    w = FU.IdentityUploadWindow(shared_root, app)
    try:
        w.num.set("123456789")
        assert "לא תקין" in w.validate()
        w.num.set("123456782")
        assert "סלפי" in w.validate()
        w.paths = {"doc": _photo_with_gps(tmp_path), "selfie": _photo_with_gps(tmp_path)}
        assert "לאשר" in w.validate()
        w.consent.set(True)
        assert w.validate() is None
    finally:
        w.destroy()
    assert sent == []


def test_trust_section_shows_identity_required(shared_root):  # noqa: F811
    import tkinter as tk
    app = SimpleNamespace(client=None, root=shared_root)
    frame = tk.Frame(shared_root)
    try:
        sec = FU.VolunteerTrustSection(frame, app)
        sec.apply({"verification": "verified", "identity": {"status": "none", "status_he": "לא הועלה"}})
        assert "לא יישלחו" in sec.id_note.cget("text") and str(sec.id_btn.cget("state")) == "normal"
        sec.apply({"verification": "unverified", "identity": {"status": "rejected", "status_he": "נדחה",
                                                              "note": "התמונה מטושטשת"}})
        assert "מטושטשת" in sec.id_note.cget("text")
        sec.apply({"verification": "unverified", "identity": {"status": "verified", "status_he": "מאומת/ת"}})
        assert sec.id_note.cget("text") == "" and str(sec.id_btn.cget("state")) == "disabled"
    finally:
        frame.destroy()
