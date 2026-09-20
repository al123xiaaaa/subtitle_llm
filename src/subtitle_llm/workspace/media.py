"""产物始终使用启动时的字幕快照；原文重识别只由系统执行。"""
from __future__ import annotations

import copy
import os
import subprocess
import tempfile
from pathlib import Path

from subtitle_llm.io import SubtitleIO, seconds_to_srt_time
from subtitle_llm.media.muxer import mux_subtitle_track
from subtitle_llm.media.transcriber import transcribe
from subtitle_llm.pipeline.task_store import config_from_snapshot
from .artifacts import write_snapshot
from .validation import milliseconds, validate_timing
from .versions import fork_version


def export_video(workspace, task_id: str, output: str, *, video: str | None = None, partial=False, mux=mux_subtitle_track):
    doc = workspace.version(task_id)
    source = video or doc['source_video']
    if not source or not Path(source).is_file():
        raise ValueError('缺少本地视频，请先下载或选择对应视频；已有字幕可继续使用')
    artifact = workspace.begin_artifact(task_id, 'video', partial=partial)
    destination = Path(output).expanduser().resolve()
    if artifact['partial'] and '未完成' not in destination.stem:
        destination = destination.with_name(destination.stem + '.未完成.mkv')
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.subtitle-video-', dir=destination.parent) as temporary:
            subtitle = Path(temporary) / 'snapshot.srt'
            rendered = Path(temporary) / 'result.mkv'
            write_snapshot(artifact['entries'], subtitle, artifact['output_format'])
            mux(source, subtitle, rendered, target_language=doc['language'], ffmpeg=os.getenv('SUBTITLE_LLM_FFMPEG', 'ffmpeg'))
            os.link(rendered, destination)
        return workspace.finish_artifact(task_id, artifact['artifact_id'], path=str(destination))
    except Exception:
        workspace.finish_artifact(task_id, artifact['artifact_id'], error='视频生成失败，字幕仍可使用；可单独重试视频')
        raise


def retranscribe(operations, task_id: str, indices: list[int], *, token_limit: int, max_seconds: int = 120):
    workspace = operations.workspace
    doc = workspace.version(task_id)
    selected = [entry for entry in doc['entries'] if entry['index'] in indices]
    if not selected or len(selected) != len(set(indices)):
        raise ValueError('请选择有效的连续字幕范围')
    positions = [position for position, entry in enumerate(doc['entries']) if entry['index'] in indices]
    if positions != list(range(min(positions), max(positions) + 1)):
        raise ValueError('重新识别需要连续范围')
    start, end = milliseconds(selected[0]['start_time']) / 1000, milliseconds(selected[-1]['end_time']) / 1000
    if not 0 < end - start <= min(max_seconds, 120):
        raise ValueError('单次系统重新识别最多 120 秒，请缩小范围')
    if not doc['source_video'] or not Path(doc['source_video']).is_file():
        raise ValueError('没有可用音视频证据，保留原文疑点；无需用户听音')
    check = next((check for check in reversed(doc['checks']) if not check['outdated'] and set(indices).issubset(check['indices'])), None)
    context = operations._context(doc, check) if check else doc['entries']
    reserve = operations.estimate(task_id, [entry['index'] for entry in context])['estimated_tokens']
    operation = operations._begin(doc, 'retranscribe', indices, token_limit, reserve, False)
    operation['audio_seconds'] = end - start
    used = 0
    network_pending = False
    try:
        config = config_from_snapshot(workspace.tasks.get_task(task_id).config_snapshot_json)
        with tempfile.TemporaryDirectory(prefix='subtitle-retranscribe-') as temporary:
            audio, output = Path(temporary) / 'scope.wav', Path(temporary) / 'source.srt'
            subprocess.run([os.getenv('SUBTITLE_LLM_FFMPEG', 'ffmpeg'), '-nostdin', '-v', 'error', '-ss', str(start),
                '-i', doc['source_video'], '-t', str(end - start), '-vn', '-ac', '1', '-ar', '16000', str(audio)],
                capture_output=True, check=True, timeout=180)
            transcribe(audio, doc['source_language'], output, config.asr)
            recognized = SubtitleIO.read_srt(output).entries
        candidate = []
        for entry in recognized:
            candidate.append({**entry.to_dict(), 'start_time': seconds_to_srt_time(start + milliseconds(entry.start_time) / 1000),
                              'end_time': seconds_to_srt_time(start + milliseconds(entry.end_time) / 1000)})
        operation['candidate'] = candidate
        payload = operations._payload(
            {'full_context': context, 'summary': operations._summary(doc), 'old_source': selected, 'new_source': candidate},
            {'supported': 'Is the new machine transcription a clear, contextually supported correction to old source, without new contradictions or guessed entities? If uncertain return false.',
             'aligned': 'Does the replacement cover exactly the old source time range and preserve the neighboring source continuity?'})
        network_pending = True
        judgments, used = operations._judge(payload)
        network_pending = False
        operation['verification'] = judgments
        if candidate and min(judgments.values()) >= 0.9:
            # 替换范围采用新证据；其他条目按内容和时间精确继承，不能沿用变更部分的旧译文。
            combined = copy.deepcopy(doc['entries'][:positions[0]])
            used_ids = {entry['index'] for entry in doc['entries']}
            next_id = max(used_ids) + 1
            for entry in candidate:
                entry['index'] = next_id
                next_id += 1
                combined.append(entry)
            combined.extend(copy.deepcopy(doc['entries'][positions[-1] + 1:]))
            validate_timing(doc['entries'], combined, {entry['index'] for entry in candidate})
            derived = fork_version(workspace, task_id, source_entries=combined)
            operation['derived_task_id'] = derived['task_id']
            operation['status'] = 'derived'
        else:
            operation['status'] = 'candidate'
            operation['error'] = '新识别证据仍有不确定性，已保留候选；当前版本原文和译文未替换'
    except Exception:
        if network_pending:
            used = None
        operation['status'] = 'candidate' if operation['candidate'] else 'unavailable'
        operation['error'] = '系统重新识别未完成或证据不足，当前版本已保留；无需听音'
    return operations._finish(doc, operation, used)
