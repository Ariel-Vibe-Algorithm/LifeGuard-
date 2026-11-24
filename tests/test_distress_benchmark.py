# -*- coding: utf-8 -*-
"""קורא zip מפוצל + לוגיקת הבנצ'מרק (תיוג, חלוקה לפי מעלה, אימון והערכה צולבת) על נתונים סינתטיים."""
import os
import zipfile

import numpy as np
import pytest

from research_tools import distress_benchmark as B
from research_tools.split_zip import SplitZip, parts_for


def _split(src, base, part_size):
    """מדמה zip -s: חותך את הבתים לחלקים ומתקן מספרי דיסק/היסטים ב-central directory וב-EOCD."""
    import struct
    data = open(src, "rb").read()
    eocd = data.rfind(b"PK\x05\x06")
    _, _, _, _, total, cd_size, cd_off, _ = struct.unpack("<4sHHHHIIH", data[eocd:eocd + 22])
    loc = lambda off: (off // part_size, off % part_size)          # noqa: E731
    cd, p, new_cd = data[cd_off:cd_off + cd_size], 0, b""
    for _ in range(total):
        nlen, xlen, clen2 = struct.unpack("<HHH", cd[p + 28:p + 34])
        rec = bytearray(cd[p:p + 46 + nlen + xlen + clen2])
        d, o = loc(struct.unpack("<I", rec[42:46])[0])
        struct.pack_into("<H", rec, 34, d)
        struct.pack_into("<I", rec, 42, o)
        new_cd += bytes(rec)
        p += len(rec)
    body = data[:cd_off]
    chunks = [body[i:i + part_size] for i in range(0, len(body), part_size)]
    last_disk = len(chunks) - 1
    tail_cd_off = len(chunks[-1])
    eocd_rec = struct.pack("<4sHHHHIIH", b"PK\x05\x06", last_disk, last_disk, total, total,
                           len(new_cd), tail_cd_off, 0)
    names = [f"{base}.z{i + 1:02d}" for i in range(last_disk)] + [base + ".zip"]
    for i, n in enumerate(names):
        with open(n, "wb") as fh:
            fh.write(chunks[i] + (new_cd + eocd_rec if i == last_disk else b""))
    return names


def test_split_zip_reads_across_parts(tmp_path):
    rng = np.random.default_rng(0)
    files = {f"FSD/{i}.wav": rng.bytes(int(rng.integers(500, 3000))) for i in range(12)}
    files["FSD/text.txt"] = b"hello " * 400                      # נדחס היטב (deflate)
    src = str(tmp_path / "src.zip")
    with zipfile.ZipFile(src, "w") as z:
        for n, b in files.items():
            z.writestr(n, b, compress_type=zipfile.ZIP_DEFLATED if n.endswith(".txt") else zipfile.ZIP_STORED)
    names = _split(src, str(tmp_path / "A"), part_size=4096)
    assert len(names) >= 3 and parts_for(names[-1]) == names
    with SplitZip(names[-1]) as sz:
        assert sorted(sz.names()) == sorted(files)
        for n, b in files.items():
            assert sz.read(n) == b                                   # כולל רשומות שחוצות גבול חלק
    with SplitZip(src) as single:                                    # zip רגיל = חלק אחד
        assert single.read("FSD/text.txt") == files["FSD/text.txt"]


def test_labels_and_uploader_folds():
    assert B.label_clip(["Screaming", "Human_voice"]) == (True, "Screaming")
    assert B.label_clip(["Laughter", "Shout"]) == (True, "Shout")    # חיובי גובר על שלילי קשה
    assert B.label_clip(["Laughter", "Speech"]) == (False, "Laughter")
    assert B.label_clip(["Rain"]) == (False, "other")
    assert B.fold_of("alice") == B.fold_of("alice") and {B.fold_of(f"u{i}") for i in range(50)} == set(range(B.FOLDS))


def test_crossval_is_out_of_fold_and_separates_synthetic_data():
    rng = np.random.default_rng(0)
    rows, feats = [], {}
    for i in range(120):
        pos = i % 4 == 0
        emb = rng.normal(0, 1, (6, 1024)).astype(np.float16)
        if pos:
            emb[:, :8] += 2.5
        f = str(i)
        rows.append({"fname": f, "positive": pos, "label": "Screaming" if pos else "other", "uploader": f"u{i % 30}"})
        feats[f] = {"emb": emb, "event": np.ones(6, bool), "seconds": 3.0}
    oof = B.crossval_scores(rows, feats)
    assert set(oof) == {r["fname"] for r in rows}
    pos_peak = np.mean([oof[r["fname"]].max() for r in rows if r["positive"]])
    neg_peak = np.mean([oof[r["fname"]].max() for r in rows if not r["positive"]])
    assert pos_peak > 0.8 and neg_peak < 0.5


@pytest.mark.skipif(not os.path.exists(os.path.join("research", "samples", "fsd50k", "raw", "COMPLETE")),
                    reason="FSD50K לא הורד במלואו (הקובץ COMPLETE נוצר אחרי בדיקת גדלים)")
def test_real_fsd50k_archive_opens():
    with SplitZip(os.path.join("research", "samples", "fsd50k", "raw", "FSD50K.eval_audio.zip")) as z:
        wavs = [n for n in z.names() if n.endswith(".wav")]
        assert len(wavs) == 10231
        assert z.read(wavs[0])[:4] == b"RIFF"
