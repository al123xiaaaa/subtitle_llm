from src.services.file_handler import FileHandler
from src.services.llm_client_factory import LLMClientFactory
from src.services.json_handler import JSONSubtitleHandler  # 新增导入
from src.utils.utility_functions import (
    load_yaml_config,
    process_translation,
    chunk_list,
)
import concurrent.futures
import re


def translate_subtitles(input_file, output_file, target_language):
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
        client_summary, config["summary_model"], subtitle_text_for_summary
    )
    context = f"Overall summary: {overall_summary}\nShort Terms to keep unchanged: {', '.join(untranslatable_terms)}"
    print(f"Context: {context}")
    
    # Save context to a file
    context_file_path = output_file.rsplit('.', 1)[0] + '_context.txt'
    with open(context_file_path, 'w', encoding='utf-8') as context_file:
        context_file.write(context)
    print(f"Context saved to: {context_file_path}")

    total_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    translated_entries = []

    def process_chunk(chunk, chunk_index, total_chunks):
        print(f"Processing chunk {chunk_index}/{total_chunks}")
        local_token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        rough_translation = translate_chunk(
            client,
            config["translation_model"],
            chunk,
            context,
            target_language,
            local_token_usage,
        )
        print(f"Rough translation: \n{rough_translation}\n")
        refined_translation = refine_translation(
            client,
            config["translation_model"],
            chunk,
            rough_translation,
            context,
            target_language,
            local_token_usage,
        )
        print(f"Refined translation: \n{refined_translation}\n")

        # 检查并修复缺失的翻译行
        if (
            "Translation missing line" in refined_translation
            or "Translated text" in refined_translation
        ):
            refined_translation = fix_missing_translations(
                client,
                config["translation_model"],
                chunk,
                refined_translation,
                target_language,
                local_token_usage,
            )
            print(f"Fixed translation: \n{refined_translation}\n")

        # Parse the refined_translation with indices
        translated_lines = refined_translation.strip().split("\n")
        if len(translated_lines) != 2 * len(chunk):
            raise ValueError("Mismatch between number of indices and translations.")

        chunk_results = []
        for i in range(0, len(translated_lines), 2):
            index_line = translated_lines[i].strip()
            translation_line = translated_lines[i + 1].strip()

            # Extract index number
            match = re.match(r"\[(\d+)\]", index_line)
            if not match:
                raise ValueError(f"Invalid index format: {index_line}")
            index = int(match.group(1))

            # Map to the corresponding SubtitleEntry
            entry = chunk[index - 1]  # Assuming chunk is 0-indexed
            chunk_results.append((entry, translation_line))

        return chunk_results, local_token_usage

    # 使用配置中的线程数进行并行处理
    max_workers = config["threads"]  # 默认 40
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
                        len(filtered_chunks),
                        len(subtitle_chunks),
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
                print(f"An error occurred while processing a chunk: {e}")
                # 可以在这里添加更多的错误处理逻辑

    # 最后，打印总共使用的令牌数量
    print(f"总共使用的令牌数量: {total_token_usage['total_tokens']}")
    print(f"  提示令牌: {total_token_usage['prompt_tokens']}")
    print(f"  完成令牌: {total_token_usage['completion_tokens']}")
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


def generate_summary_and_terms(client, config, content):
    prompt = f"""Analyze the following subtitle content and provide two outputs(response in Chinese):

1. A brief summary of the content.
2. A list of technical terms, proper nouns, or specific terminology that should not be translated.

Subtitle content:
{content}

Please format your response as follows:
总结: [Your summary here]

短语术语:
- [Term 1]
- [Term 2]
- [Term 3]
...
"""
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


def translate_chunk(client, config, chunk, context, target_language, token_usage):
    chunk_size = len(chunk)
    chunk_text = "\n".join(
        [f"[{i+1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)]
    )
    prompt = f"""You are a professional translator tasked with translating subtitles to {target_language}.

Context: {context}

Original subtitle chunk:
{chunk_text}

Instructions:
1. Translate the above subtitle chunk to {target_language}.
2. You MUST follow this EXACT format for each entry:
   [index]
   [Translated text]
3. The [index] MUST be on its own line, followed by the translated text on the next line.
4. Ensure the translation accurately conveys the original meaning.
5. Preserve the tone and style appropriate for subtitles.
6. Maintain the EXACT number of entries as the original ({chunk_size}).
7. Do NOT merge or split subtitle entries. Each [index] must correspond to exactly one subtitle entry.
8. Do NOT include any additional text, explanations, or the original text in your response.
9. If one complete subtitle is separated to two lines or more, leave it as is. This is the most important rule!
10. Pay special attention to whether there are punctuation marks at the end of sentences, if the original sentence does not have a punctuation mark at the end, then no punctuation mark can be added to the end of the translated sentence! For example:
    [3]
    I learned a lot in the meantime, so today we are taking this project to the next level.
    在这段时间里，我学到了很多，所以今天我们将这个项目提升到一个新的水平。
    [4]
    I'll show you how to use Siglib embeddings to divide players into teams, how to use the keypoint detection and
    我将向你展示如何使用 Siglib embeddings 将球员划分为队伍，如何利用关键点检测和
    [5]
    homography to create video game style radar view.
    透视变换创建视频游戏风格的雷达视图。
    [6]
    We'll also use the extracted data to calculate some advanced stats like ball trajectory and Voronoi
    我们还将使用提取的数据计算一些高级统计数据，比如球的轨迹和
    [7]
    diagram illustrating team control over the pitch.
    展示球队对场地控制的 Voronoi diagram。
WARNING: Merging or splitting entries will severely impact subtitle quality. Ensure each [index] corresponds to exactly one translated entry.

Example of the required format(index from [1] to [{chunk_size}]):
[1]
[Translated text for entry 1]
[2]
[Translated text for entry 2]
...
[{chunk_size}]
[Translated text for entry {chunk_size}]

Now, provide your translation following this format:
"""
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )
    translated_text = result["content"]
    usage = result["usage"]
    # 累积令牌使用量
    token_usage["prompt_tokens"] += usage.prompt_tokens
    token_usage["completion_tokens"] += usage.completion_tokens
    token_usage["total_tokens"] += usage.total_tokens

    return process_translation(chunk_text, translated_text)


def refine_translation(
    client, config, chunk, rough_translation, context, target_language, token_usage
):
    chunk_size = len(chunk)
    original_text = "\n".join(
        [f"[{i+1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)]
    )
    prompt = f"""You are a professional translator specializing in {target_language}. Your task is to refine a rough translation of subtitles.

Context: {context}

Original text:
{original_text}

Rough translation:
{rough_translation}

Instructions:
1. Refine the translation to {target_language}.
2. You MUST follow this EXACT format for each entry:
   [index]
   [Refined translated text]
3. The [index] MUST be on its own line, followed by the refined translated text on the next line.
4. Ensure accurate conveyance of the original meaning.
5. Maintain appropriate tone and style for each line.
6. Keep the EXACT number of entries ({chunk_size}) as the original.
7. Do NOT merge or split subtitle entries. Each [index] must correspond to exactly one subtitle entry.
8. Pay special attention to entries marked as [Translation missing line - index]:
   - For these entries, provide a new translation based on the original text.
   - Ensure consistency with the surrounding context.
9. Correct any mistakes or inaccuracies in the rough translation.
10. Do NOT include any additional text, explanations, or the original text, or 'Here is the refined translation:' 'Note: blah blah blah' etc. in your response.
11. If one complete subtitle is separated to two lines or more, leave it as is. This is the most important rule!
12. Pay special attention to whether there are punctuation marks at the end of sentences, if the original sentence does not have a punctuation mark at the end, then no punctuation mark can be added to the end of the translated sentence! For example:
    [3]
    I learned a lot in the meantime, so today we are taking this project to the next level.
    在这段时间里，我学到了很多，所以今天我们将这个项目提升到一个新的水平。
    [4]
    I'll show you how to use Siglib embeddings to divide players into teams, how to use the keypoint detection and
    我将向你展示如何使用 Siglib embeddings 将球员划分为队伍，如何利用关键点检测和
    [5]
    homography to create video game style radar view.
    透视变换创建视频游戏风格的雷达视图。
    [6]
    We'll also use the extracted data to calculate some advanced stats like ball trajectory and Voronoi
    我们还将使用提取的数据计算一些高级统计数据，比如球的轨迹和
    [7]
    diagram illustrating team control over the pitch.
    展示球队对场地控制的 Voronoi diagram。
13. Every translated text length should be matched with the original text length.

WARNING: Merging or splitting entries will severely impact subtitle quality. Ensure each [index] corresponds to exactly one translated entry.

Example of the required format(index from [1] to [{chunk_size}]):
[1]
[Refined translated text for entry 1]
[2]
[Refined translated text for entry 2]
[3]
[Refined translated text for entry 3]
...
[{chunk_size}]
[Refined translated text for entry {chunk_size}]

Now, provide your refined translation following this format:
"""
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )
    refined_translation = result["content"]
    usage = result["usage"]
    # 累积令牌使用量
    token_usage["prompt_tokens"] += usage.prompt_tokens
    token_usage["completion_tokens"] += usage.completion_tokens
    token_usage["total_tokens"] += usage.total_tokens
    return process_translation(original_text, refined_translation)


def fix_missing_translations(
    client, config, chunk, refined_translation, target_language, token_usage
):
    chunk_size = len(chunk)
    original_text = "\n".join(
        [f"[{i+1}]\n[{entry.original_text}]" for i, entry in enumerate(chunk)]
    )
    prompt = f"""You are a professional translator specializing in {target_language}. Your task is to fix missing translations in a subtitle chunk.

Original text:
{original_text}

Current translation with missing lines:
{refined_translation}

Instructions:
1. Identify any lines in the current translation that are missing or incomplete. These lines are indicated in the translation as '[Translation missing line - index]'.
2. For each missing translation, provide an accurate translation of the corresponding original text line.
3. Re-translate the entire subtitle chunk, ensuring all lines are translated.
4. Maintain the exact format and number of entries.
5. Ensure consistency with the surrounding context.
6. Do NOT include any additional text, explanations, or the original text, or phrases like 'Here is the fixed translation:' in your response.
7. Ensure the total number of translated entries (including fixed ones) is exactly {chunk_size}, matching the original chunk size.

Example:

Suppose the Original text is:
[2]
This is an intimate setting for two candidates who have never met.
[3]
President Trump won the coin toss.
[4]
He chose to deliver the final closing statement of the evening.
[5]
Vice President Harris selected the podium to the right.

And the wrong Current translation with missing lines is:
[2]
[Translation missing line - 2]
[3]
这是一个亲密的环境，适合两位从未见过面的候选人。
[4]
特朗普总统赢得了抛硬币的机会。
[5]
他选择在今晚进行最后的总结发言。
[6]
哈里斯副总统选择了右侧的讲台。

Then the Correct fixed re-translation should be:
[2]
这是一个亲密的环境，适合两位从未见过面的候选人。
[3]
特朗普总统赢得了抛硬币的机会。
[4]
他选择在今晚进行最后的总结发言。
[5]
哈里斯副总统选择了右侧的讲台。

Your task is to re-arrange the translation to match the original text paragraphing from start to end! And then fix the missing translation.


Now, provide the fixed re-translation following this format:
"""
    result = LLMClientFactory.create_completion(
        client, config, [{"role": "user", "content": prompt}]
    )
    fixed_translation = result["content"]
    usage = result["usage"]
    # 累积令牌使用量
    token_usage["prompt_tokens"] += usage.prompt_tokens
    token_usage["completion_tokens"] += usage.completion_tokens
    token_usage["total_tokens"] += usage.total_tokens
    return process_translation(original_text, fixed_translation)
