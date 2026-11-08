"""Certificate helper regression checks; never mutate OS or Java trust stores."""
import os
import subprocess
import unittest
from unittest.mock import patch

import make_cert


class CertificateCommandTests(unittest.TestCase):
    def test_mkcert_uses_only_system_trust_without_changing_parent_environment(self):
        with patch.dict(os.environ, {"JAVA_HOME": r"C:\Program Files\Java\jdk-17", "TRUST_STORES": "java"}):
            with patch.object(make_cert.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ready\n", "")) as invoke:
                self.assertEqual(make_cert.run(["mkcert.exe", "-install"]), "ready")
            values = invoke.call_args.kwargs
            self.assertEqual(invoke.call_args.args[0], ["mkcert.exe", "-install"])
            self.assertEqual(values["env"]["TRUST_STORES"], "system")
            self.assertEqual(values["env"]["JAVA_HOME"], os.environ["JAVA_HOME"])
            self.assertEqual(os.environ["TRUST_STORES"], "java")
            self.assertNotIn("shell", values)

    def test_system_trust_failure_still_stops_with_diagnostics(self):
        failure = subprocess.CompletedProcess([], 1, "", "Windows system trust store access denied")
        with patch.object(make_cert.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(SystemExit, "Windows system trust store access denied"):
                make_cert.run(["mkcert.exe", "-install"])


if __name__ == "__main__": unittest.main()
