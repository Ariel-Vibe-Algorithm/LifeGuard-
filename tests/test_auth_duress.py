import tempfile
import unittest
from pathlib import Path

import auth_module
import config


class AuthDuressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = config.USERS_DB_FILE
        config.USERS_DB_FILE = str(Path(self.tmp.name) / "users.json")

    def tearDown(self):
        config.USERS_DB_FILE = self.original_db
        self.tmp.cleanup()

    def test_duress_phrase_is_distinct_from_password_and_regular_cancel(self):
        ok, message = auth_module.register_user(
            "lgtester", "StrongPass!2026", [{"name": "Emergency Contact", "account": "family@example.test", "role": "family"}],
            voice_secret_word="red siren",
            silent_alarm_phrase="blue lantern",
            secret_cancel_phrase="green garden",
            duress_phrase="silver window",
            terms_accepted=True,
        )
        self.assertTrue(ok, message)
        self.assertTrue(auth_module.verify_password("lgtester", "StrongPass!2026"))
        contacts = auth_module.get_contacts("lgtester")
        self.assertEqual(contacts[0]["account"], "family@example.test")
        self.assertNotIn("phone", contacts[0])
        self.assertTrue(auth_module.verify_duress_phrase_typed("lgtester", "silver window"))
        self.assertFalse(auth_module.verify_duress_phrase_typed("lgtester", "green garden"))
        self.assertFalse(auth_module.update_duress_phrase("lgtester", "green garden")[0])


if __name__ == "__main__":
    unittest.main()
