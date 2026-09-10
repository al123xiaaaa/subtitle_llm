import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from typer.testing import CliRunner


@contextlib.contextmanager
def isolated_filesystem():
    """typer 0.27 移除了 CliRunner.isolated_filesystem，这里用临时目录替代。"""
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            yield tmp
        finally:
            os.chdir(old_cwd)

from subtitle_llm.media.muxer import MuxResult
from subtitle_llm.cli.app import app
from subtitle_llm.pipeline.report import TranslationReport


class TestNewCLI(unittest.TestCase):
    def test_translate_command_invokes_service_and_prints_report(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            context_file="output_context.txt",
            llm_trace_dir="data/logs/demo_llm_trace",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_result = Mock(report=report)
        fake_service = Mock()
        fake_service.translate.return_value = fake_result

        with isolated_filesystem():
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
            self.assertIn("LLM诊断：data/logs/demo_llm_trace", result.output)
            self.assertIn('"llm_trace_dir": "data/logs/demo_llm_trace"', result.output)
            self.assertIn("日志文件：", result.output)
            log_text = self._single_log("translate").read_text(encoding="utf-8")
            self.assertIn("用户操作: translate", log_text)
            self.assertIn("命令完成: translate", log_text)

            request = fake_service.translate.call_args.args[0]
            self.assertIsNone(request.review_mode)
            self.assertFalse(request.force_asr)

    def test_translate_command_maps_review_flags(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            context_file="output_context.txt",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with isolated_filesystem():
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

    def test_translate_command_maps_force_asr_flag(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            context_file="output_context.txt",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with isolated_filesystem():
            with patch("subtitle_llm.cli.app._load_service", return_value=fake_service):
                result = runner.invoke(
                    app,
                    [
                        "translate",
                        "--input",
                        "https://example.test/video",
                        "--target-language",
                        "Chinese",
                        "--force-asr",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            request = fake_service.translate.call_args.args[0]
            self.assertTrue(request.force_asr)

    def test_translate_command_allows_omitting_output(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="data/output/input.zh.srt",
            context_file="data/output/input.zh_context.txt",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with isolated_filesystem():
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

    def test_translate_command_allows_task_id_resume_without_input(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="/tmp/input.srt",
            output_file="/tmp/output.srt",
            context_file="/tmp/output_context.txt",
            task_id="task-123",
            task_db_file="/tmp/tasks.sqlite3",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with isolated_filesystem():
            with patch("subtitle_llm.cli.app._load_service", return_value=fake_service):
                result = runner.invoke(app, ["translate", "--task-id", "task-123", "--resume"])

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("任务记录：task-123", result.output)
            request = fake_service.translate.call_args.args[0]
            self.assertEqual(request.task_id, "task-123")
            self.assertTrue(request.resume)
            self.assertIsNone(request.input_file)

    def test_translate_command_rejects_task_id_with_new_input(self):
        runner = CliRunner()

        with isolated_filesystem():
            result = runner.invoke(
                app,
                [
                    "translate",
                    "--task-id",
                    "task-123",
                    "--resume",
                    "--input",
                    "other.srt",
                    "--target-language",
                    "Chinese",
                ],
            )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("不能同时指定新的 --input 或 --output", result.output)

    def test_translate_command_can_embed_video_after_translation(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            context_file="output_context.txt",
            source_video_file="downloaded.mp4",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with isolated_filesystem():
            with patch("subtitle_llm.cli.app._load_service", return_value=fake_service):
                with patch(
                    "subtitle_llm.cli.app.mux_subtitle_track",
                    return_value=MuxResult(output_file="output.mkv", command=["ffmpeg"]),
                ) as mux:
                    result = runner.invoke(
                        app,
                        [
                            "translate",
                            "--input",
                            "https://example.test/video",
                            "--target-language",
                            "Chinese",
                            "--embed-video",
                            "--ffmpeg",
                            "/bin/ffmpeg",
                        ],
                    )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("源视频：downloaded.mp4", result.output)
            self.assertIn("输出视频：output.mkv", result.output)
            mux.assert_called_once_with(
                video_file="downloaded.mp4",
                subtitle_file="output.srt",
                output_file=None,
                target_language="Chinese",
                ffmpeg="/bin/ffmpeg",
            )

    def test_translate_command_keeps_subtitle_success_when_embedding_fails(self):
        runner = CliRunner()
        report = TranslationReport(
            input_file="input.srt",
            output_file="output.srt",
            context_file="output_context.txt",
            total_entries=1,
            processed_entries=1,
            total_chunks=1,
            completed_chunks=1,
        )
        fake_service = Mock()
        fake_service.translate.return_value = Mock(report=report)

        with isolated_filesystem():
            with patch("subtitle_llm.cli.app._load_service", return_value=fake_service):
                result = runner.invoke(
                    app,
                    [
                        "translate",
                        "--input",
                        "input.srt",
                        "--target-language",
                        "Chinese",
                        "--embed-video",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("输出文件：output.srt", result.output)
            self.assertIn("视频封装：未选择视频，跳过生成 MKV", result.output)

    def test_translate_command_logs_failures(self):
        runner = CliRunner()
        fake_service = Mock()
        fake_service.translate.side_effect = RuntimeError("boom")

        with isolated_filesystem():
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

    def test_mux_command_prints_output_video(self):
        runner = CliRunner()
        with isolated_filesystem():
            with patch(
                "subtitle_llm.cli.app.mux_subtitle_track",
                return_value=MuxResult(output_file="output.mkv", command=["ffmpeg"]),
            ) as mux:
                result = runner.invoke(
                    app,
                    [
                        "mux",
                        "video.mp4",
                        "subtitle.srt",
                        "--target-language",
                        "Chinese",
                        "--ffmpeg",
                        "/bin/ffmpeg",
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("MKV 生成完成", result.output)
            self.assertIn("输出视频：output.mkv", result.output)
            mux.assert_called_once()
            self.assertEqual(mux.call_args.kwargs["video_file"], Path("video.mp4"))
            self.assertEqual(mux.call_args.kwargs["subtitle_file"], Path("subtitle.srt"))
            self.assertEqual(mux.call_args.kwargs["target_language"], "Chinese")

    def test_help_shows_commands(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Usage", result.output)
        self.assertIn("translate", result.output)
        self.assertIn("mux", result.output)

    def _single_log(self, command: str) -> Path:
        logs = list(Path("data/logs").glob(f"*_{command}_*.log"))
        self.assertEqual(len(logs), 1)
        return logs[0]


if __name__ == "__main__":
    unittest.main()
