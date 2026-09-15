import os
import subprocess
import unittest
from unittest.mock import patch

from clipboard_app.input_method import prepare_input_method


class InputMethodStartupTests(unittest.TestCase):
    def test_available_portal_used_before_qt_starts(self):
        with patch.dict(os.environ, {"QT_IM_MODULE": "ibus"}, clear=True):
            with patch("clipboard_app.input_method.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "(true,)\n")):
                prepare_input_method()
            self.assertEqual(os.environ["IBUS_USE_PORTAL"], "1")

    def test_no_portal_keeps_normal_ibus_discovery(self):
        with patch.dict(os.environ, {"QT_IM_MODULE": "ibus"}, clear=True):
            with patch("clipboard_app.input_method.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "(false,)\n")):
                prepare_input_method()
            self.assertNotIn("IBUS_USE_PORTAL", os.environ)

    def test_other_input_methods_and_explicit_configuration_untouched(self):
        for environment in ({"QT_IM_MODULE": "fcitx"}, {"QT_IM_MODULE": "ibus", "IBUS_USE_PORTAL": "1"}):
            with patch.dict(os.environ, environment, clear=True):
                with patch("clipboard_app.input_method.subprocess.run") as probe:
                    prepare_input_method()
                    probe.assert_not_called()
                self.assertEqual(dict(os.environ), environment)

    def test_probe_timeout_does_not_prevent_startup(self):
        with patch.dict(os.environ, {"QT_IM_MODULE": "ibus"}, clear=True):
            with patch("clipboard_app.input_method.subprocess.run", side_effect=subprocess.TimeoutExpired("gdbus", 2)):
                prepare_input_method()
            self.assertNotIn("IBUS_USE_PORTAL", os.environ)


if __name__ == "__main__":
    unittest.main()
