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

**Readonly Boundary Context:**
{boundary_context}

**Original Subtitle Chunk ({chunk_size} entries):**
{chunk_text}

**Instructions:**
1. Translate each subtitle entry individually, preserving the one-entry-in, one-entry-out structure.
2. Each entry must begin with its [index] on a separate line, and the translated text must follow immediately.
3. Do not merge, split, explain, or output readonly boundary context entries.
4. Ensure your translation contains exactly {chunk_size} entries.

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

**Readonly Boundary Context:**
{boundary_context}

**Original Text ({chunk_size} entries):**
{original_text}

**Rough Translation:**
{rough_translation}

**Instructions:**
1. Refine each translated entry individually, preserving each original entry's index and position.
2. Each entry must begin with its [index] on a separate line, and the refined translated text must follow immediately.
3. Do not merge, split, explain, or output readonly boundary context entries.
4. Ensure your refined translation contains exactly {chunk_size} entries.

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
3. Do not include any additional text, explanations, or original text.

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
3. Do not include any additional text, explanations, or original text.
4. Ensure the total number of translated entries is exactly {chunk_size}.

Example of the required xml format:
<response>
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
