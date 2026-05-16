from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, cast

from pydub import AudioSegment

from subtitle_llm.domain import Subtitle, SubtitleEntry
from subtitle_llm.io import SubtitleIO, seconds_to_srt_time
from subtitle_llm.settings import ASRConfig


def transcribe(audio_path: str | Path, language: str, output_path: str | Path, config: ASRConfig | None = None) -> str:
    qwen_asr: Any = importlib.import_module("qwen_asr")
    Qwen3ASRModel = qwen_asr.Qwen3ASRModel

    config = config or ASRConfig()
    kwargs = {"device_map": config.device} if config.device else {}

    print(f"正在加载 Qwen3-ASR 模型 ({config.model})...")
    model = Qwen3ASRModel.from_pretrained(
        config.model,
        forced_aligner=config.forced_aligner,
        forced_aligner_kwargs=kwargs,
        **kwargs,
    )

    audio_path = Path(audio_path)
    audio = AudioSegment.from_file(audio_path)
    duration_sec = len(audio) / 1000.0

    if duration_sec <= config.max_audio_length:
        segments = [audio_path]
        offsets = [0.0]
    else:
        print(f"音频时长 {duration_sec:.1f}s，分割为 {config.max_audio_length}s 的片段...")
        segments, offsets = _split_audio(audio, audio_path, config.max_audio_length)

    subtitle = Subtitle()
    entry_index = 1

    for index, (segment_path, offset) in enumerate(zip(segments, offsets), start=1):
        print(f"正在转写片段 {index}/{len(segments)}...")
        results = model.transcribe(audio=[str(segment_path)], language=[language], return_time_stamps=True)
        for result in results:
            if not result.time_stamps:
                continue
            for timestamp in result.time_stamps:
                subtitle.add_entry(
                    SubtitleEntry(
                        index=entry_index,
                        start_time=seconds_to_srt_time(timestamp.start_time + offset),
                        end_time=seconds_to_srt_time(timestamp.end_time + offset),
                        original_text=timestamp.text.strip(),
                    )
                )
                entry_index += 1

        if segment_path != audio_path:
            os.remove(segment_path)

    SubtitleIO.write_srt(subtitle, output_path, output_format="source-only")
    print(f"字幕已生成：{output_path}")
    return str(output_path)


def _split_audio(audio: AudioSegment, audio_path: Path, max_length: int):
    base = audio_path.with_suffix("")
    extension = audio_path.suffix
    segments: list[Path] = []
    offsets: list[float] = []
    chunk_ms = max_length * 1000

    for start_ms in range(0, len(audio), chunk_ms):
        end_ms = min(start_ms + chunk_ms, len(audio))
        offset = start_ms / 1000.0
        segment_path = Path(f"{base}_segment_{offset:.0f}{extension}")
        chunk = cast(Any, audio)[start_ms:end_ms]
        chunk.export(segment_path, format="wav")
        segments.append(segment_path)
        offsets.append(offset)

    return segments, offsets
