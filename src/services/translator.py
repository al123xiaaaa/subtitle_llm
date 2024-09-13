from src.services.file_handler import FileHandler
from src.services.llm_client_factory import LLMClientFactory
from src.services.json_handler import JSONSubtitleHandler  # 新增导入
from src.utils.utility_functions import (
    load_yaml_config,
    process_translation,
    chunk_list,
)
import concurrent.futures


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
        if "[Translation missing line" in refined_translation:
            refined_translation = fix_missing_translations(
                client,
                config["translation_model"],
                chunk,
                refined_translation,
                target_language,
                local_token_usage,
            )
            print(f"Fixed translation: \n{refined_translation}\n")

        chunk_results = list(zip(chunk, refined_translation.split("\n")))
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
        [f"[{i+1}]\n{entry.original_text}  " for i, entry in enumerate(chunk)]
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
9. If one complete subtitle is separated to two lines or more, leave it as is. This is the most important rule! For example:
    I thought I was going to be an astronomer, but I think I had a
    我曾以为自己会成为一名天文学家，但我想我
    a conversation with my advisor that was about being gainfully employed.
    和我导师的一次关于就业的对话。
10. Strictly correspond to punctuation marks, do not add or delete punctuation marks at will. Pay special attention to whether there are punctuation marks at the end of sentences, if the original sentence does not have a punctuation mark at the end, then no punctuation mark can be added to the end of the translated sentence!

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
        [f"[{i+1}]\n{entry.original_text}  " for i, entry in enumerate(chunk)]
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
11. If one complete subtitle is separated to two lines or more, leave it as is. This is the most important rule! For example:
    I thought I was going to be an astronomer, but I think I had a
    我曾以为自己会成为一名天文学家，但我想我
    a conversation with my advisor that was about being gainfully employed.
    和我导师的一次关于就业的对话。
12. Strictly correspond to punctuation marks, do not add or delete punctuation marks at will. Pay special attention to whether there are punctuation marks at the end of sentences, if the original sentence does not have a punctuation mark at the end, then no punctuation mark can be added to the end of the translated sentence!
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
        [f"[{i+1}]\n{entry.original_text}" for i, entry in enumerate(chunk)]
    )
    prompt = f"""You are a professional translator specializing in {target_language}. Your task is to fix missing translations in a subtitle chunk.

Original text:
{original_text}

Current translation with missing lines:
{refined_translation}

Instructions:
1. Focus only on fixing the entries marked as [Translation missing line - index].
2. Provide translations for these missing entries based on the original text.
3. Re-translate the whole subtitle chunk.
4. Maintain the exact format and number of entries.
5. Ensure consistency with the surrounding context.
6. Do NOT include any additional text, explanations, or the original text, or 'Here is the fixed translation:' etc. in your response.
7. Ensure the total number of translated entries (including fixed ones) is exactly {chunk_size}, matching the original chunk size.

Example of the required format(index from [1] to [{chunk_size}]):
[1]
[Fixed translated text for entry 1]
[2]
[Fixed translated text for entry 2]
...
[{chunk_size}]
[Fixed translated text for entry {chunk_size}]

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
