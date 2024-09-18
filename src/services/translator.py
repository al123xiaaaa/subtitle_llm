from src.services.file_handler import FileHandler
from src.services.llm_client_factory import LLMClientFactory
from src.services.json_handler import JSONSubtitleHandler
from src.utils.utility_functions import (
    load_yaml_config,
    process_translation,
    chunk_list,
    combine_translations_by_index,
)
from src.services.tui_manager import TUIManager
from src.utils.prompts import (
    GENERATE_SUMMARY_PROMPT,
    TRANSLATE_CHUNK_PROMPT,
    REFINE_TRANSLATION_PROMPT,
    FIX_MISSING_TRANSLATIONS_PROMPT,
    RE_TRANSLATE_PROMPT,
)
from src.models.subtitle_entry import SubtitleEntry

import concurrent.futures
import re
import threading
import sys
import os

# 配置日志
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class InputWithTimeout:
    """辅助类，用于在指定时间内获取用户输入。"""

    def __init__(self, prompt, timeout):
        self.prompt = prompt
        self.timeout = timeout
        self.input = None
        self.input_received = threading.Event()

    def _get_input(self):
        try:
            self.input = input(self.prompt)
            self.input_received.set()
        except EOFError:
            # 处理EOF错误，例如当输入被关闭时
            self.input_received.set()

    def get_input(self):
        thread = threading.Thread(target=self._get_input)
        thread.daemon = True
        thread.start()
        self.input_received.wait(self.timeout)
        if self.input_received.is_set():
            return self.input
        else:
            return None


def translate_subtitles(input_file, output_file, target_language, custom_handling=True):
    config = load_yaml_config()
    client = LLMClientFactory.create_client(config["translation_model"])
    client_summary = LLMClientFactory.create_client(config["summary_model"])

    input_format = input_file.split(".")[-1].lower()

    if input_format == "srt":
        subtitle = FileHandler.read_srt(input_file)
    elif input_format == "json":
        json_handler = JSONSubtitleHandler(
            max_chars=int(config.get("max_chars", 58)),
            max_duration=float(config.get("max_duration", 7.0)),
        )
        subtitle = json_handler.process_json_to_subtitle(input_file)
    else:
        raise ValueError(f"Unsupported input format: {input_format}")

    subtitle_chunks = chunk_list(subtitle.entries, config["chunk_size"])

    subtitle_text_for_summary = "\n".join(
        [
            entry.original_text
            for entry in subtitle.entries
            if len(entry.original_text) >= 10
        ]
    )
    overall_summary, untranslatable_terms = generate_summary_and_terms(
        client_summary,
        config["summary_model"],
        subtitle_text_for_summary,
        target_language,
    )
    context = f"Overall summary: {overall_summary}\nShort Terms: {', '.join(untranslatable_terms)}"

    context = review_context_in_console(context)
    # Save context to a file
    context_file_path = output_file.rsplit(".", 1)[0] + "_context.txt"
    with open(context_file_path, "w", encoding="utf-8") as context_file:
        context_file.write(context)
    logger.info(f"Context saved to: {context_file_path}")

    total_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    translated_entries = []

    # 初始化 TUIManager
    run_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "run_custom_handling.py"
    )
    run_script = os.path.abspath(run_script)
    tui_manager = TUIManager(run_script)

    def process_chunk(chunk):
        local_token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        if not chunk:
            logger.warning("Empty chunk received in process_chunk")
            return [], local_token_usage

        # 初始翻译
        rough_translation = translate_chunk(
            client,
            config["translation_model"],
            chunk,
            context,
            target_language,
            local_token_usage,
        )

        # 处理缺失的翻译
        rough_translation, rough_chunk = handle_missing_translations(
            rough_translation, chunk, local_token_usage
        )
        chunk = rough_chunk
        # logger.info(f"Rough translation: \n{rough_translation}\n")

        # 精炼翻译
        refined_translation = refine_translation(
            client,
            config["translation_model"],
            chunk,
            rough_translation,
            context,
            target_language,
            local_token_usage,
        )

        # 再次处理缺失的翻译
        refined_translation, refined_chunk = handle_missing_translations(
            refined_translation, chunk, local_token_usage
        )
        chunk = refined_chunk
        # logger.info(f"Refined translation: \n{refined_translation}\n")

        # 解析翻译结果
        try:
            chunk_results = parse_translation_results(refined_translation, chunk)
        except Exception as e:
            logger.error(f"Error in parse_translation_results: {e}")
            logger.error(f"Refined translation: {refined_translation}")
            logger.error(f"Chunk: {chunk}")
            raise

        return chunk_results, local_token_usage

    def handle_missing_translations(translation, chunk, local_token_usage):
        if (
            "Translation missing line" in translation
            or "Translated text" in translation
            or any(entry.translated_text.strip() == "" for entry in chunk)
            or any(
                (
                    len(entry.translated_text.strip()) - 2
                )  # 减2是因为翻译前后多了两个字符，[和]
                < 0.13 * len(entry.original_text)
                for entry in chunk
            )  # 或者相对于原句，翻译后的文本长度与原句长度的比例小于10%
        ):
            if custom_handling:
                return handle_custom_translation(translation, chunk, local_token_usage)
            else:
                return handle_default_translation(
                    translation, chunk, local_token_usage
                ), chunk
        return translation, chunk

    def handle_custom_translation(translation, chunk, local_token_usage):
        data = {
            "subtitle_entries": [entry.to_dict() for entry in chunk],
            "target_language": target_language,
            "config": config,
        }
        data_need_to_translate = tui_manager.open_new_terminal(data)
        selected_entries = [
            entry
            for entry in data_need_to_translate.get("selected_subtitle_entries", [])
            if entry.get("needs_retranslation", False)
        ]

        if selected_entries:
            selected_subtitles = [
                SubtitleEntry.from_dict(entry) for entry in selected_entries
            ]
            selected_chunk_results, selected_token_usage = process_chunk(
                selected_subtitles
            )

            for key in local_token_usage:
                local_token_usage[key] += selected_token_usage.get(key, 0)

            new_translation = "\n".join(
                [
                    f"[{i}]\n{entry.translated_text}"
                    for i, entry in enumerate(selected_subtitles, start=1)
                ]
            )
            return new_translation, selected_subtitles
        else:
            logger.info("No entries selected for re-translation.")
            return translation, chunk

    def handle_default_translation(translation, chunk, local_token_usage):
        rough_missing_translation = fix_missing_translations(
            client,
            config["translation_model"],
            chunk,
            translation,
            target_language,
            local_token_usage,
        )
        combined_translation = combine_translations_by_index(
            translation, rough_missing_translation
        )
        return re_translate(
            client,
            config["translation_model"],
            chunk,
            combined_translation,
            target_language,
            local_token_usage,
        )

    def parse_translation_results(translation, chunk):
        translated_lines = translation.strip().split("\n")
        chunk_results = []
        for i in range(len(chunk)):
            translation_line = translated_lines[2 * i + 1].strip()

            try:
                # 将翻译结果与对应的条目配对
                chunk_results.append((chunk[i], translation_line))
            except Exception as e:
                logger.error(f"Unexpected error while parsing translation results: {e}")
                logger.error(f"Translated lines: {translated_lines}")
                raise

        return chunk_results

    # 使用配置中的线程数进行并行处理
    max_workers = config.get("threads", 4)  # 默认 4
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        filtered_chunks = []
        for chunk in subtitle_chunks:
            # 过滤掉非常短的字幕
            filtered_chunk = [
                entry for entry in chunk if len(entry.original_text.strip()) > 4
            ]
            if filtered_chunk:
                filtered_chunks.append(filtered_chunk)
                futures.append(
                    executor.submit(
                        process_chunk,
                        filtered_chunk,
                    )
                )

        for future in concurrent.futures.as_completed(futures):
            try:
                chunk_results, chunk_token_usage = future.result()
                # 累积令牌使用量
                for key in total_token_usage:
                    total_token_usage[key] += chunk_token_usage.get(key, 0)
                for entry, refined_text in chunk_results:
                    entry.set_translated_text(refined_text.strip())
                    translated_entries.append(entry)
            except Exception as e:
                logger.error(f"An error occurred while processing a chunk: {e}")

    # 最后，打印总共使用的令牌数量
    logger.info(f"总共使用的令牌数量: {total_token_usage['total_tokens']}")
    logger.info(f"  提示令牌: {total_token_usage['prompt_tokens']}")
    logger.info(f"  完成令牌: {total_token_usage['completion_tokens']}")

    # 添加非常短的字幕行，不进行翻译
    for entry in subtitle.entries:
        if len(entry.original_text.strip()) <= 4:
            entry.set_translated_text(entry.original_text)
            translated_entries.append(entry)

    # 根据原始顺序对翻译后的条目进行排序
    translated_entries.sort(key=lambda x: x.index)

    subtitle.entries = translated_entries
    FileHandler.write_srt(subtitle, output_file)
    return subtitle


def review_context_in_console(context):
    """Displays the context in the console and allows the user to review and edit it."""
    print("\n===== Context Review =====")
    print(context)
    print("==========================\n")

    while True:
        prompt = "Choose an action: (Y) Proceed, (E) Edit, (A) Abort: "
        input_with_timeout = InputWithTimeout(prompt, 60)
        user_input = input_with_timeout.get_input()

        if user_input is not None:
            user_input = user_input.strip().lower()
            if user_input == "y":
                print("Proceeding with the current context...")
                return context
            elif user_input == "e":
                print(
                    "Enter your edited context. Press Enter on an empty line to finish."
                )
                edited_lines = []
                while True:
                    line = input()
                    if line == "":
                        break
                    edited_lines.append(line)
                edited_context = "\n".join(edited_lines)
                if edited_context.strip() == "":
                    print("No changes made. Keeping the original context.")
                    return context
                else:
                    print("\n===== Edited Context =====")
                    print(edited_context)
                    print("==========================\n")
                    # Confirm the edited context
                    confirm_prompt = "Do you want to use the edited context? (Y/N): "
                    confirm_input = InputWithTimeout(confirm_prompt, 60).get_input()
                    if (
                        confirm_input is not None
                        and confirm_input.strip().lower() == "y"
                    ):
                        return edited_context
                    else:
                        print("Discarding edits. Keeping the original context.")
            elif user_input == "a":
                print("Aborting the translation process as per user request.")
                sys.exit(0)
            else:
                print(
                    "Invalid input. Please enter 'Y' to proceed, 'E' to edit, or 'A' to abort."
                )
        else:
            print("\n60秒已到，自动继续后续流程。")
            return context


def generate_summary_and_terms(client, config, content, target_language):
    prompt = GENERATE_SUMMARY_PROMPT.format(
        target_language=target_language, content=content
    )
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )

    try:
        summary, terms = result["content"].split("短语术语:")
    except ValueError:
        print("Error: Unable to split the result into summary and terms.")
        summary = result["content"]
        terms = ""
    summary = summary.replace("总结:", "").strip()
    terms = [
        term.strip().strip("-") for term in terms.strip().split("\n") if term.strip()
    ]
    return summary, terms


def update_token_usage(token_usage, usage):
    """更新令牌使用量"""
    for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
        if isinstance(usage, dict):
            token_usage[key] += usage.get(key, 0)
        else:
            token_usage[key] += getattr(usage, key, 0)


def translate_chunk(client, config, chunk, context, target_language, token_usage):
    chunk_size = len(chunk)
    chunk_text = "\n".join(
        [f"[{i+1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)]
    )
    prompt = TRANSLATE_CHUNK_PROMPT.format(
        target_language=target_language,
        context=context,
        chunk_text=chunk_text,
        chunk_size=chunk_size,
    )
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )
    translated_text = result["content"]
    update_token_usage(token_usage, result["usage"])
    return process_translation(chunk_text, translated_text, chunk)


def refine_translation(
    client, config, chunk, rough_translation, context, target_language, token_usage
):
    chunk_size = len(chunk)
    original_text = "\n".join(
        [f"[{i+1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)]
    )
    prompt = REFINE_TRANSLATION_PROMPT.format(
        target_language=target_language,
        context=context,
        original_text=original_text,
        rough_translation=rough_translation,
        chunk_size=chunk_size,
    )
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )
    refined_translation = result["content"]
    update_token_usage(token_usage, result["usage"])
    return process_translation(original_text, refined_translation, chunk)


def fix_missing_translations(
    client, config, chunk, processed_lines, target_language, token_usage
):
    chunk_size = len(chunk)
    original_text = "\n".join(
        [f"[{i+1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)]
    )
    missing_lines = {}

    # Split the processed_lines string into a list
    processed_lines_list = processed_lines.split("\n")

    for i in range(0, len(processed_lines_list), 2):
        if i + 1 < len(processed_lines_list):
            # Extract index number
            index = processed_lines_list[i].strip("[]")
            # Check if the line contains 'Translation missing line'
            if "Translation missing line" in processed_lines_list[i + 1]:
                missing_lines[index] = processed_lines_list[i + 1].strip("[]")

    # If no missing lines are found, return the original processed_lines
    if not missing_lines:
        return processed_lines

    is_single_missing_line = len(missing_lines) == 1
    if is_single_missing_line:
        missing_index = next(iter(missing_lines))
        example_format = f"""Example of the required format:
[{missing_index}]
[Translated text for entry {missing_index}]
"""
    else:
        example_format = f"""Example of the required format(index from [{min(missing_lines)}] to [{max(missing_lines)}]):
[{min(missing_lines)}]
[Translated text for entry {min(missing_lines)}]
...
[{max(missing_lines)}]
[Translated text for entry {max(missing_lines)}]
"""

    # Precompute the joined missing lines
    missing_lines_formatted = "\n".join(
        [f"[{index}]\n[{line}]" for index, line in missing_lines.items()]
    )

    # Construct the prompt using the precomputed string
    prompt = FIX_MISSING_TRANSLATIONS_PROMPT.format(
        target_language=target_language,
        original_text=original_text,
        missing_lines_indices=", ".join([str(index) for index in missing_lines]),
        missing_lines_formatted=missing_lines_formatted,
        example_format=example_format,
    )
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )
    fixed_translation = result["content"]
    update_token_usage(token_usage, result["usage"])
    return fixed_translation


def re_translate(client, config, chunk, translation, target_language, token_usage):
    chunk_size = len(chunk)
    original_text = "\n".join(
        [f"[{i+1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)]
    )
    prompt = RE_TRANSLATE_PROMPT.format(
        target_language=target_language,
        original_text=original_text,
        translation=translation,
        chunk_size=chunk_size,
    )
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )
    fixed_translation = (
        re.search(r"<translation>(.*?)</translation>", result["content"], re.DOTALL)
        .group(1)
        .strip()
    )
    update_token_usage(token_usage, result["usage"])
    return process_translation(original_text, fixed_translation, chunk)
