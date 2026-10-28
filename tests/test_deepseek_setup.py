import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from scripts import configure_deepseek as setup
from scripts.configure_deepseek import save_key


class CredentialSetupTests(unittest.TestCase):
    def test_terminal_without_hidden_input_does_not_save_a_key(self):
        def unsupported(*args):
            warnings.warn("No hidden input", setup.getpass.GetPassWarning)
        with patch.object(setup.getpass, "getpass", side_effect=unsupported), \
             patch.object(setup, "save_key") as save, patch("builtins.print"):
            self.assertEqual(setup.main([]), 1)
            save.assert_not_called()

    def test_rotation_preserves_mail_and_removes_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("# local\nEMAIL_AUTO_SEND=0\nEMAIL_SIGNATURE=Example\nDEEPSEEK_API_KEY=old\nexport DEEPSEEK_API_KEY=duplicate\nDEEPSEEK_ANALYSIS_MODEL=chosen-model\n")
            key = "sk-" + "test-only-credential" * 2
            save_key(path, key)
            text = path.read_text()
            self.assertIn("EMAIL_AUTO_SEND=0\nEMAIL_SIGNATURE=Example", text)
            self.assertEqual(text.count("DEEPSEEK_API_KEY="), 1)
            self.assertNotIn("duplicate", text)
            self.assertIn("DEEPSEEK_ANALYSIS_MODEL=chosen-model", text)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_input_cannot_inject_another_environment_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("EMAIL_AUTO_SEND=0\n")
            with self.assertRaises(ValueError):
                save_key(path, "sk-" + "a" * 20 + "\nEMAIL_AUTO_SEND=1")
            self.assertEqual(path.read_text(), "EMAIL_AUTO_SEND=0\n")

    @unittest.skipIf(os.name == "nt", "Symlink privileges differ on Windows")
    def test_refuses_symlink_and_leaves_target_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            target.write_text("protected")
            link = Path(folder) / ".env"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                save_key(link, "sk-" + "test-only-credential" * 2)
            self.assertEqual(target.read_text(), "protected")
