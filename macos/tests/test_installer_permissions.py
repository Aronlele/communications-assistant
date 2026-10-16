from pathlib import Path
import tempfile
import unittest

from scripts.check_installer import validate_public_permissions


class InstalledPermissionsTests(unittest.TestCase):
    def test_root_owned_api_resource_must_be_readable_by_app_user(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'Test.app'; app.mkdir(mode=0o755)
            resource = app / 'TelegramAPI.json'; resource.write_text('synthetic client configuration')
            resource.chmod(0o600)
            with self.assertRaisesRegex(AssertionError, '普通用户无法读取'):
                validate_public_permissions(app)
            resource.chmod(0o644)
            validate_public_permissions(app)

    def test_directory_traversal_and_executable_access_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'Test.app'; app.mkdir(mode=0o755)
            executable = app / 'engine'; executable.write_text('synthetic executable'); executable.chmod(0o700)
            with self.assertRaises(AssertionError): validate_public_permissions(app)
            executable.chmod(0o755)
            app.chmod(0o700)
            with self.assertRaises(AssertionError): validate_public_permissions(app)
            app.chmod(0o755)
            validate_public_permissions(app)
