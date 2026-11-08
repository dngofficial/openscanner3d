"""Certificate setup diagnostics; tests do not change system trust."""
from contextlib import redirect_stderr
import io
import os
import subprocess
import unittest
from unittest.mock import patch

from scripts import make_cert


class CertificateSetupTests(unittest.TestCase):
    def test_invalid_config_reports_an_actionable_cli_error_before_mkcert(self):
        output = io.StringIO()
        with patch("sys.argv", ["make_cert"]), patch.object(make_cert, "load_settings", side_effect=ValueError("Invalid TOML in config.local.toml: duplicate setting")):
            with patch.object(make_cert, "find_mkcert") as find, redirect_stderr(output):
                with self.assertRaises(SystemExit) as raised:
                    make_cert.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("config.local.toml", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
        find.assert_not_called()

    def test_mkcert_only_targets_system_trust_and_preserves_parent_environment(self):
        with patch.dict(os.environ, {"TRUST_STORES": "java", "JAVA_HOME": r"C:\Program Files\Java\jdk-17"}):
            with patch.object(make_cert.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ready\n", "")) as invoke:
                self.assertEqual(make_cert.run(["mkcert.exe", "-install"]), "ready")
            self.assertEqual(invoke.call_args.kwargs["env"]["TRUST_STORES"], "system")
            self.assertEqual(os.environ["TRUST_STORES"], "java")

    def test_system_trust_errors_are_not_ignored(self):
        with patch.object(make_cert.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "system store access denied")):
            with self.assertRaisesRegex(SystemExit, "system store access denied"):
                make_cert.run(["mkcert.exe", "-install"])
