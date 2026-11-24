import unittest
from unittest import mock

import location_module


def with_checksum(payload):
    checksum = 0
    for char in payload:
        checksum ^= ord(char)
    return f"${payload}*{checksum:02X}"


class LocationTests(unittest.TestCase):
    def test_parse_valid_gga_fix(self):
        sentence = with_checksum("GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
        fix = location_module.parse_nmea_sentence(sentence)
        self.assertIsNotNone(fix)
        self.assertTrue(fix["is_precise"])
        self.assertAlmostEqual(fix["lat"], 48.1173, places=4)
        self.assertAlmostEqual(fix["lon"], 11.516666, places=4)
        self.assertEqual(fix["satellites"], 8)

    def test_reject_bad_checksum_and_no_fix(self):
        self.assertIsNone(location_module.parse_nmea_sentence("$GPGGA,123519,,,,,0,00,99.9,,,,,,*00"))
        sentence = with_checksum("GPGGA,123519,4807.038,N,01131.000,E,0,00,99.9,545.4,M,46.9,M,,")
        self.assertIsNone(location_module.parse_nmea_sentence(sentence))

    def test_no_provider_returns_unavailable_not_fake_coordinates(self):
        with mock.patch.object(location_module, "HAS_PYSERIAL", False), \
             mock.patch.object(location_module, "HAS_REQUESTS", False):
            loc = location_module.get_location(allow_ip_fallback=False)
        self.assertFalse(loc["available"])
        self.assertIsNone(loc["lat"])
        self.assertIsNone(loc["lon"])
        self.assertEqual(loc["source"], "unavailable")

    def test_map_link_rejects_missing_coordinates(self):
        self.assertIsNone(location_module.build_live_map_link(None, None))
        self.assertIsNotNone(location_module.build_live_map_link(32.0, 34.8))


if __name__ == "__main__":
    unittest.main()
