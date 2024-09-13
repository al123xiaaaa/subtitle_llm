import json
from src.models.transcript import Word, Segment, Transcript
from src.models.subtitle import Subtitle
from src.models.subtitle_entry import SubtitleEntry
from src.utils.utility_functions import seconds_to_srt_time, count_words


class JSONSubtitleHandler:
    def __init__(self, max_chars: int = 122, max_duration: float = 7.0):
        self.max_chars = max_chars
        self.max_duration = max_duration
        self.split_punctuations = {".", "!", "?"}

    def read_json(self, file_path: str) -> Transcript:
        """
        读取词级 JSON 字幕文件并解析为 Transcript 对象。
        """
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        segments = []
        for seg in data.get("segments", []):
            words = []
            prev_word = None
            for w in seg.get("words", []):
                if 'start' not in w or 'end' not in w or 'score' not in w:
                    if prev_word:
                        w['start'] = prev_word.start + 0.3
                        w['end'] = prev_word.end + 0.5
                        w['score'] = prev_word.score
                    else:
                        # If it's the first word and missing attributes, use segment start/end
                        w['start'] = seg.get("start", 0.0)
                        w['end'] = seg.get("end", 0.0)
                        w['score'] = 0.0  # Default score

                word = Word(**w)
                words.append(word)
                prev_word = word

            segment = Segment(
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                text=seg.get("text", ""),
                words=words,
            )
            segments.append(segment)

        return Transcript(segments=segments)

    def split_transcript(self, transcript: Transcript) -> Subtitle:
        """
        将 Transcript 对象分割为合理长度的 Subtitle 对象。
        """
        subtitles = []
        buffer_words = []
        buffer_start = None
        buffer_end = None

        for segment in transcript.segments:
            for word in segment.words:
                if not buffer_words:
                    buffer_start = word.start
                buffer_words.append(word)
                buffer_end = word.end

                # 构建当前缓冲区的文本
                buffer_text = " ".join([w.word for w in buffer_words]).strip()
                buffer_length = len(buffer_text)
                buffer_duration = buffer_end - buffer_start

                # 检查是否以标点符号结尾
                is_end_punctuation = buffer_words[-1].word.endswith(
                    tuple(self.split_punctuations)
                )
                exceeds_chars = buffer_length > self.max_chars
                exceeds_duration = buffer_duration > self.max_duration

                if exceeds_chars or exceeds_duration or is_end_punctuation:
                    # 确定分割点
                    if is_end_punctuation:
                        split_index = len(buffer_words)
                    else:
                        split_index = None
                        for i in range(len(buffer_words) - 1, -1, -1):
                            if buffer_words[i].word.endswith(
                                tuple(self.split_punctuations)
                            ):
                                split_index = i + 1
                                break
                        if split_index is None:
                            if exceeds_chars or exceeds_duration:
                                split_index = len(buffer_words) - 1
                            else:
                                split_index = len(buffer_words)

                    # 提取分割后的单词
                    sub_words = buffer_words[:split_index]
                    sub_text = " ".join([w.word for w in sub_words]).strip()
                    sub_start = buffer_start
                    sub_end = sub_words[-1].end if sub_words else word.end

                    # 创建 SubtitleEntry 并添加到 Subtitle 对象
                    entry = SubtitleEntry(
                        index=len(subtitles) + 1,
                        start_time=seconds_to_srt_time(sub_start),
                        end_time=seconds_to_srt_time(sub_end),
                        text=sub_text,
                    )
                    subtitles.append(entry)

                    # 重置缓冲区
                    buffer_words = buffer_words[split_index:]
                    buffer_start = buffer_words[0].start if buffer_words else None
                    buffer_end = buffer_words[-1].end if buffer_words else None

        # 处理剩余的缓冲区
        if buffer_words:
            sub_text = " ".join([w.word for w in buffer_words]).strip()
            sub_start = buffer_start
            sub_end = buffer_end
            entry = SubtitleEntry(
                index=len(subtitles) + 1,
                start_time=seconds_to_srt_time(sub_start),
                end_time=seconds_to_srt_time(sub_end),
                text=sub_text,
            )
            subtitles.append(entry)

        # 创建 Subtitle 对象
        subtitle = Subtitle()
        for entry in subtitles:
            subtitle.add_entry(entry)

        # **优化步骤**：合并包含 <=3 个单词的字幕行到前一行
        self.merge_short_subtitles(subtitle)

        return subtitle

    def merge_short_subtitles(self, subtitle: Subtitle, max_words: int = 3):
        """
        合并包含 <= max_words 个单词的字幕行到前一行。
        """
        optimized_entries = []
        for entry in subtitle.entries:
            word_count = count_words(entry.original_text)
            if word_count <= max_words:
                if optimized_entries:
                    # 合并到前一行
                    previous_entry = optimized_entries[-1]
                    previous_entry.original_text += " " + entry.original_text
                    previous_entry.end_time = entry.end_time
                else:
                    # 如果是第一行，无法合并，直接添加
                    optimized_entries.append(entry)
            else:
                optimized_entries.append(entry)

        # 更新 Subtitle 对象
        subtitle.entries = optimized_entries

    def process_json_to_subtitle(self, file_path: str) -> Subtitle:
        """
        从 JSON 文件读取并处理，返回 Subtitle 对象。
        """
        transcript = self.read_json(file_path)
        subtitle = self.split_transcript(transcript)
        return subtitle
