import os
from pydub import AudioSegment
from src.models.subtitle import Subtitle
from src.models.subtitle_entry import SubtitleEntry
from src.utils.utility_functions import seconds_to_srt_time


def transcribe(audio_path: str, language: str, output_path: str, config: dict = None):
    """
    Transcribe audio using Qwen3-ASR + ForcedAligner and generate an .srt file.

    Args:
        audio_path: Path to the audio file (wav/mp3/etc.)
        language: Language name (e.g. "English", "Chinese")
        output_path: Where to write the output .srt
        config: Optional dict with keys: model, forced_aligner, max_audio_length, device

    Returns:
        Path to the generated .srt file.
    """
    from qwen_asr import Qwen3ASRModel

    if config is None:
        config = {}

    model_name = config.get("model", "Qwen/Qwen3-ASR-1.7B")
    aligner_name = config.get("forced_aligner", "Qwen/Qwen3-ForcedAligner-0.6B")
    max_audio_length = config.get("max_audio_length", 300)
    device = config.get("device", None)

    kwargs = {}
    if device:
        kwargs["device_map"] = device

    print(f"正在加载 Qwen3-ASR 模型 ({model_name})...")
    model = Qwen3ASRModel.from_pretrained(
        model_name,
        forced_aligner=aligner_name,
        forced_aligner_kwargs=kwargs,
        **kwargs,
    )

    # Split audio into segments within the max length
    audio = AudioSegment.from_file(audio_path)
    duration_sec = len(audio) / 1000.0

    if duration_sec <= max_audio_length:
        segments = [audio_path]
        offsets = [0.0]
    else:
        print(f"音频时长 {duration_sec:.1f}s，分割为 {max_audio_length}s 的片段...")
        segments, offsets = _split_audio(audio, audio_path, max_audio_length)

    # Transcribe each segment
    subtitle = Subtitle()
    entry_index = 1

    for i, (seg_path, offset) in enumerate(zip(segments, offsets)):
        print(f"正在转写片段 {i + 1}/{len(segments)}...")
        results = model.transcribe(
            audio=[seg_path],
            language=[language],
            return_time_stamps=True,
        )

        for r in results:
            if not r.time_stamps:
                continue
            for ts in r.time_stamps:
                start = ts.start_time + offset
                end = ts.end_time + offset
                entry = SubtitleEntry(
                    index=entry_index,
                    start_time=seconds_to_srt_time(start),
                    end_time=seconds_to_srt_time(end),
                    text=ts.text.strip(),
                )
                subtitle.add_entry(entry)
                entry_index += 1

        # Clean up temp segment file
        if seg_path != audio_path:
            os.remove(seg_path)

    # Write the .srt
    from src.services.file_handler import FileHandler
    FileHandler.write_srt(subtitle, output_path)
    print(f"字幕已生成：{output_path}")
    return output_path


def _split_audio(audio: AudioSegment, audio_path: str, max_length: int):
    """Split audio into segments of max_length seconds, returning (paths, offsets)."""
    base, ext = os.path.splitext(audio_path)
    segments = []
    offsets = []
    total_ms = len(audio)
    chunk_ms = max_length * 1000

    for start_ms in range(0, total_ms, chunk_ms):
        end_ms = min(start_ms + chunk_ms, total_ms)
        chunk = audio[start_ms:end_ms]
        offset = start_ms / 1000.0
        seg_path = f"{base}_segment_{offset:.0f}{ext}"
        chunk.export(seg_path, format="wav")
        segments.append(seg_path)
        offsets.append(offset)

    return segments, offsets
