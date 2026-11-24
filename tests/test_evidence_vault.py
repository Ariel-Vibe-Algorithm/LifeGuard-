# -*- coding: utf-8 -*-
"""כספת ראיות: מוצפן ולא מטושטש בזמן אירוע; אמת = ראיה (ייצוא עם hash); שווא = טשטוש, אימון רק בהסכמה, מקור נמחק."""
import hashlib
import os
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
import evidence_vault as EV  # noqa: E402


def _photo(path, seed=0):
    img = (np.random.default_rng(seed).random((120, 160, 3)) * 255).astype(np.uint8)
    cv2.imwrite(path, img)
    with open(path, "rb") as fh:
        return fh.read()


def _fake_blur(img):
    return cv2.GaussianBlur(img, (31, 31), 0), 1


def test_seal_encrypts_removes_plaintext_and_keeps_integrity(tmp_path):
    v = EV.EvidenceVault(str(tmp_path / "vault"))
    p = str(tmp_path / "face_1.jpg")
    raw = _photo(p)
    sealed = v.seal(p, "evt-1", "face")
    assert not os.path.exists(p)                                   # הגלוי נמחק
    with open(sealed, "rb") as fh:
        assert raw[:200] not in fh.read()                          # מוצפן
    m = v.manifest("evt-1")
    it = m["items"][0]
    assert it["sha256"] == hashlib.sha256(raw).hexdigest() and it["blurred"] is False
    assert v.open_item("evt-1", it) == raw                         # פענוח מחזיר את המקור בדיוק (לא מטושטש)
    with open(sealed, "r+b") as fh:                                # שינוי בקובץ -> נכשל (לא ראיה מזויפת)
        fh.seek(20)
        fh.write(b"\x00\x01")
    with pytest.raises(Exception):
        v.open_item("evt-1", it)


def test_true_event_kept_as_evidence_and_exportable(tmp_path):
    v = EV.EvidenceVault(str(tmp_path / "vault"))
    raws = []
    for i in range(2):
        p = str(tmp_path / f"face_{i}.jpg")
        raws.append(_photo(p, i))
        v.seal(p, "evt-2", "face")
    m = v.on_rating("evt-2", "true_event", training_dir=str(tmp_path / "train"), blur=_fake_blur)
    assert m["status"] == "evidence" and m["delete_after"] > time.time() + 300 * 86400
    assert not os.path.exists(str(tmp_path / "train"))           # ראיה לא נכנסת לאימון
    n = v.export("evt-2", str(tmp_path / "police"))
    assert n == 2
    sums = open(str(tmp_path / "police" / "SHA256SUMS.txt"), encoding="utf-8").read()
    assert hashlib.sha256(raws[0]).hexdigest() in sums
    with open(str(tmp_path / "police" / "face_0.jpg"), "rb") as fh:
        assert fh.read() == raws[0]


def test_false_alarm_blurs_trains_only_with_consent_and_deletes_originals(tmp_path):
    for consent in (True, False):
        root = tmp_path / f"v{consent}"
        v = EV.EvidenceVault(str(root))
        p = str(tmp_path / "face_9.jpg")
        _photo(p, 9)
        v.seal(p, "evt-3", "face")
        train = str(tmp_path / f"train{consent}")
        m = v.on_rating("evt-3", "false_alarm", training_dir=train if consent else None, blur=_fake_blur)
        assert not [f for f in os.listdir(str(root / "evt-3")) if f.endswith(".lgev") or f == "key.bin"]
        assert m["items"][0]["blurred"] is True and m["items"][0]["sealed"] is None
        if consent:
            assert m["status"] == "false_alarm_blurred" and len(os.listdir(train)) == 1
        else:
            assert m["status"] == "false_alarm_deleted" and not os.path.exists(train)


def test_retention_purge(tmp_path):
    v = EV.EvidenceVault(str(tmp_path / "vault"))
    p = str(tmp_path / "face_1.jpg")
    _photo(p)
    v.seal(p, "old", "face")
    v.on_rating("old", "felt_danger")
    assert v.purge_expired(now=time.time() + 10) == 0
    assert v.purge_expired(now=time.time() + EV.RETENTION_DAYS * 86400 + 10) == 1
    assert not os.path.exists(str(tmp_path / "vault" / "old"))
