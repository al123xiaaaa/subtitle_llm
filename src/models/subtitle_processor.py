from src.models.transcript import Transcript
from src.models.subtitle import Subtitle
from src.models.subtitle_entry import SubtitleEntry
from src.utils.utility_functions import seconds_to_srt_time, count_words


class SubtitleProcessor:
    def __init__(self, max_chars: int = 122, max_duration: float = 7.0):
        self.max_chars = max_chars
        self.max_duration = max_duration
        self.split_punctuations = {".", "!", "?"}

    def transcript_to_subtitle(self, transcript: Transcript) -> Subtitle:
        """
        Convert Transcript object to Subtitle object with reasonable subtitle lengths,
        prioritizing natural sentence boundaries and ensuring no subtitle has <=3 words.

        :param transcript: Transcript object containing segments and words
        :return: Subtitle object with processed entries
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

                # Build current buffer text
                buffer_text = " ".join([w.word for w in buffer_words]).strip()
                buffer_length = len(buffer_text)
                buffer_duration = buffer_end - buffer_start

                # Check if buffer exceeds limits or ends with punctuation
                is_end_punctuation = buffer_words[-1].word.endswith(
                    tuple(self.split_punctuations)
                )
                exceeds_chars = buffer_length > self.max_chars
                exceeds_duration = buffer_duration > self.max_duration

                if exceeds_chars or exceeds_duration or is_end_punctuation:
                    # Determine split point
                    if is_end_punctuation:
                        split_index = len(buffer_words)
                    else:
                        # Find last punctuation in buffer to split
                        split_index = None
                        for i in range(len(buffer_words) - 1, -1, -1):
                            if buffer_words[i].word.endswith(
                                tuple(self.split_punctuations)
                            ):
                                split_index = i + 1
                                break
                        if split_index is None:
                            # No punctuation found, split at the last possible word that doesn't exceed limits
                            if exceeds_chars or exceeds_duration:
                                split_index = len(buffer_words) - 1
                            else:
                                split_index = len(buffer_words)

                    # Finalize subtitle
                    sub_words = buffer_words[:split_index]
                    sub_text = " ".join([w.word for w in sub_words]).strip()
                    sub_start = buffer_start
                    sub_end = sub_words[-1].end if sub_words else word.end

                    # Create SubtitleEntry and add to subtitles
                    subtitle_entry = SubtitleEntry(
                        index=len(subtitles) + 1,
                        start_time=seconds_to_srt_time(sub_start),
                        end_time=seconds_to_srt_time(sub_end),
                        text=sub_text,
                    )
                    subtitles.append(subtitle_entry)

                    # Reset buffer with remaining words
                    buffer_words = buffer_words[split_index:]
                    buffer_start = buffer_words[0].start if buffer_words else None
                    buffer_end = buffer_words[-1].end if buffer_words else None

        # Add remaining buffer
        if buffer_words:
            sub_text = " ".join([w.word for w in buffer_words]).strip()
            sub_start = buffer_start
            sub_end = buffer_end
            subtitle_entry = SubtitleEntry(
                index=len(subtitles) + 1,
                start_time=seconds_to_srt_time(sub_start),
                end_time=seconds_to_srt_time(sub_end),
                text=sub_text,
            )
            subtitles.append(subtitle_entry)

        # **优化步骤**：合并包含 <=3 个单词的字幕行到前一行
        optimized_subtitles = []
        for sub in subtitles:
            word_count = count_words(sub.text)
            if word_count <= 3:
                if optimized_subtitles:
                    # 合并到前一行
                    optimized_subtitles[-1].text += " " + sub.text
                    optimized_subtitles[-1].end_time = sub.end_time
                else:
                    # 如果是第一行，无法合并，直接添加
                    optimized_subtitles.append(sub)
            else:
                optimized_subtitles.append(sub)

        # 更新索引
        for idx, sub in enumerate(optimized_subtitles, 1):
            sub.index = idx

        # 创建 Subtitle 对象
        final_subtitle = Subtitle()
        for sub in optimized_subtitles:
            final_subtitle.add_entry(sub)

        return final_subtitle
