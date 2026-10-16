import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.configure_api import configure


class APIBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.json"
        self.destination = self.root / "app/TelegramAPI.json"

    def test_bundled_configuration_and_permissions(self):
        self.source.write_text(json.dumps({"api_id": 12345, "api_hash": "a" * 32, "unrelated": "discard"}))
        self.source.chmod(0o600)
        self.assertTrue(configure(self.source, self.destination, release=True))
        self.assertEqual(json.loads(self.destination.read_text()), {"api_id": 12345, "api_hash": "a" * 32})
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o600)

    def test_missing_development_configuration_removes_previous_credentials(self):
        self.destination.parent.mkdir()
        self.destination.write_text("obsolete credentials")
        self.assertFalse(configure(None, self.destination))
        self.assertFalse(self.destination.exists())

    def test_release_requires_configuration(self):
        with self.assertRaisesRegex(ValueError, "正式发布"):
            configure(None, self.destination, release=True)
        self.assertFalse(self.destination.exists())

    def test_explicit_missing_file_fails_even_for_development(self):
        with self.assertRaises(ValueError):
            configure(self.source, self.destination)

    def test_source_cannot_be_deleted_as_destination(self):
        self.source.write_text("keep original input")
        with self.assertRaises(ValueError):
            configure(self.source, self.source)
        self.assertEqual(self.source.read_text(), "keep original input")

    def test_invalid_values_never_packaged_or_exposed(self):
        invalid = [None, [], {}, {"api_id": True, "api_hash": "a" * 32},
                   {"api_id": 0, "api_hash": "a" * 32}, {"api_id": 2147483648, "api_hash": "a" * 32},
                   {"api_id": "12345", "api_hash": "a" * 32}, {"api_id": 1, "api_hash": 123},
                   {"api_id": 1, "api_hash": "secret_invalid_value"}]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                self.source.write_text(json.dumps(value))
                with self.assertRaises(ValueError) as caught:
                    configure(self.source, self.destination)
                self.assertNotIn("secret_invalid_value", str(caught.exception))
                self.assertFalse(self.destination.exists())

    def test_cli_does_not_log_credentials(self):
        secret = "b" * 32
        self.source.write_text(json.dumps({"api_id": 54321, "api_hash": secret}))
        script = Path(__file__).resolve().parents[1] / "scripts/configure_api.py"
        result = subprocess.run([sys.executable, str(script), "--source", str(self.source),
                                 "--destination", str(self.destination), "--release"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn("54321", result.stdout + result.stderr)
