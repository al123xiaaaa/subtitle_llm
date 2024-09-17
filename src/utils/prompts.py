GENERATE_SUMMARY_PROMPT = """Analyze the following subtitle content and provide:
1. A concise summary of the video content (2-3 sentences).
2. A list of technical terms, proper nouns, or specific terminology with {target_language} translation.

Subtitle content:
{content}

Please format your response as follows:
总结: [Your summary here]

短语术语:
- [Term 1]({target_language} translation)
- [Term 2]({target_language} translation)
- [Term 3]({target_language} translation)
...
"""

TRANSLATE_CHUNK_PROMPT = """You are a professional translator tasked with translating subtitles to {target_language}.

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
7. DO NOT merge or split subtitle entries. Each [index] must correspond to exactly one subtitle entry.
8. DO NOT include any additional text, explanations, or the original text in your response.
9. If one complete subtitle is separated to two lines or more, leave it as is. This is the most important rule!
10. Pay special attention to whether there are punctuation marks at the end of sentences, if the original sentence does not have a punctuation mark at the end, then no punctuation mark can be added to the end of the translated sentence!

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

REFINE_TRANSLATION_PROMPT = """You are a professional translator specializing in {target_language}. Your task is to refine a rough translation of subtitles.

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
7. DO NOT merge or split subtitle entries. Each [index] must correspond to exactly one subtitle entry.
8. Pay special attention to entries marked as [Translation missing line - index]:
   - For these entries, provide a new translation based on the original text.
   - Ensure consistency with the surrounding context.
9. Correct any mistakes or inaccuracies in the rough translation.
10. DO NOT include any additional text, explanations, or the original text, or 'Here is the refined translation:' 'Note: blah blah blah' etc. in your response.
11. If one complete subtitle is separated to two lines or more, leave it as is. This is the most important rule!
12. Pay special attention to whether there are punctuation marks at the end of sentences, if the original sentence does not have a punctuation mark at the end, then no punctuation mark can be added to the end of the translated sentence!
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

FIX_MISSING_TRANSLATIONS_PROMPT = """You are a professional translator specializing in {target_language}. Your task is to fix missing translations in a subtitle chunk.

Original text:
{original_text}

Translation missing lines [{missing_lines_indices}]:
{missing_lines_formatted}

Instructions:
1. For each missing translation, provide an accurate translation of the corresponding original text line.
2. Maintain the exact format and number of entries.
3. DO NOT include any additional text, explanations, or the original text, or phrases like 'Here is the fixed translation:' in your response.

{example_format}

Now, provide the translation following this format:
"""

RE_TRANSLATE_PROMPT = """You are a professional translator specializing in {target_language}. Your task is to totally re-translate the following subtitle chunk in order to eliminate the repeated translated lines.

Original text:
{original_text}

Intermediate translation:
{translation}

Instructions:
1. Retranslate the entire subtitle chunk, ensure all {chunk_size} lines are translated.
2. Maintain the exact format and number of entries.
3. DO NOT include any additional text, explanations, or the original text, or phrases like 'Here is the fixed translation:' in your response.
4. Ensure the total number of translated entries (including fixed ones) is exactly {chunk_size}, matching the original chunk size.
5. Reflect before you start to translate.

Example of the required xml format:
<response>
<reflection_thinking>
(一行一行比对 Original text 和 Intermediate translation，确定错误翻译或错误排列)
<wrong_list>
<wrong>wrong 1</wrong>
<wrong>wrong 2</wrong>
<wrong>wrong 3</wrong>
...
</wrong_list>
</reflection_thinking>
<translation>
[1]
[Translated text for entry 1]
[2]
[Translated text for entry 2]
...
[{chunk_size}]
[Translated text for entry {chunk_size}]
</translation>
</response>

Now, provide the fixed re-translation following this format:
"""
