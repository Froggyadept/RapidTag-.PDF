import importlib.util
import os
import unittest
from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / "launcher.py"
SPEC = importlib.util.spec_from_file_location("rapidtag_launcher", LAUNCHER)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_app_path_is_next_to_launcher_not_current_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(Path(__file__).resolve().parents[3])
            self.assertEqual(launcher._app_path(), LAUNCHER.parent / "app.py")
            self.assertTrue(launcher._app_path().exists())
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()