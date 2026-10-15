import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
import xml.etree.ElementTree as ET


spec = importlib.util.spec_from_file_location('installer_metadata', Path(__file__).resolve().parents[1] / 'macos/scripts/installer_metadata.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)


class InstallerMetadataTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = Path(self.folder.name) / '通讯助手.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        (self.app / 'Contents/MacOS/CommunicationsAssistant').write_bytes(b'synthetic binary')
        self.info = {'CFBundleIdentifier': 'local.communications.assistant',
                     'CFBundleExecutable': 'CommunicationsAssistant',
                     'CFBundleShortVersionString': '0.3.3', 'CFBundleVersion': '12',
                     'LSMinimumSystemVersion': '14.0'}

    def tearDown(self):
        self.folder.cleanup()

    def generate(self):
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(self.info))
        return installer.write_metadata(self.app, Path(self.folder.name))

    def test_wrong_application_or_missing_binary_rejected(self):
        self.info['CFBundleIdentifier'] = 'other.application'
        with self.assertRaisesRegex(ValueError, '应用标识'): self.generate()
        self.info['CFBundleIdentifier'] = 'local.communications.assistant'
        (self.app / 'Contents/MacOS/CommunicationsAssistant').unlink()
        with self.assertRaisesRegex(ValueError, '可执行文件'): self.generate()

    def test_invalid_versions_cannot_enter_distribution(self):
        for key in ('CFBundleShortVersionString', 'LSMinimumSystemVersion'):
            for value in ('', '<script/>', '../14', '14.0;exit', '١.٢.٣', '１４.０', 14):
                with self.subTest(key=key, value=value):
                    original = self.info[key]; self.info[key] = value
                    with self.assertRaisesRegex(ValueError, '版本'): self.generate()
                    self.info[key] = original

    def test_package_uses_actual_app_version_and_minimum_system(self):
        self.info.update(CFBundleShortVersionString='1.2.3', LSMinimumSystemVersion='15.2')
        self.assertEqual(self.generate(), '1.2.3')
        xml = ET.parse(Path(self.folder.name) / 'Distribution.xml').getroot()
        self.assertEqual(xml.find('.//os-version').get('min'), '15.2')
        self.assertEqual(xml.find('pkg-ref').get('version'), '1.2.3')
        self.assertEqual(xml.find('options').get('hostArchitectures'), 'arm64')

    def test_upgrade_is_nonrelocatable_and_closes_existing_app(self):
        self.generate()
        components = plistlib.loads((Path(self.folder.name) / 'components.plist').read_bytes())
        self.assertFalse(components[0]['BundleIsRelocatable'])
        self.assertTrue(components[0]['BundleIsVersionChecked'])
        self.assertEqual(components[0]['BundleOverwriteAction'], 'upgrade')
        xml = ET.parse(Path(self.folder.name) / 'Distribution.xml').getroot()
        self.assertEqual(xml.find('.//must-close/app').get('id'), self.info['CFBundleIdentifier'])
        self.assertEqual(xml.find('domains').get('enable_currentUserHome'), 'false')
        self.assertEqual(xml.find('options').get('require-scripts'), 'false')
