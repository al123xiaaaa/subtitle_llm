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

RE_TRANSLATE_PROMPT = """You are a senior subtitle translator specializing in {target_language}.

The previous translation failed deterministic quality checks. Your task is to produce a clean full re-translation of the entire subtitle chunk from the Original text.

Original text:
{original_text}

Previous flawed translation, provided only to show what to avoid:
{translation}

Quality diagnosis of the previous translation:
{quality_report}

Instructions:
1. Translate from the Original text, not from the previous flawed translation.
2. Use the quality diagnosis only to understand what failed and avoid repeating those failures.
3. Output exactly {chunk_size} translated entries, numbered [1] through [{chunk_size}] in order.
4. Preserve one-entry-in, one-entry-out alignment. Do not merge, split, skip, reorder, or renumber entries.
5. Each translated entry must be natural {target_language}, concise enough for subtitles, and faithful to the corresponding original entry.
6. Do not output placeholders, empty entries, source text, punctuation-only entries, explanations, or markdown.
7. If adjacent original entries are similar or repetitive, translate each entry according to its own meaning and context; do not blindly copy the same translated line unless the original meaning is truly identical.
8. Preserve names, terminology, numbers, URLs, commands, paths, code-like tokens, and speaker intent.
9. Output only the XML block below.

Required XML format:
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

Before answering, silently verify:
- there are exactly {chunk_size} entries;
- every index from [1] to [{chunk_size}] appears once;
- no entry is empty, placeholder text, source text, or punctuation-only.

Now provide the corrected re-translation:
"""
