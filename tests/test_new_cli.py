import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from typer.testing import CliRunner

from subtitle_llm.cli.app import app
from subtitle_llm.pipeline.report import TranslationReport


class TestNewCLI(unittest.TestCase):
    def test_translate_command_invokes_service_and_prints_report(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            checkpoint_file="output_checkpoint.json",
            context_file="output_context.txt",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_result = Mock(report=report)
        fake_service = Mock()
        fake_service.translate.return_value = fake_result

        with patch("subtitle_llm.cli.app._load_service", return_value=fake_service):
            result = runner.invoke(
                app,
                [
                    "translate",
                    "--input",
                    "input.srt",
                    "--output",
                    "output.srt",
                    "--target-language",
                    "Chinese",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("翻译完成", result.output)
        fake_service.translate.assert_called_once()

    def test_help_shows_commands(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Usage", result.output)
        self.assertIn("translate", result.output)


if __name__ == "__main__":
    unittest.main()
