from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from subtitle_llm.media import download as download_media
from subtitle_llm.media import transcribe as transcribe_audio
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.settings import ConfigError, load_config

app = typer.Typer(
    name="subtitle-llm",
    help="Translate, download, and transcribe subtitle files with LLMs.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


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
    output_file: Annotated[str, typer.Option("--output", "-o", help="Output .srt file.")],
    target_language: Annotated[str, typer.Option("--target-language", "-t", help="Target translation language.")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path.")] = None,
    source_language: Annotated[str, typer.Option("--source-language", "-s", help="Source language for URL subtitles/ASR.")] = "en",
    output_format: Annotated[
        str | None,
        typer.Option(
            "--format",
            help="source-first, target-first, target-only, source-only, or bilingual.",
        ),
    ] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Resume from checkpoint if metadata matches.")] = False,
    review: Annotated[bool, typer.Option("--review/--no-review", help="Use TUI review for suspicious chunks.")] = False,
) -> None:
    """Translate an SRT file, word-level JSON transcript, or video URL."""
    service = _load_service(config)
    result = service.translate(
        TranslationRequest(
            input_file=input_file,
            output_file=output_file,
            target_language=target_language,
            source_language=source_language,
            output_format=output_format,
            resume=resume,
            review_mode="tui" if review else "auto",
        )
    )
    _print_report(result.report)


@app.command()
def download(
    url: Annotated[str, typer.Argument(help="Video URL.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o", help="Directory for downloaded media/subtitles.")] = Path("data/input"),
    source_language: Annotated[str, typer.Option("--source-language", "-s", help="Subtitle language.")] = "en",
) -> None:
    """Download a video and available subtitles."""
    result = download_media(url, output_dir, source_language)
    typer.echo(result)


@app.command()
def transcribe(
    audio: Annotated[Path, typer.Argument(help="Audio file path.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output .srt file.")],
    language: Annotated[str, typer.Option("--language", "-l", help="Spoken language.")] = "English",
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path.")] = None,
) -> None:
    """Transcribe audio to SRT with the configured ASR model."""
    app_config = load_config(config)
    transcribe_audio(audio, language, output, app_config.asr)


def _print_report(report) -> None:
    successful_chunks = report.completed_chunks - len(report.failed_chunks)
    typer.echo("\n===== 翻译完成 =====")
    typer.echo(f"输出文件：{report.output_file}")
    typer.echo(f"上下文文件：{report.context_file}")
    typer.echo(f"断点文件：{report.checkpoint_file}")
    typer.echo(f"输出格式：{report.output_format}")
    typer.echo(
        f"字幕条数：{report.total_entries}，"
        f"已处理：{report.processed_entries}，"
        f"短句保留：{report.short_entries}"
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


if __name__ == "__main__":
    app()
