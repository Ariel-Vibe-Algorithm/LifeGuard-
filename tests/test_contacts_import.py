# -*- coding: utf-8 -*-
"""ייבוא אנשי קשר מקבצי ייצוא אמיתיים (פורמטים של Google / iPhone / Outlook)."""
import contacts_import as ci

VCF = """BEGIN:VCARD
VERSION:3.0
FN:אמא
N:;אמא;;;
TEL;TYPE=CELL:050-123-4567
TEL;TYPE=HOME:+972 3 555 1234
END:VCARD
BEGIN:VCARD
VERSION:3.0
N:Cohen;Dan;;;
item1.TEL;type=pref:+1 (212) 555-0100
END:VCARD
BEGIN:VCARD
VERSION:2.1
FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=D7=A1=D7=91=D7=AA=D7=90
TEL;CELL:052
 7778899
END:VCARD
BEGIN:VCARD
FN:No Phone
END:VCARD
"""

GOOGLE_CSV = ("Name,Given Name,Family Name,Phone 1 - Type,Phone 1 - Value,Phone 2 - Value\n"
              "Yossi Levi,Yossi,Levi,Mobile,054-111-2222 ::: 054-333-4444,\n"
              ",Rina,Katz,Mobile,03-6000000,\n"
              "No Number,,,,,\n")
OUTLOOK_CSV = "First Name,Last Name,Mobile Phone,E-mail Address\nAvi,Bar,+972501112233,a@b.c\n"


def test_vcf():
    got = ci.parse_vcf(VCF)
    assert {"name": "אמא", "phone": "0501234567"} in got
    assert {"name": "אמא", "phone": "+97235551234"} in got
    assert {"name": "Dan Cohen", "phone": "+12125550100"} in got
    assert {"name": "סבתא", "phone": "0527778899"} in got  # quoted-printable + שורה מקופלת
    assert not any(c["name"] == "No Phone" for c in got)


def test_google_csv_multiple_numbers_and_split_names():
    got = ci.parse_csv(GOOGLE_CSV)
    assert {"name": "Yossi Levi", "phone": "0541112222"} in got
    assert {"name": "Yossi Levi", "phone": "0543334444"} in got
    assert {"name": "Rina Katz", "phone": "036000000"} in got
    assert len(got) == 3


def test_outlook_csv():
    assert ci.parse_csv(OUTLOOK_CSV) == [{"name": "Avi Bar", "phone": "+972501112233"}]


def test_load_file_encodings(tmp_path):
    p = tmp_path / "c.vcf"
    p.write_bytes(VCF.encode("utf-16"))
    assert len(ci.load_file(str(p))) == 4
    p2 = tmp_path / "c.csv"
    p2.write_bytes("Name,Mobile\nדנה,0501234567\n".encode("cp1255"))
    assert ci.load_file(str(p2)) == [{"name": "דנה", "phone": "0501234567"}]


def test_outlook_items_mapping():
    from types import SimpleNamespace as NS
    items = [NS(Class=40, FullName="Noa Levi", MobileTelephoneNumber="050-123-4567", HomeTelephoneNumber="050-123-4567"),
             NS(Class=69, FullName="Family list"),                      # רשימת תפוצה - לא איש קשר
             NS(Class=40, FullName="", CompanyName="Clinic", BusinessTelephoneNumber="+972 3 555 0000"),
             NS(Class=40, FullName="No phone")]
    assert ci.outlook_items_to_contacts(items) == [{"name": "Clinic", "phone": "+97235550000"},
                                                   {"name": "Noa Levi", "phone": "0501234567"}]
