from src.services.file_handler import FileHandler
from src.services.factories.llm_client_factory import LLMClientFactory
from src.services.json_handler import JSONSubtitleHandler
from src.utils.utility_functions import (
    load_yaml_config,
    process_translation,
    chunk_list,
    combine_translations_by_index,
)
from src.services.tui.tui_manager import TUIManager
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

IGNORE_SUBTITLE_LENGTH = 4


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
    tui_manager = TUIManager(run_script) # Initialize TUIManager once

    # process_chunk now needs more parameters because it's called directly
    def process_chunk(chunk, subtitle_entries, tui_manager_instance, current_config, current_client, current_target_language, current_context):
        try:
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
                current_client,
                current_config["translation_model"],
                chunk,
                current_context,
                current_target_language,
                local_token_usage,
            )

            # 精炼翻译
            refined_translation = refine_translation(
                current_client,
                current_config["translation_model"],
                chunk,
                rough_translation,
                current_context,
                current_target_language,
                local_token_usage,
            )

            # 处理缺失的翻译
            # Pass tui_manager and other necessary params
            refined_translation, chunk = handle_missing_translations(
                refined_translation, chunk, local_token_usage, subtitle_entries,
                tui_manager_instance, current_config, current_client, current_target_language, current_context
            )

            # 解析翻译结果
            chunk_results = parse_translation_results(refined_translation, chunk)

            return chunk_results, local_token_usage
        except Exception as e:
            logger.error(f"Error in process_chunk: {e}")
            logger.error(f"Chunk: {chunk}")
            raise

    def handle_missing_translations( # Added params
        translation, chunk, local_token_usage, subtitle_entries,
        tui_manager_instance, current_config, current_client, current_target_language, current_context
    ):
        needs_retranslation = False
        for i, entry in enumerate(chunk):
            if (
                entry.translated_text.strip() == ""
                or "Translation missing line" in entry.translated_text
                or "Translated text" in entry.translated_text
                or "翻译缺失" in entry.translated_text
                or (
                    len(entry.translated_text.strip()) - 2
                    < 0.1 * len(entry.original_text)
                    and len(entry.original_text) > 26
                )
                # 或者翻译结果仅为标点符号(全角或半角)
                or all(
                    char in "，。？！：；“”、" for char in entry.translated_text
                )  # 判断是否全为中文标点
                or all(
                    char in ",.?!:;\"'()-" for char in entry.translated_text
                )  # 判断是否全为英文标点
            ):
                needs_retranslation = True

            if (
                needs_retranslation
            ):  # 从第一条被认为需要重新翻译的条目开始，后续的都需要重新翻译
                entry.needs_retranslation = True

        if any(entry.needs_retranslation for entry in chunk):
            if custom_handling:
                # Pass tui_manager and other necessary params
                return handle_custom_translation(
                    translation, chunk, local_token_usage, subtitle_entries,
                    tui_manager_instance, current_config, current_client, current_target_language, current_context
                )
            else:
                # Pass client, config, target_language to handle_default_translation
                return handle_default_translation(
                    translation, chunk, local_token_usage, current_client, current_config, current_target_language
                ), chunk
        return translation, chunk

    # Added tui_manager_instance, current_config, current_client, current_target_language, current_context
    def handle_custom_translation(
        translation, chunk, local_token_usage, subtitle_entries,
        tui_manager_instance, current_config, current_client, current_target_language, current_context
    ):
        try:
            # TUIManager.open_new_terminal is now async and just enqueues.
            # It returns a status, not the processed data.
            tui_manager_instance.open_new_terminal([entry.to_dict() for entry in chunk])
            
            # Polling logic to get results from the temporary file
            processed_data_from_tui = None
            while True:
                try:
                    with open(tui_manager_instance.tmpfile_path, "r", encoding="utf-8") as f:
                        json_data = json.load(f)
                    if json_data.get("tui_completed", False):
                        # Check if this completed data corresponds to the current chunk.
                        # This is tricky if multiple chunks are rapidly enqueued.
                        # For now, assume sequential processing ensures the file content is for the latest TUI interaction.
                        # A robust solution might involve chunk identifiers in the JSON.
                        # For this iteration, we assume the TUI processes one by one and updates the file accordingly.
                        processed_data_from_tui = json_data
                        logger.info(f"TUI completed processing chunk. Data retrieved from tmpfile: {tui_manager_instance.tmpfile_path}")
                        break
                except json.JSONDecodeError:
                    logger.debug(f"JSON decode error reading {tui_manager_instance.tmpfile_path}, TUI might be writing.")
                except FileNotFoundError:
                    logger.error(f"Temporary file {tui_manager_instance.tmpfile_path} not found. TUI may have failed or file was deleted.")
                    # This is a critical error, should probably stop or return an error state
                    raise # Or handle more gracefully
                except Exception as e:
                    logger.error(f"Error reading TUI status file {tui_manager_instance.tmpfile_path}: {e}")
                
                logger.debug(f"Waiting for TUI to complete chunk... Polling {tui_manager_instance.tmpfile_path}")
                time.sleep(0.5) # Polling interval

            if not processed_data_from_tui:
                logger.error("Failed to retrieve processed data from TUI.")
                return translation, chunk # Or raise an exception

            selected_entries_dicts = processed_data_from_tui["selected_subtitle_entries"]
            merge_map = processed_data_from_tui.get("merge_map", [])

            # 创建一个基于索引的字幕条目映射
            index_to_entry = {entry.index: entry for entry in chunk}

            if all(
                not entry_dict.get("needs_retranslation", True)
                for entry_dict in selected_entries_dicts
            ):
                logger.info("All translations accepted. Skipping re-translation.")
                return translation, chunk

            if selected_entries_dicts:
                try:
                    # 应用合并操作到 chunk 和 subtitle_entries
                    for merge_op in merge_map:
                        merged_index = merge_op["merged_index"]
                        merged_from_indices = merge_op["merged_from_indices"]

                        # 确保所有索引都存在
                        if merged_index not in index_to_entry:
                            logger.error(
                                f"Merged index {merged_index} not found in chunk."
                            )
                            continue
                        missing_indices = [
                            idx
                            for idx in merged_from_indices
                            if idx not in index_to_entry
                        ]
                        if missing_indices:
                            logger.error(
                                f"Merged from indices {missing_indices} not found in chunk."
                            )
                            continue

                        # 创建合并后的条目
                        merged_text = " ".join(
                            index_to_entry[idx].original_text
                            for idx in merged_from_indices
                        )
                        merged_start_time = index_to_entry[
                            merged_from_indices[0]
                        ].start_time
                        merged_end_time = index_to_entry[
                            merged_from_indices[-1]
                        ].end_time

                        merged_entry = SubtitleEntry(
                            index=merged_index,
                            start_time=merged_start_time,
                            end_time=merged_end_time,
                            text=merged_text,
                        )
                        index_to_entry[merged_index] = merged_entry

                        # 从 chunk 和 index_to_entry 中删除被合并的条目
                        for idx in merged_from_indices[1:]:
                            if idx in index_to_entry:
                                del index_to_entry[idx]
                            for entry in chunk:
                                if entry.index == idx:
                                    chunk.remove(entry)
                                    break

                        # 替换 merged_index 的条目
                        for i, entry in enumerate(subtitle_entries):
                            if entry.index == merged_index:
                                subtitle_entries[i] = merged_entry
                                break
                        # 删除被合并的条目
                        for idx in merged_from_indices[1:]:
                            subtitle_entries[:] = [
                                entry
                                for entry in subtitle_entries
                                if entry.index != idx
                            ]

                    # 更新 chunk 列表
                    chunk = list(index_to_entry.values())

                    # 处理选中的需要重新翻译的条目
                    selected_entries = [
                        SubtitleEntry.from_dict(entry_dict).set_needs_retranslation(
                            False
                        )
                        for entry_dict in selected_entries_dicts
                        if entry_dict.get("needs_retranslation", False)
                    ]

                    # 递归调用 process_chunk 处理选中的条目
                    # Pass all required parameters for the recursive call
                    selected_chunk_results, selected_token_usage = process_chunk(
                        selected_entries, subtitle_entries,
                        tui_manager_instance, current_config, current_client, current_target_language, current_context
                    )

                    deeper_chunk = [entry for entry, _ in selected_chunk_results]
                    deeper_chunk_first_index = deeper_chunk[0].index
                    chunk = (
                        [
                            entry
                            for entry in chunk
                            if entry.index < deeper_chunk_first_index
                        ]
                        + deeper_chunk
                    )

                    # 更新令牌使用量
                    for key in local_token_usage:
                        local_token_usage[key] += selected_token_usage.get(key, 0)

                    # 更新翻译结果
                    updated_translations = {
                        entry.index: translated_text
                        for entry, translated_text in selected_chunk_results
                    }
                    for entry in chunk:
                        if entry.index in updated_translations:
                            entry.translated_text = updated_translations[entry.index]

                    # 合并翻译文本
                    merged_translation = ""
                    for i, chunk_entry in enumerate(chunk):
                        merged_translation += (
                            f"[{i + 1}]\n{chunk_entry.translated_text}\n"
                        )

                    return merged_translation, chunk
                except Exception as e:
                    logger.error(f"Error processing selected entries: {e}")
                    return translation, chunk
            else:
                logger.info("No entries selected for re-translation.")
                return translation, chunk
        except Exception as e:
            logger.error(f"Error in handle_custom_translation: {e}")
            # Ensure tmpfile_path is available on tui_manager_instance for error reporting if needed
            if hasattr(tui_manager_instance, 'tmpfile_path'):
                logger.error(f"Error occurred while TUI was interacting with {tui_manager_instance.tmpfile_path}")
            return translation, chunk

    # Added current_client, current_config, current_target_language
    def handle_default_translation(translation, chunk, local_token_usage, current_client, current_config, current_target_language):
        rough_missing_translation = fix_missing_translations(
            current_client,
            current_config["translation_model"],
            chunk,
            translation,
            current_target_language,
            local_token_usage,
        )
        combined_translation = combine_translations_by_index(
            translation, rough_missing_translation
        )
        return re_translate(
            current_client,
            current_config["translation_model"],
            chunk,
            combined_translation,
            current_target_language,
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

    # Simplified to always process chunks sequentially as per Option A.
    # The ThreadPoolExecutor and the conditional custom_handling logic for parallelism are removed.
    logger.info("Processing all subtitle chunks sequentially.")
    for chunk_item in subtitle_chunks: # Use a different variable name for clarity
        filtered_chunk = [
            entry
            for entry in chunk_item # Iterate over items in the current chunk_item
            if len(entry.original_text.strip()) > IGNORE_SUBTITLE_LENGTH
        ]
        if filtered_chunk:
            try:
                # Call process_chunk with all necessary parameters including the single tui_manager instance
                chunk_results, chunk_token_usage = process_chunk(
                    filtered_chunk,
                    subtitle.entries,
                    tui_manager, 
                    config,
                    client,
                    target_language,
                    context
                )
                # Accumulate token usage and results
                for key in total_token_usage:
                    total_token_usage[key] += chunk_token_usage.get(key, 0)
                for entry, refined_text in chunk_results:
                    entry.set_translated_text(refined_text.strip())
                    translated_entries.append(entry)
            except Exception as e:
                logger.error(f"An error occurred while processing a chunk sequentially: {e}")

    # Call shutdown on tui_manager after all chunks are processed.
    # This is done regardless of custom_handling, as tui_manager is always initialized.
    tui_manager.shutdown()

    # 最后，打印总共使用的令牌数量
    logger.info(f"总共使用的令牌数量: {total_token_usage['total_tokens']}")
    logger.info(f"  提示令牌: {total_token_usage['prompt_tokens']}")
    logger.info(f"  完成令牌: {total_token_usage['completion_tokens']}")

    # 对于非常短的字幕行，由于之前没有翻译，所以这里增添进去
    for entry in subtitle.entries:
        if len(entry.original_text.strip()) <= IGNORE_SUBTITLE_LENGTH:
            entry.set_translated_text(entry.original_text.strip())
            translated_entries.append(entry)

    # 对于翻译后的字幕，前后有[]的，去掉。[]可能有多个，就像[[xxx]]，要全部去掉。
    # for entry in translated_entries:
    #     entry.set_translated_text(
    #         re.sub(r"^\[+|\]+$", "", entry.translated_text.strip())
    #     )

    # 根据原始顺序对翻译后的条目进行排序
    translated_entries.sort(key=lambda x: x.index)

    subtitle.entries = translated_entries
    subtitle.reorder_entries()
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
