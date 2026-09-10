from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from subtitle_llm.cli.result_events import emit_result_event
from subtitle_llm.media import download as download_media
from subtitle_llm.media import mux_subtitle_track
from subtitle_llm.media.asr_models import resolve_asr_config
from subtitle_llm.media.muxer import MuxError
from subtitle_llm.media import transcribe as transcribe_audio
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.pipeline.report import TranslationReport
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.progress_events import ProgressEmitter
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


def _validate_translate_options(
    *,
    input_file: str | None,
    target_language: str | None,
    output_file: str | None,
    resume: bool,
    task_id: str | None,
) -> None:
    if task_id:
        if not resume:
            typer.secho("--task-id 只能和 --resume 一起使用", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        if input_file or output_file:
            typer.secho("--task-id 恢复不能同时指定新的 --input 或 --output", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        return
    if not input_file:
        typer.secho("缺少 --input；如需精确恢复任务，请使用 --task-id --resume", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    if not target_language:
        typer.secho("缺少 --target-language", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)


def _load_service(
    config_path: Path | None,
    asr_model: str | None = None,
    asr_device: str | None = None,
) -> TranslationService:
    try:
        config = load_config(config_path)
        if asr_model or asr_device:
            config.asr = resolve_asr_config(config.asr, profile=asr_model, device=asr_device)
        return TranslationService(config)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


@app.command()
def translate(
    input_file: Annotated[
        str | None,
        typer.Option("--input", "-i", help="Input .srt/.json file or video URL."),
    ] = None,
    target_language: Annotated[
        str | None,
        typer.Option("--target-language", "-t", help="Target translation language."),
    ] = None,
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
    resume: Annotated[bool, typer.Option("--resume", help="Resume from a translation task record.")] = False,
    task_id: Annotated[
        str | None,
        typer.Option("--task-id", help="Resume a specific translation task record."),
    ] = None,
    review: Annotated[
        bool | None,
        typer.Option("--review/--no-review", help="Use TUI review for suspicious chunks."),
    ] = None,
    refine: Annotated[
        bool | None,
        typer.Option("--refine/--no-refine", help="Run a second LLM refinement pass after the rough translation."),
    ] = None,
    force_asr: Annotated[
        bool,
        typer.Option("--force-asr/--no-force-asr", help="For URL input, skip source subtitles and generate subtitles with ASR."),
    ] = False,
    asr_model: Annotated[
        str | None,
        typer.Option("--asr-model", help="ASR 模型 profile：fun-asr-nano / paraformer-zh / qwen3-asr（覆盖配置文件 asr 段）。"),
    ] = None,
    asr_device: Annotated[
        str | None,
        typer.Option("--asr-device", help="ASR 推理设备：cpu / mps / cuda。默认 cpu。"),
    ] = None,
    embed_video: Annotated[
        bool,
        typer.Option("--embed-video/--no-embed-video", help="Generate an MKV with the translated subtitle as a styled ASS soft subtitle track."),
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
    _validate_translate_options(
        input_file=input_file,
        target_language=target_language,
        output_file=output_file,
        resume=resume,
        task_id=task_id,
    )
    log_path = configure_run_logging("translate")
    progress = ProgressEmitter("translate")
    progress.emit(
        stage="startup",
        detail="load_config",
        label="加载配置",
        message="正在加载模型和翻译配置",
    )
    logger.info(
        "用户操作: translate input=%s output=%s target_language=%s source_language=%s config=%s format=%s resume=%s task_id=%s review=%s refine=%s force_asr=%s embed_video=%s video=%s video_output=%s",
        input_file,
        output_file,
        target_language,
        source_language,
        config,
        output_format,
        resume,
        task_id,
        review,
        refine,
        force_asr,
        embed_video,
        video_file,
        video_output,
    )
    try:
        service = _load_service(config, asr_model=asr_model, asr_device=asr_device)
        result = service.translate(
            TranslationRequest(
                input_file=input_file,
                output_file=output_file,
                target_language=target_language or "",
                source_language=source_language,
                output_format=output_format,
                resume=resume,
                review_mode=None if review is None else ("tui" if review else "auto"),
                refine_translation=refine,
                force_asr=force_asr,
                task_id=task_id,
            ),
            progress=progress,
            emit_complete=not embed_video,
        )
        if embed_video:
            _embed_translated_subtitle(
                report=result.report,
                video_file=video_file,
                video_output=video_output,
                target_language=result.report.target_language or target_language or "Chinese",
                ffmpeg=ffmpeg,
                progress=progress,
            )
            progress.emit(
                stage="complete",
                detail="complete",
                status="warning" if result.report.embedded_video_error else "done",
                label="完成",
                message=(
                    f"翻译完成，MKV 未生成：{result.report.embedded_video_error}"
                    if result.report.embedded_video_error
                    else "翻译和 MKV 生成已完成"
                ),
                total_chunks=result.report.total_chunks,
            )
    except Exception:
        logger.exception("命令失败: translate")
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        trace_dir = log_path.with_name(f"{log_path.stem}_llm_trace")
        if trace_dir.exists():
            typer.secho(f"LLM诊断：{trace_dir}", fg=typer.colors.YELLOW, err=True)
        raise
    logger.info(
        "命令完成: translate output=%s source_entries=%s final_output_entries=%s chunks=%s failed_chunks=%s total_tokens=%s",
        result.report.output_file,
        result.report.total_entries,
        result.report.final_output_entries or result.report.processed_entries,
        result.report.total_chunks,
        len(result.report.failed_chunks),
        result.report.token_usage.total_tokens,
    )
    _print_report(result.report)
    typer.echo(f"日志文件：{log_path}")


@app.command()
def mux(
    video: Annotated[Path, typer.Argument(help="Video file path.")],
    subtitle: Annotated[Path, typer.Argument(help="Translated .srt/.ass file path.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output MKV path. Defaults to the subtitle output directory."),
    ] = None,
    target_language: Annotated[str, typer.Option("--target-language", "-t", help="Subtitle track language.")] = "Chinese",
    ffmpeg: Annotated[str, typer.Option("--ffmpeg", help="FFmpeg executable path.")] = "ffmpeg",
) -> None:
    """Mux translated subtitles as the default styled ASS soft subtitle track in an MKV."""
    log_path = configure_run_logging("mux")
    progress = ProgressEmitter("mux")
    progress.emit(
        stage="mux",
        detail="validate_input",
        label="检查输入",
        message="正在检查视频和字幕文件",
    )
    logger.info(
        "用户操作: mux video=%s subtitle=%s output=%s target_language=%s ffmpeg=%s",
        video,
        subtitle,
        output,
        target_language,
        ffmpeg,
    )
    try:
        progress.emit(
            stage="mux",
            detail="run_ffmpeg",
            label="执行 FFmpeg",
            message="正在生成 MKV 软字幕视频",
        )
        result = mux_subtitle_track(
            video_file=video,
            subtitle_file=subtitle,
            output_file=output,
            target_language=target_language,
            ffmpeg=ffmpeg,
        )
    except MuxError as exc:
        progress.emit(
            stage="mux",
            detail="run_ffmpeg",
            status="failed",
            label="生成失败",
            message=str(exc),
        )
        logger.exception("命令失败: mux")
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(2) from exc
    logger.info("命令完成: mux output=%s", result.output_file)
    progress.emit(
        stage="complete",
        detail="complete",
        status="done",
        label="完成",
        message=f"MKV 已生成：{result.output_file}",
    )
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
    progress = ProgressEmitter("download")
    progress.emit(
        stage="startup",
        detail="prepare_task",
        label="启动任务",
        message="正在准备下载任务",
    )
    logger.info(
        "用户操作: download url=%s output_dir=%s source_language=%s",
        url,
        output_dir,
        source_language,
    )
    try:
        result = download_media(url, output_dir, source_language, progress=progress)
    except Exception:
        logger.exception("命令失败: download")
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        raise
    logger.info("命令完成: download result=%s", result)
    progress.emit(
        stage="complete",
        detail="complete",
        status="done",
        label="完成",
        message="下载任务完成",
    )
    typer.echo(result)
    typer.echo(f"日志文件：{log_path}")


@app.command()
def transcribe(
    audio: Annotated[Path, typer.Argument(help="Audio file path.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output .srt file.")],
    language: Annotated[str, typer.Option("--language", "-l", help="Spoken language.")] = "English",
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path.")] = None,
    asr_model: Annotated[
        str | None,
        typer.Option("--asr-model", help="ASR 模型 profile：fun-asr-nano / paraformer-zh / qwen3-asr（覆盖配置文件 asr 段）。"),
    ] = None,
    asr_device: Annotated[
        str | None,
        typer.Option("--asr-device", help="ASR 推理设备：cpu / mps / cuda。默认 cpu。"),
    ] = None,
) -> None:
    """Transcribe audio to SRT with the configured ASR model."""
    log_path = configure_run_logging("transcribe")
    progress = ProgressEmitter("transcribe")
    progress.emit(
        stage="startup",
        detail="load_config",
        label="加载配置",
        message="正在加载转写配置",
    )
    logger.info(
        "用户操作: transcribe audio=%s output=%s language=%s config=%s",
        audio,
        output,
        language,
        config,
    )
    try:
        app_config = load_config(config)
        if asr_model or asr_device:
            app_config.asr = resolve_asr_config(app_config.asr, profile=asr_model, device=asr_device)
        transcribe_audio(audio, language, output, app_config.asr, progress=progress)
    except Exception:
        logger.exception("命令失败: transcribe")
        typer.secho(f"日志文件：{log_path}", fg=typer.colors.YELLOW, err=True)
        raise
    logger.info("命令完成: transcribe output=%s", output)
    progress.emit(
        stage="complete",
        detail="complete",
        status="done",
        label="完成",
        message=f"字幕已生成：{output}",
    )
    typer.echo(f"日志文件：{log_path}")


@app.command("tasks")
def list_tasks(
    include_deleted: Annotated[
        bool,
        typer.Option("--include-deleted", help="Include soft-deleted translation task records."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print task records as JSON."),
    ] = False,
    delete: Annotated[
        str | None,
        typer.Option("--delete", help="Soft-delete a translation task record by task id."),
    ] = None,
    restore: Annotated[
        str | None,
        typer.Option("--restore", help="Restore a soft-deleted translation task record by task id."),
    ] = None,
) -> None:
    """List or soft-delete translation task records."""
    store = TranslationTaskStore()
    if delete:
        store.soft_delete(delete)
        typer.echo(f"已删除任务记录：{delete}")
        return
    if restore:
        store.restore_deleted(restore)
        typer.echo(f"已恢复任务记录：{restore}")
        return

    records = [
        {
            "task_id": record.task_id,
            "status": record.status,
            "input_display": record.input_display,
            "working_directory": record.working_directory,
            "source_subtitle_path": record.source_subtitle_path,
            "target_language": record.target_language,
            "source_language": record.source_language,
            "output_format": record.output_format,
            "output_file": record.output_file,
            "context_file": record.context_file,
            "llm_trace_dir": record.llm_trace_dir,
            "source_video_file": record.source_video_file,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "deleted_at": record.deleted_at,
        }
        for record in store.list_tasks(include_deleted=include_deleted)
    ]
    if json_output:
        typer.echo(json.dumps(records, ensure_ascii=False))
        return

    if not records:
        typer.echo("暂无翻译任务记录")
        return
    for record in records:
        deleted = " 已删除" if record.get("deleted_at") else ""
        typer.echo(
            f"{record['task_id']} [{record['status']}]{deleted} "
            f"{record['input_display']} -> {record['output_file']}"
        )


def _embed_translated_subtitle(
    report: TranslationReport,
    video_file: Path | None,
    video_output: Path | None,
    target_language: str,
    ffmpeg: str,
    progress: ProgressEmitter | None = None,
) -> None:
    source_video = str(video_file) if video_file else report.source_video_file
    if not source_video:
        report.embedded_video_error = "未选择视频，跳过生成 MKV"
        if progress is not None:
            progress.emit(
                stage="generate_result",
                detail="mux_skipped",
                status="skipped",
                label="生成 MKV",
                message=report.embedded_video_error,
            )
        logger.warning("视频封装跳过: %s", report.embedded_video_error)
        return

    try:
        if progress is not None:
            progress.emit(
                stage="generate_result",
                detail="mux_video",
                label="生成 MKV",
                message="正在将字幕作为软字幕轨道嵌入视频",
            )
        result = mux_subtitle_track(
            video_file=source_video,
            subtitle_file=report.output_file,
            output_file=video_output,
            target_language=target_language,
            ffmpeg=ffmpeg,
        )
    except MuxError as exc:
        report.embedded_video_error = str(exc)
        if progress is not None:
            progress.emit(
                stage="generate_result",
                detail="mux_video",
                status="failed",
                label="生成 MKV",
                message=str(exc),
            )
        logger.exception("视频封装失败: video=%s subtitle=%s", source_video, report.output_file)
        return

    report.embedded_video_file = result.output_file
    if progress is not None:
        progress.emit(
            stage="generate_result",
            detail="mux_video",
            status="done",
            label="生成 MKV",
            message=f"MKV 已生成：{result.output_file}",
        )
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
    if report.task_id:
        typer.echo(f"任务记录：{report.task_id}")
    if report.task_db_file:
        typer.echo(f"任务数据库：{report.task_db_file}")
    if report.llm_trace_dir:
        typer.echo(f"LLM诊断：{report.llm_trace_dir}")
    typer.echo(f"输出格式：{report.output_format}")
    final_output_entries = report.final_output_entries or report.processed_entries
    typer.echo(
        f"字幕条数：源 {report.total_entries}，输出 {final_output_entries}，短句保留：{report.short_entries}"
    )
    if report.auto_layout_repairs:
        typer.echo(f"语义布局自动修复：{len(report.auto_layout_repairs)} 处")
    typer.echo(f"Chunk：成功 {successful_chunks} / {report.total_chunks}，失败 {len(report.failed_chunks)}")
    typer.echo(f"疑似跨 Chunk 断句边界：{report.boundary_risk_count}")
    usage = report.token_usage
    typer.echo(
        "Token："
        f"{usage.total_tokens} "
        f"(prompt={usage.prompt_tokens}, "
        f"completion={usage.completion_tokens})"
    )
    if usage.prompt_cache_hit_tokens or usage.prompt_cache_miss_tokens:
        typer.echo(
            f"Prompt 缓存：命中 {usage.prompt_cache_hit_tokens} "
            f"/ {usage.prompt_cache_hit_tokens + usage.prompt_cache_miss_tokens} "
            f"({usage.cache_hit_rate()}%)"
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
        task_id=report.task_id,
        task_db_file=report.task_db_file,
        llm_trace_dir=report.llm_trace_dir,
        output_format=report.output_format,
    )


if __name__ == "__main__":
    app()
