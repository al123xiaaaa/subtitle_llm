import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.review.tui_manager import TUIManager
from subtitle_llm.runtime_logging import RUN_LOG_ENV


class TestTUIManager(unittest.TestCase):
    def test_worker_command_passes_run_log_path(self):
        manager = TUIManager()
        log_file = "/tmp/subtitle llm/run.log"

        with patch.dict(os.environ, {RUN_LOG_ENV: log_file}):
            with patch("subtitle_llm.review.tui_manager.platform.system", return_value="Linux"):
                with patch.object(TUIManager, "_find_virtualenv_activate", return_value=None):
                    with patch("subtitle_llm.review.tui_manager.subprocess.Popen") as popen:
                        manager._ensure_worker()

        command = popen.call_args.args[0][4]
        self.assertIn("tui_worker.py", command)
        self.assertIn("'/tmp/subtitle llm/run.log'", command)


if __name__ == "__main__":
    unittest.main()
