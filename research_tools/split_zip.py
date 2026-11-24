# -*- coding: utf-8 -*-
"""
split_zip.py - קריאת ארכיון zip מפוצל (file.z01, file.z02 ... file.zip) בלי לחלץ לדיסק.

למה: FSD50K מופץ כ-zip מפוצל (zip -s) ו-zipfile של פייתון לא תומך בכך. במקום להתקין 7-Zip
ולחלץ 6GB, קוראים את ה-central directory בעצמנו ומתרגמים (מספר דיסק, היסט) למיקום בקבצים.
תומך ב-zip64, stored ו-deflate. קורא בלבד; שמות קבצים לא משמשים כנתיבים בדיסק (אין path traversal).
"""

import os
import struct
import zlib

EOCD = b"PK\x05\x06"
EOCD64_LOC = b"PK\x06\x07"
EOCD64 = b"PK\x06\x06"
CDH = b"PK\x01\x02"
LFH = b"PK\x03\x04"


def parts_for(zip_path):
    """file.zip -> [file.z01, file.z02, ..., file.zip] (רק מה שקיים ברצף)."""
    base = zip_path[:-4]
    parts, i = [], 1
    while os.path.exists(f"{base}.z{i:02d}"):
        parts.append(f"{base}.z{i:02d}")
        i += 1
    return parts + [zip_path]


class SplitZip:
    def __init__(self, zip_path):
        self.parts = parts_for(zip_path)
        self.sizes = [os.path.getsize(p) for p in self.parts]
        self.starts = [sum(self.sizes[:i]) for i in range(len(self.sizes))]
        self._fh = {}
        self.entries = {}
        self._read_central_directory()

    # ------------------------------------------------------------ גישה לבתים
    def _read(self, disk, offset, n):
        out = b""
        while n > 0:
            fh = self._fh.get(disk)
            if fh is None:
                fh = self._fh[disk] = open(self.parts[disk], "rb")
            fh.seek(offset)
            chunk = fh.read(n)
            out += chunk
            n -= len(chunk)
            if n > 0:                     # הרשומה ממשיכה בחלק הבא
                if not chunk and offset >= self.sizes[disk]:
                    disk, offset = disk + 1, 0
                    if disk >= len(self.parts):
                        raise EOFError("ארכיון קטוע")
                else:
                    offset += len(chunk)
        return out

    def close(self):
        for fh in self._fh.values():
            fh.close()
        self._fh.clear()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------ מבנה
    def _read_central_directory(self):
        last = len(self.parts) - 1
        size = self.sizes[last]
        tail_len = min(size, 65536 + 22)
        tail = self._read(last, size - tail_len, tail_len)
        pos = tail.rfind(EOCD)
        if pos < 0:
            raise ValueError("לא נמצא סוף ארכיון (EOCD)")
        (_, _, cd_disk, _, total, cd_size, cd_off, _) = struct.unpack("<4sHHHHIIH", tail[pos:pos + 22])
        if cd_off == 0xFFFFFFFF or total == 0xFFFF or cd_disk == 0xFFFF:
            loc = tail.rfind(EOCD64_LOC, 0, pos)
            _, eocd64_disk, eocd64_off, _ = struct.unpack("<4sIQI", tail[loc:loc + 20])
            rec = self._read(eocd64_disk, eocd64_off, 56)
            (sig, _, _, _, _, cd_disk, _, total, cd_size, cd_off) = struct.unpack("<4sQHHIIQQQQ", rec)
            assert sig == EOCD64
        cd = self._read(cd_disk, cd_off, cd_size)
        p = 0
        for _ in range(total):
            if cd[p:p + 4] != CDH:
                raise ValueError("central directory פגום")
            (method, crc, csize, usize, nlen, xlen, clen, disk, _, _, loff) = struct.unpack(
                "<10xH4xIIIHHHHHII", cd[p:p + 46])
            name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
            extra = cd[p + 46 + nlen:p + 46 + nlen + xlen]
            usize, csize, loff, disk = self._zip64(extra, usize, csize, loff, disk)
            self.entries[name] = (method, crc, csize, usize, disk, loff)
            p += 46 + nlen + xlen + clen

    @staticmethod
    def _zip64(extra, usize, csize, loff, disk):
        i = 0
        while i + 4 <= len(extra):
            hid, hlen = struct.unpack("<HH", extra[i:i + 4])
            if hid == 1:
                data, j = extra[i + 4:i + 4 + hlen], 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", data[j:j + 8])[0]; j += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", data[j:j + 8])[0]; j += 8
                if loff == 0xFFFFFFFF:
                    loff = struct.unpack("<Q", data[j:j + 8])[0]; j += 8
                if disk == 0xFFFF:
                    disk = struct.unpack("<I", data[j:j + 4])[0]
            i += 4 + hlen
        return usize, csize, loff, disk

    def names(self):
        return list(self.entries)

    def read(self, name):
        method, crc, csize, usize, disk, loff = self.entries[name]
        hdr = self._read(disk, loff, 30)
        if hdr[:4] != LFH:
            raise ValueError(f"local header פגום: {name}")
        nlen, xlen = struct.unpack("<HH", hdr[26:30])
        # הנתונים מתחילים אחרי הכותרת המקומית; עשויים לחצות גבול בין חלקים
        start = loff + 30 + nlen + xlen
        d, off = disk, start
        while off >= self.sizes[d]:
            off -= self.sizes[d]
            d += 1
        raw = self._read(d, off, csize)
        if method == 0:
            data = raw
        elif method == 8:
            data = zlib.decompress(raw, -15)
        else:
            raise ValueError(f"שיטת דחיסה לא נתמכת: {method}")
        if zlib.crc32(data) & 0xFFFFFFFF != crc:
            raise ValueError(f"CRC שגוי: {name}")
        return data
