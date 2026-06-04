from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from subtitle_llm.cli.result_events import emit_result_event
from subtitle_llm.media import download as download_media
from subtitle_llm.media import mux_subtitle_track
from subtitle_llm.media.muxer import MuxError
from subtitle_llm.media import transcribe as transcribe_audio
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.runtime_logging import configure_run_logging
from subtitle_llm.settings import ConfigError, load_config

# 自动加载项目根目录的 .env 文件（API keys 等）
load_dotenv()

app = typer.Typer(
    name="subtitle-llm",
    help="Translate, download, and transcribe subtitle files with LLMs.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

logger = logging.getLogger(__name__)


def _load_service(config_path: Path | None) -> TranslationService:
    try:
        config = load_config(config_path)
        return TranslationService(config)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


@app.command()
def translate(
    input_file: Annotated[str, typer.Option("--input", "-i", help="Input .srt/.json file or video URL.")],
    target_language: Annotated[str, typer.Option("--target-language", "-t", help="Target translation language.")],
    output_file: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help="Output .srt file. Defaults to data/output/<title>.<target>.srt.",
        ),
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path.")] = None,
    source_language: Annotated[
        str, typer.Option("--source-language", "-s", help="Source language for URL subtitles/ASR.")
    ] = "en",
    output_format: Annotated[
        str | None,
        typer.Option(
            "--format",
            help="source-first, target-first, target-only, source-only, or bilingual.",
        ),
    ] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Resume from checkpoint if metadata matches.")] = False,
    review: Annotated[
        bool | None,
        typer.Option("--review/--no-review", help="Use TUI review for suspicious chunks."),
    ] = None,
    embed_video: Annotated[
        bool,
        typer.Option("--embed-video/--no-embed-video", help="Generate an MKV with the translated SRT as a soft subtitle track."),
    ] = False,
    video_file: Annotated[
        Path | None,
        typer.Option("--video", help="Video file to mux with the translated subtitle. URL inputs can resolve this automatically."),
    ] = None,
    video_output: Annotated[
        Path | None,
        typer.Option("--video-output", help="Output MKV path. Defaults to the subtitle output directory."),
    ] = None,
    ffmpeg: Annotated[str, typer.Option("--ffmpeg", help="FFmpeg executable path.")] = "ffmpeg",
) -> None:
    """Translate an SRT file, word-level JSON transcript, or video URL."""
    log_path = configure_run_logging("translate")
    logger.info(
        "用户操作: translate input=%s output=%s target_language=%s source_language=%s config=%s format=%s resume=%s review=%s embed_video=%s video=%s video_output=%s",
        input_file,
        output_file,
        target_language,
        source_language,
        config,
        output_format,
        resume,
        review,
        embed_video,
        video_file,
        video_output,
    )
    try:
        service = _load_service(config)
        result = service.translate(
            TranslationRequest(
                input_file=input_file,
                output_file=output_file,
                target_language=target_language,
                source_language=source_language,
                output_format=output_format,
                resume=resume,
                review_mode=None if review is None else ("tui" if review else "auto"),
            )
        )
        if embed_video:
            _embed_translated_subtitle(
                report=result.report,
                video_file=video_file,
                video_output=video_output,
                target_language=target_language,
                ffmpeg=ffmpeg,
            )
    except Exception:
        logger.exception("命令失败: translate")
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        raise
    logger.info(
        "命令完成: translate output=%s entries=%s chunks=%s failed_chunks=%s total_tokens=%s",
        result.report.output_file,
        result.report.total_entries,
        result.report.total_chunks,
        len(result.report.failed_chunks),
        result.report.token_usage.total_tokens,
    )
    _print_report(result.report)
    typer.echo(f"日志文件：{log_path}")


@app.command()
def mux(
    video: Annotated[Path, typer.Argument(help="Video file path.")],
    subtitle: Annotated[Path, typer.Argument(help="Translated .srt file path.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output MKV path. Defaults to the subtitle output directory."),
    ] = None,
    target_language: Annotated[str, typer.Option("--target-language", "-t", help="Subtitle track language.")] = "Chinese",
    ffmpeg: Annotated[str, typer.Option("--ffmpeg", help="FFmpeg executable path.")] = "ffmpeg",
) -> None:
    """Mux translated SRT as the default soft subtitle track in an MKV."""
    log_path = configure_run_logging("mux")
    logger.info(
        "用户操作: mux video=%s subtitle=%s output=%s target_language=%s ffmpeg=%s",
        video,
        subtitle,
        output,
        target_language,
        ffmpeg,
    )
    try:
        result = mux_subtitle_track(
            video_file=video,
            subtitle_file=subtitle,
            output_file=output,
            target_language=target_language,
            ffmpeg=ffmpeg,
        )
    except MuxError as exc:
        logger.exception("命令失败: mux")
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(2) from exc
    logger.info("命令完成: mux output=%s", result.output_file)
    typer.echo("===== MKV 生成完成 =====")
    typer.echo(f"输出视频：{result.output_file}")
    emit_result_event("mux", output_video_file=result.output_file)
    typer.echo(f"日志文件：{log_path}")


@app.command()
def download(
    url: Annotated[str, typer.Argument(help="Video URL.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for downloaded media/subtitles.")
    ] = Path("data/input"),
    source_language: Annotated[str, typer.Option("--source-language", "-s", help="Subtitle language.")] = "en",
) -> None:
    """Download a video and available subtitles."""
    log_path = configure_run_logging("download")
    logger.info(
        "用户操作: download url=%s output_dir=%s source_language=%s",
        url,
        output_dir,
        source_language,
    )
    try:
        result = download_media(url, output_dir, source_language)
    except Exception:
        logger.exception("命令失败: download")
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        raise
    logger.info("命令完成: download result=%s", result)
    typer.echo(result)
    typer.echo(f"日志文件：{log_path}")


@app.command()
def transcribe(
    audio: Annotated[Path, typer.Argument(help="Audio file path.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output .srt file.")],
    language: Annotated[str, typer.Option("--language", "-l", help="Spoken language.")] = "English",
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path.")] = None,
) -> None:
    """Transcribe audio to SRT with the configured ASR model."""
    log_path = configure_run_logging("transcribe")
    logger.info(
        "用户操作: transcribe audio=%s output=%s language=%s config=%s",
        audio,
        output,
        language,
        config,
    )
    try:
        app_config = load_config(config)
        transcribe_audio(audio, language, output, app_config.asr)
    except Exception:
        logger.exception("命令失败: transcribe")
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        raise
    logger.info("命令完成: transcribe output=%s", output)
    typer.echo(f"日志文件：{log_path}")


def _embed_translated_subtitle(
    report: TranslationReport,
    video_file: Path | None,
    video_output: Path | None,
    target_language: str,
    ffmpeg: str,
) -> None:
    source_video = str(video_file) if video_file else report.source_video_file
    if not source_video:
        report.embedded_video_error = "未选择视频，跳过生成 MKV"
        logger.warning("视频封装跳过: %s", report.embedded_video_error)
        return

    try:
        result = mux_subtitle_track(
            video_file=source_video,
            subtitle_file=report.output_file,
            output_file=video_output,
            target_language=target_language,
            ffmpeg=ffmpeg,
        )
    except MuxError as exc:
        report.embedded_video_error = str(exc)
        logger.exception("视频封装失败: video=%s subtitle=%s", source_video, report.output_file)
        return

    report.embedded_video_file = result.output_file
    logger.info("视频封装完成: output=%s", result.output_file)


def _print_report(report) -> None:
    successful_chunks = report.completed_chunks - len(report.failed_chunks)
    typer.echo("\n===== 翻译完成 =====")
    typer.echo(f"输出文件：{report.output_file}")
    if report.source_video_file:
        typer.echo(f"源视频：{report.source_video_file}")
    if report.embedded_video_file:
        typer.echo(f"输出视频：{report.embedded_video_file}")
    if report.embedded_video_error:
        typer.echo(f"视频封装：{report.embedded_video_error}")
    typer.echo(f"上下文文件：{report.context_file}")
    typer.echo(f"断点文件：{report.checkpoint_file}")
    typer.echo(f"输出格式：{report.output_format}")
    typer.echo(
        f"字幕条数：{report.total_entries}，已处理：{report.processed_entries}，短句保留：{report.short_entries}"
    )
    typer.echo(f"Chunk：成功 {successful_chunks} / {report.total_chunks}，失败 {len(report.failed_chunks)}")
    typer.echo(f"疑似跨 Chunk 断句边界：{report.boundary_risk_count}")
    typer.echo(
        "Token："
        f"{report.token_usage.total_tokens} "
        f"(prompt={report.token_usage.prompt_tokens}, "
        f"completion={report.token_usage.completion_tokens})"
    )
    if report.failed_chunks:
        typer.echo("失败 chunk：")
        for failed in report.failed_chunks:
            typer.echo(f"- #{failed.chunk_index + 1}: {failed.error}")
    emit_result_event(
        "translate",
        output_file=report.output_file,
        source_video_file=report.source_video_file,
        embedded_video_file=report.embedded_video_file,
        embedded_video_error=report.embedded_video_error,
        context_file=report.context_file,
        checkpoint_file=report.checkpoint_file,
        output_format=report.output_format,
    )


if __name__ == "__main__":
    app()
