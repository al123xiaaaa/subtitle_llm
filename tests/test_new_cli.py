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

        with runner.isolated_filesystem():
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
            self.assertIn("日志文件：", result.output)
            log_text = self._single_log("translate").read_text(encoding="utf-8")
            self.assertIn("用户操作: translate", log_text)
            self.assertIn("命令完成: translate", log_text)

            request = fake_service.translate.call_args.args[0]
            self.assertIsNone(request.review_mode)

    def test_translate_command_maps_review_flags(self):
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
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with runner.isolated_filesystem():
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
                        "--review",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            request = fake_service.translate.call_args.args[0]
            self.assertEqual(request.review_mode, "tui")

    def test_translate_command_allows_omitting_output(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="data/output/input.zh.srt",
            checkpoint_file="data/output/input.zh_checkpoint.json",
            context_file="data/output/input.zh_context.txt",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with runner.isolated_filesystem():
            with patch("subtitle_llm.cli.app._load_service", return_value=fake_service):
                result = runner.invoke(
                    app,
                    [
                        "translate",
                        "--input",
                        "input.srt",
                        "--target-language",
                        "Chinese",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            request = fake_service.translate.call_args.args[0]
            self.assertIsNone(request.output_file)

    def test_translate_command_logs_failures(self):
        runner = CliRunner()
        fake_service = Mock()
        fake_service.translate.side_effect = RuntimeError("boom")

        with runner.isolated_filesystem():
            with patch("subtitle_llm.cli.app._load_service", return_value=fake_service):
                result = runner.invoke(
                    app,
                    [
                        "translate",
                        "--input",
                        "input.srt",
                        "--target-language",
                        "Chinese",
                    ],
                )

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("日志文件：", result.output)
            log_text = self._single_log("translate").read_text(encoding="utf-8")
            self.assertIn("命令失败: translate", log_text)
            self.assertIn("RuntimeError: boom", log_text)

    def test_help_shows_commands(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Usage", result.output)
        self.assertIn("translate", result.output)

    def _single_log(self, command: str) -> Path:
        logs = list(Path("data/logs").glob(f"*_{command}_*.log"))
        self.assertEqual(len(logs), 1)
        return logs[0]


if __name__ == "__main__":
    unittest.main()
