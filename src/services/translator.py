from src.services.file_handler import FileHandler
from src.services.llm_client_factory import LLMClientFactory
import concurrent.futures
import yaml

def load_config():
    import os
    # Get the directory of the current script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level to the src directory
    src_dir = os.path.dirname(current_dir)
    # Construct the path to config.yaml
    config_path = os.path.join(src_dir, "config", "config.yaml")
    with open(config_path, "r") as file:
        return yaml.safe_load(file)

def translate_subtitles(input_file, output_file, target_language):
    config = load_config()
    client = LLMClientFactory.create_client(config["translation_model"])
    client_summary = LLMClientFactory.create_client(config["summary_model"])

    subtitle = FileHandler.read_srt(input_file)

    chunk_size = config["chunk_size"]
    subtitle_chunks = [
        subtitle.entries[i : i + chunk_size]
        for i in range(0, len(subtitle.entries), chunk_size)
    ]

    subtitle_text_for_summary = "\n".join(
        [
            entry.original_text
            for entry in subtitle.entries
            if len(entry.original_text) >= 20
        ]
    )
    overall_summary, untranslatable_terms = generate_summary_and_terms(
        client_summary, config["summary_model"], subtitle_text_for_summary
    )
    context = f"Overall summary: {overall_summary}\nShort Terms to keep unchanged: {', '.join(untranslatable_terms)}"
    print(f"Context: {context}")

    translated_entries = []

    def process_chunk(chunk, chunk_index, total_chunks):
        print(f"Processing chunk {chunk_index}/{total_chunks}")
        rough_translation = translate_chunk(
            client, config["translation_model"], chunk, context, target_language
        )
        print(f"Rough translation: \n{rough_translation}\n")
        refined_translation = refine_translation(
            client, config["translation_model"], chunk, rough_translation, context, target_language
        )
        print(f"Refined translation: \n{refined_translation}\n")
        
        # 检查并修复缺失的翻译行
        if "[Translation missing line" in refined_translation:
            refined_translation = fix_missing_translations(
                client, config["translation_model"], chunk, refined_translation, target_language
            )
            print(f"Fixed translation: \n{refined_translation}\n")
        
        return list(zip(chunk, refined_translation.split("\n")))

    # Use the 'threads' config to limit parallel processing
    max_workers = config["threads"]  # Default to 4 if not specified
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        filtered_chunks = []
        for chunk in subtitle_chunks:
            # Filter out very short subtitles
            filtered_chunk = [entry for entry in chunk if len(entry.original_text.strip()) > 4]
            if filtered_chunk:
                filtered_chunks.append(filtered_chunk)
                futures.append(executor.submit(process_chunk, filtered_chunk, len(filtered_chunks), len(subtitle_chunks)))

        for future in concurrent.futures.as_completed(futures):
            chunk_results = future.result()
            for entry, refined_text in chunk_results:
                entry.set_translated_text(refined_text.strip())
                translated_entries.append(entry)

    # Add back very short subtitles without translation
    for entry in subtitle.entries:
        if len(entry.original_text.strip()) <= 4:
            entry.set_translated_text(entry.original_text)
            translated_entries.append(entry)

    # Sort the entries based on their original order
    translated_entries.sort(key=lambda x: x.index)

    subtitle.entries = translated_entries
    return subtitle

def generate_summary_and_terms(client, config, content):
    prompt = f"""Analyze the following subtitle content and provide two outputs(response in Chinese):

1. A brief summary of the content.
2. A list of technical terms, proper nouns, or specific terminology that should not be translated.

Subtitle content:
{content}

Please format your response as follows:
总结: [Your summary here]

短语术语: [List of shortterms, one per line]"""

    result = LLMClientFactory.create_completion(client, config, [{"role": "user", "content": prompt}])

    try:
        summary, terms = result.split("短语术语:")
    except ValueError:
        print("Error: Unable to split the result into summary and terms.")
        summary = result
        terms = ""
    summary = summary.replace("Summary:", "").strip()
    terms = [term.strip() for term in terms.strip().split("\n") if term.strip()]

    return summary, terms

def translate_chunk(client, config, chunk, context, target_language):
    chunk_size = len(chunk)
    chunk_text = "\n".join(
        [f"[{i+1}]\n{entry.original_text}" for i, entry in enumerate(chunk)]
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
9. If one complete subtitle is separated to two lines or more, leave it as is.
10. If the translated text contains a newline, break it into multiple lines. This is the most important rule! For example:
    I thought I was going to be an astronomer, but I think I had a
    我曾以为自己会成为一名天文学家，但我想我
    a conversation with my advisor that was about being gainfully employed.
    和我导师的一次关于就业的对话。
11. Strictly correspond to punctuation marks, do not add or delete punctuation marks at will. Pay special attention to whether there are punctuation marks at the end of sentences, if the original sentence does not have a punctuation mark at the end, then no punctuation mark can be added to the end of the translated sentence!

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

    translated_text = LLMClientFactory.create_completion(client, config, [{"role": "user", "content": prompt}])
    return process_translation(chunk_text, translated_text)

def refine_translation(client, config, chunk, rough_translation, context, target_language):
    chunk_size = len(chunk)
    original_text = "\n".join(
        [f"[{i+1}]\n{entry.original_text}" for i, entry in enumerate(chunk)]
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
...
[{chunk_size}]
[Refined translated text for entry {chunk_size}]

Now, provide your refined translation following this format:
"""

    refined_translation = LLMClientFactory.create_completion(client, config, [{"role": "user", "content": prompt}])
    return process_translation(original_text, refined_translation)

def process_translation(original_text, translated_text):
    # 将原始文本和翻译后的文本分割成行
    original_lines = original_text.split("\n")
    translated_lines = translated_text.split("\n")

    processed_lines = []
    current_translation = ""

    # 遍历翻译后的每一行
    for line in translated_lines:
        # 检查是否是新的条目（以方括号开始和结束）
        if line.strip().startswith("[") and line.strip().endswith("]"):
            # 如果有累积的翻译，添加到处理后的行列表中
            if current_translation:
                processed_lines.append(current_translation.strip())
            # 重置当前翻译
            current_translation = ""
        else:
            # 累积当前条目的翻译
            current_translation += line + " "

    # 添加最后一个累积的翻译（如果有的话）
    if current_translation:
        processed_lines.append(current_translation.strip())

    # 确保翻译后的行数与原始行数相同
    while (
        len(processed_lines) < len(original_lines) // 2
    ):  # 因为原始文本每两行表示一个条目
        # 对于缺失的翻译行，添加占位符
        processed_lines.append(
            f"[Translation missing line - {len(processed_lines) + 1}]"
        )

    # 返回处理后的翻译，确保行数与原始文本的一半相同（因为原始文本每两行表示一个条目）
    return "\n".join(processed_lines[: len(original_lines) // 2])

def fix_missing_translations(client, config, chunk, refined_translation, target_language):
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

    fixed_translation = LLMClientFactory.create_completion(client, config, [{"role": "user", "content": prompt}])
    return process_translation(original_text, fixed_translation)
