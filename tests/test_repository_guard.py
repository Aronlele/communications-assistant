import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('source_guard', Path(__file__).resolve().parents[1] / 'scripts/check_source.py')
guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


class RepositoryGuardTests(unittest.TestCase):
    def test_private_paths_rejected_even_when_force_added(self):
        for path in ('chatlog/work.txt', 'macos/Private/config.json', 'output/a.zip', 'telegram.session-journal',
                     '.env.local', 'user.sqlite-wal', 'macos/ACCEPTANCE.md'):
            with self.subTest(path=path): self.assertTrue(guard.violations(path, b''))

    def test_github_and_bot_credentials_rejected(self):
        for value in (b'ghp_' + b'x' * 36, b'github_pat_' + b'x' * 40, b'123456789:' + b'a' * 35):
            self.assertTrue(guard.violations('README.md', value))

    def test_known_api_hash_caught_without_printing_it(self):
        value = b'abcdef0123456789' * 2
        self.assertTrue(guard.violations('design/a.md', b'configuration:' + value, [value]))

    def test_placeholders_and_code_are_allowed(self):
        self.assertFalse(guard.violations('.env.example', b'API_HASH=your_telegram_api_hash'))
        self.assertFalse(guard.violations('macos/engine/store.py', b'Token and api_hash are excluded'))

    def test_documentation_source_is_allowed(self):
        self.assertFalse(guard.violations('design/product-design.md', '默认仅提醒，示例账号'.encode()))

    def test_binary_tables_not_mistaken_for_tokens_but_known_secret_caught(self):
        sequence = b'123456:' + b'abcdefghijklmnopqrstuvwxyz0123456789'
        self.assertFalse(guard.violations('docs/assets/demo.jpg', sequence, scan_text=False))
        value = b'private configuration sentinel'
        self.assertTrue(guard.violations('docs/assets/demo.jpg', value, [value], scan_text=False))
