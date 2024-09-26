GENERATE_SUMMARY_PROMPT = """Analyze the following subtitle content and provide:
1. A concise {target_language} summary of the video content (5-7 sentences).
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

TRANSLATE_CHUNK_PROMPT = """You are a professional translator tasked with translating subtitles into {target_language}.

**Context:**
{context}

**Original Subtitle Chunk ({chunk_size} entries):**
{chunk_text}

**Instructions:**
1. **Translate each subtitle entry individually**, ensuring that the translation of each entry corresponds exactly to the original entry's index and position.
2. **Maintain the original formatting strictly**:
   - Each entry must begin with its **[index]** on a separate line.
   - The **translated text** must be on the line immediately following its index.
   - **Preserve all line breaks and do not add or remove any.**
3. **Do not merge or split entries**:
   - **Each [index] must correspond to exactly one subtitle entry**.
   - If an original subtitle entry spans multiple lines, **translate it as is**, maintaining the same line structure.
4. **Do not add any additional content**:
   - Do not include explanations, notes, or the original text in your response.
   - Do not prepend or append any text like "Here is the translation:".
5. **Punctuation**:
   - **Preserve the original punctuation**.
   - **Do not add punctuation marks at the end of sentences if they are not present in the original text**.
6. **Accuracy and Style**:
   - Ensure the translation accurately conveys the original meaning.
   - Preserve the tone and style appropriate for subtitles.
7. **Total Entries**:
   - **Ensure your translation contains exactly {chunk_size} entries**, matching the original number.

**Example Format (for indices [1] to [{chunk_size}]):**
[1]
[Translated text for entry 1]
[2]
[Translated text for entry 2]
...
[{chunk_size}]
[Translated text for entry {chunk_size}]

**Now, provide your translation following this format in {target_language}:**
"""

REFINE_TRANSLATION_PROMPT = """You are a professional translator specializing in {target_language}. Your task is to refine a rough translation of subtitles.

**Context:**
{context}

**Original Text ({chunk_size} entries):**
{original_text}

**Rough Translation:**
{rough_translation}

**Instructions:**
1. **Refine each translated entry individually**, ensuring that each refined entry corresponds exactly to the original entry's index and position.
2. **Maintain the original formatting strictly**:
   - Each entry must begin with its **[index]** on a separate line.
   - The **refined translated text** must be on the line immediately following its index.
   - **Preserve all line breaks and do not add or remove any.**
3. **Do not merge or split entries**:
   - **Each [index] must correspond to exactly one subtitle entry**.
   - If an original subtitle entry spans multiple lines, **refine it as is**, maintaining the same line structure.
4. **Address Missing Translations**:
   - For entries marked as **[Translation missing line - index]**, provide a new translation based on the original text.
   - Ensure consistency with the surrounding context.
5. **Do not add any additional content**:
   - Do not include explanations, notes, or the original text in your response.
   - Do not prepend or append any text like "Here is the refined translation:".
6. **Punctuation**:
   - **Preserve the original punctuation**.
   - **Do not add punctuation marks at the end of sentences if they are not present in the original text**.
7. **Style and Length**:
   - Ensure the translation accurately conveys the original meaning.
   - Maintain the tone and style appropriate for subtitles.
   - **Match the length of each translated text to the original text length**.
8. **Total Entries**:
   - **Ensure your refined translation contains exactly {chunk_size} entries**, matching the original number.

**Example Format (for indices [1] to [{chunk_size}]):**
[1]
[Refined translated text for entry 1]
[2]
[Refined translated text for entry 2]
...
[{chunk_size}]
[Refined translated text for entry {chunk_size}]

**Now, provide your refined translation following this format in {target_language}:**
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
