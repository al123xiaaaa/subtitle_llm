import os
os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"

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

from rich.logging import RichHandler
from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    SpinnerColumn,
)

console = Console()

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[RichHandler(console=console, show_time=True, show_path=False, markup=True)],
)
# Suppress noisy third-party loggers
for _name in ("grpc", "google", "google_generativeai", "urllib3", "filelock"):
    logging.getLogger(_name).setLevel(logging.WARNING)
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
    tui_manager = TUIManager(run_script)

    def translate_and_refine(chunk):
        """Phase 1: 粗翻译 + 精翻译（纯并行，不涉及 TUI）"""
        local_token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        if not chunk:
            return chunk, "", local_token_usage

        logger.debug(f"Translating chunk ({len(chunk)} entries)...")
        rough_translation = translate_chunk(
            client, config["translation_model"], chunk,
            context, target_language, local_token_usage,
        )
        refined_translation = refine_translation(
            client, config["translation_model"], chunk,
            rough_translation, context, target_language, local_token_usage,
        )
        logger.debug(f"Chunk translation done ({len(chunk)} entries)")
        return chunk, refined_translation, local_token_usage

    def check_missing_translations(chunk):
        """检测哪些条目需要重新翻译，返回是否需要处理"""
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
                or (
                    len(entry.translated_text.strip()) < 0.19 * len(entry.original_text)
                    and len(entry.original_text) > 100
                )
                or (
                    len(entry.translated_text.strip()) > 0.65 * len(entry.original_text)
                    and len(entry.original_text) > 26
                )
                or all(
                    char in "，。？！：；""、" for char in entry.translated_text
                )
                or all(
                    char in ",.?!:;\"'()-" for char in entry.translated_text
                )
            ):
                needs_retranslation = True

            if needs_retranslation:
                entry.needs_retranslation = True

        return any(entry.needs_retranslation for entry in chunk)

    def handle_custom_translation(translation, chunk, local_token_usage, subtitle_entries, chunk_idx=0, total=1):
        try:
            data = tui_manager.submit_chunk(
                [entry.to_dict() for entry in chunk],
                chunk_index=chunk_idx,
                total_chunks=total,
            )
            if data is None:
                logger.error("TUI returned None (temp file lost). Skipping.")
                return translation, chunk
            selected_entries_dicts = data.get("selected_subtitle_entries", [])
            merge_map = data.get("merge_map", [])
            needs_retranslate = [d for d in selected_entries_dicts if d.get("needs_retranslation", False)]
            logger.info(f"TUI 返回 {len(selected_entries_dicts)} 条，其中 {len(needs_retranslate)} 条需要重新翻译")

            index_to_entry = {entry.index: entry for entry in chunk}

            if not needs_retranslate:
                logger.info("All translations accepted. Skipping re-translation.")
                return translation, chunk

            if selected_entries_dicts:
                try:
                    for merge_op in merge_map:
                        merged_index = merge_op["merged_index"]
                        merged_from_indices = merge_op["merged_from_indices"]

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

                        for idx in merged_from_indices[1:]:
                            if idx in index_to_entry:
                                del index_to_entry[idx]
                            for entry in chunk:
                                if entry.index == idx:
                                    chunk.remove(entry)
                                    break

                        for i, entry in enumerate(subtitle_entries):
                            if entry.index == merged_index:
                                subtitle_entries[i] = merged_entry
                                break
                        for idx in merged_from_indices[1:]:
                            subtitle_entries[:] = [
                                entry
                                for entry in subtitle_entries
                                if entry.index != idx
                            ]

                    chunk = list(index_to_entry.values())

                    selected_entries = [
                        SubtitleEntry.from_dict(entry_dict).set_needs_retranslation(
                            False
                        )
                        for entry_dict in selected_entries_dicts
                        if entry_dict.get("needs_retranslation", False)
                    ]

                    # 递归：翻译选中的条目
                    selected_chunk, selected_refined, selected_token_usage = translate_and_refine(
                        selected_entries
                    )
                    for key in local_token_usage:
                        local_token_usage[key] += selected_token_usage.get(key, 0)

                    # 解析递归翻译结果
                    selected_chunk_results = parse_translation_results(
                        selected_refined, selected_chunk
                    )

                    deeper_chunk_first_index = selected_chunk[0].index
                    chunk = (
                        [
                            entry
                            for entry in chunk
                            if entry.index < deeper_chunk_first_index
                        ]
                        + selected_chunk
                    )

                    for entry, text in selected_chunk_results:
                        entry.set_translated_text(text.strip())

                    merged_translation = ""
                    for i, chunk_entry in enumerate(chunk):
                        merged_translation += (
                            f"[{i + 1}]\n{chunk_entry.translated_text}\n"
                        )

                    return merged_translation, chunk
                except Exception as e:
                    import traceback
                    logger.error(f"Error processing selected entries: {e}")
                    traceback.print_exc()
                    return translation, chunk
            else:
                logger.info("No entries selected for re-translation.")
                return translation, chunk
        except Exception as e:
            import traceback
            logger.error(f"Error in handle_custom_translation: {e}")
            traceback.print_exc()
            return translation, chunk

    def handle_default_translation(translation, chunk, local_token_usage):
        rough_missing_translation = fix_missing_translations(
            client, config["translation_model"], chunk,
            translation, target_language, local_token_usage,
        )
        combined_translation = combine_translations_by_index(
            translation, rough_missing_translation
        )
        return re_translate(
            client, config["translation_model"], chunk,
            combined_translation, target_language, local_token_usage,
        )

    def parse_translation_results(translation, chunk):
        translated_lines = translation.strip().split("\n")
        expected_lines = len(chunk) * 2
        if len(translated_lines) < expected_lines:
            logger.warning(
                f"LLM 返回行数({len(translated_lines)})少于预期({expected_lines})，"
                f"chunk 共 {len(chunk)} 条"
            )
        chunk_results = []
        for i in range(len(chunk)):
            line_idx = 2 * i + 1
            if line_idx < len(translated_lines):
                translation_line = translated_lines[line_idx].strip()
            else:
                translation_line = ""
                logger.warning(f"第 {i+1} 条翻译缺失，使用空字符串")
            chunk_results.append((chunk[i], translation_line))
        return chunk_results

    # ===== 并行翻译 + 即时 TUI 处理 =====
    max_workers = config.get("threads", 4)

    filtered_chunks = []
    for chunk in subtitle_chunks:
        filtered_chunk = [
            entry
            for entry in chunk
            if len(entry.original_text.strip()) > IGNORE_SUBTITLE_LENGTH
        ]
        if filtered_chunk:
            filtered_chunks.append(filtered_chunk)

    logger.info(f"开始翻译：共 {len(filtered_chunks)} 个 chunk，{len(subtitle.entries)} 条字幕，{max_workers} 线程并行")

    pending_tui = []
    done_futures = set()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("翻译中", total=len(filtered_chunks))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = {
                executor.submit(translate_and_refine, fc): (fc, idx)
                for idx, fc in enumerate(filtered_chunks)
            }
            all_futures = set(future_to_chunk.keys())

            while len(done_futures) < len(all_futures) or pending_tui:
                # 等待至少一个翻译完成
                if len(done_futures) < len(all_futures):
                    newly_done, _ = concurrent.futures.wait(
                        all_futures - done_futures,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in newly_done:
                        done_futures.add(future)
                        try:
                            chunk, refined_translation, chunk_token_usage = future.result()
                            fc, fc_idx = future_to_chunk[future]
                            total_chunks = len(filtered_chunks)
                            for key in total_token_usage:
                                total_token_usage[key] += chunk_token_usage.get(key, 0)

                            chunk_results = parse_translation_results(refined_translation, chunk)
                            has_missing = check_missing_translations(chunk)

                            if has_missing and not custom_handling:
                                fixed_translation, chunk = handle_default_translation(
                                    refined_translation, chunk, chunk_token_usage
                                )
                                chunk_results = parse_translation_results(fixed_translation, chunk)
                                for entry, refined_text in chunk_results:
                                    entry.set_translated_text(refined_text.strip())
                                    translated_entries.append(entry)
                                progress.advance(task_id, 1)
                            elif has_missing and custom_handling:
                                pending_tui.append((chunk, refined_translation, chunk_token_usage, fc_idx, total_chunks))
                            else:
                                for entry, refined_text in chunk_results:
                                    entry.set_translated_text(refined_text.strip())
                                    translated_entries.append(entry)
                                progress.advance(task_id, 1)
                        except Exception as e:
                            logger.error(f"An error occurred while processing a chunk: {e}")
                            progress.advance(task_id, 1)

                # 有待处理的 TUI 就立即弹出（其他翻译在后台继续）
                if pending_tui:
                    chunk, translation, chunk_token_usage, fc_idx, total_chunks = pending_tui.pop(0)
                    progress.update(task_id, description=f"TUI 审核 Chunk {fc_idx + 1}/{total_chunks}")
                    try:
                        fixed_translation, chunk = handle_custom_translation(
                            translation, chunk, chunk_token_usage, subtitle.entries,
                            chunk_idx=fc_idx, total=total_chunks,
                        )
                        logger.debug(f"TUI 审核完成 Chunk {fc_idx + 1}，共 {len(chunk)} 条")
                        chunk_results = parse_translation_results(fixed_translation, chunk)
                        for entry, refined_text in chunk_results:
                            entry.set_translated_text(refined_text.strip())
                            translated_entries.append(entry)
                    except Exception as e:
                        import traceback
                        logger.error(f"An error occurred while processing a chunk in TUI: {e}")
                        traceback.print_exc()
                    progress.update(task_id, description="翻译中")
                    progress.advance(task_id, 1)

    # 所有处理完成，关闭 TUI Worker
    if custom_handling:
        tui_manager.stop()

    logger.info(f"翻译完成：共处理 {len(translated_entries)} 条，"
                f"消耗 token {total_token_usage['total_tokens']} "
                f"(prompt={total_token_usage['prompt_tokens']}, completion={total_token_usage['completion_tokens']})")

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
