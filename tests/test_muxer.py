import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pysubs2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.media.muxer import (
    FfmpegNotFound,
    ASS_STYLE_NAME,
    build_mux_command,
    file_language_code,
    mux_subtitle_track,
    prepare_ass_subtitle_file,
    resolve_output_path,
    subtitle_language_code,
)


class TestMuxer(unittest.TestCase):
    def test_build_mux_command_maps_video_audio_and_subtitle(self):
        command = build_mux_command(
            video_file="video.mp4",
            subtitle_file="subtitle.ass",
            output_file="output.mkv",
            target_language="Chinese",
            ffmpeg="/usr/local/bin/ffmpeg",
        )

        self.assertEqual(command[0], "/usr/local/bin/ffmpeg")
        self.assertIn("-map", command)
        self.assertIn("0:v", command)
        self.assertIn("0:a?", command)
        self.assertIn("1:0", command)
        self.assertIn("-c", command)
        self.assertIn("copy", command)
        self.assertIn("-c:s", command)
        self.assertIn("ass", command)
        self.assertIn("language=zho", command)
        self.assertIn("title=Chinese bilingual", command)
        self.assertIn("-disposition:s:0", command)
        self.assertIn("default", command)

    def test_resolve_output_path_uses_subtitle_directory_and_avoids_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "translated.zh.srt"
            subtitle.write_text("subtitle", encoding="utf-8")
            existing = Path(tmp) / "Demo.zh.mkv"
            existing.write_text("video", encoding="utf-8")

            output = resolve_output_path("Demo.mp4", subtitle, "Chinese")

        self.assertTrue(str(output).endswith("Demo.zh.1.mkv"))

    def test_language_codes_have_user_facing_defaults(self):
        self.assertEqual(file_language_code("Chinese"), "zh")
        self.assertEqual(subtitle_language_code("Chinese"), "zho")
        self.assertEqual(file_language_code("Klingon"), "klingon")
        self.assertEqual(subtitle_language_code("Klingon"), "kli")

    def test_prepare_ass_subtitle_file_converts_srt_with_default_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "Demo.zh.srt"
            ass = Path(tmp) / "Demo.zh.ass"
            srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello\n你好\n",
                encoding="utf-8",
            )

            output = prepare_ass_subtitle_file(srt, ass)
            styled = pysubs2.load(str(output), encoding="utf-8", format_="ass")

        self.assertEqual(output, ass)
        self.assertEqual(styled.info["PlayResX"], "1280")
        self.assertEqual(styled.info["PlayResY"], "720")
        self.assertIn(ASS_STYLE_NAME, styled.styles)
        self.assertEqual(styled.styles[ASS_STYLE_NAME].fontsize, 34.0)
        self.assertEqual(styled.events[0].style, ASS_STYLE_NAME)
        self.assertEqual(styled.events[0].text, r"Hello\N你好")

    def test_mux_subtitle_track_runs_ffmpeg_without_overwriting_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "Demo.mp4"
            subtitle = Path(tmp) / "Demo.zh.srt"
            existing = Path(tmp) / "Demo.zh.mkv"
            video.write_text("video", encoding="utf-8")
            subtitle.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello\n你好\n",
                encoding="utf-8",
            )
            existing.write_text("existing", encoding="utf-8")

            completed = Mock(returncode=0, stdout="", stderr="")
            with patch("subtitle_llm.media.muxer.shutil.which", return_value="/bin/ffmpeg"):
                def run_ffmpeg(command, capture_output, text):
                    input_positions = [index for index, value in enumerate(command) if value == "-i"]
                    subtitle_input = Path(command[input_positions[1] + 1])
                    self.assertEqual(subtitle_input.suffix, ".ass")
                    self.assertTrue(subtitle_input.exists())
                    self.assertIn("[V4+ Styles]", subtitle_input.read_text(encoding="utf-8"))
                    return completed

                with patch("subtitle_llm.media.muxer.subprocess.run", side_effect=run_ffmpeg) as run:
                    result = mux_subtitle_track(video, subtitle, target_language="Chinese")

            self.assertTrue(result.output_file.endswith("Demo.zh.1.mkv"))
            self.assertEqual(run.call_args.args[0][0], "/bin/ffmpeg")
            self.assertIn("ass", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["capture_output"], True)
            self.assertEqual(run.call_args.kwargs["text"], True)

    def test_mux_subtitle_track_requires_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "Demo.mp4"
            subtitle = Path(tmp) / "Demo.zh.srt"
            video.write_text("video", encoding="utf-8")
            subtitle.write_text("subtitle", encoding="utf-8")

            with patch("subtitle_llm.media.muxer.shutil.which", return_value=None):
                with self.assertRaises(FfmpegNotFound):
                    mux_subtitle_track(video, subtitle, target_language="Chinese")


if __name__ == "__main__":
    unittest.main()
