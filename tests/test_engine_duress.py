import unittest
from unittest import mock

import auth_module
from engine import LifeGuardEngine, State


class EngineDuressTests(unittest.TestCase):
    def test_duress_phrase_is_accepted_as_covert_cancel_path(self):
        engine = object.__new__(LifeGuardEngine)
        engine.username = "lgtester"
        engine.state = State.ACTIVE_SOS
        engine.activate_duress_mode = mock.Mock(return_value=True)
        with mock.patch.object(auth_module, "verify_duress_phrase_typed", return_value=True):
            result = engine.cancel_with_password("duress phrase")
        self.assertTrue(result)
        engine.activate_duress_mode.assert_called_once_with(source="typed")


if __name__ == "__main__":
    unittest.main()
