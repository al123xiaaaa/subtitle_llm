GENERATE_SUMMARY_PROMPT = """Analyze the following subtitle content and provide structured translation context.

Subtitle content:
{content}

Return compact valid JSON only. No markdown fences, no explanations.

Required JSON shape:
{{
  "summary": "A concise {target_language} summary of the video content in 5-7 sentences.",
  "terms": [
    {{"source": "proper noun or term", "target": "{target_language} translation"}}
  ],
  "source_corrections": [
    {{
      "cue_ids": [1],
      "observed": "exact source subtitle phrase",
      "corrected": "correct source phrase",
      "type": "person|place|street|inn|organization|institution|period_term|common_term|other",
      "enforcement": "hard|soft",
      "target_aliases": ["{target_language} rendering", "source proper noun if useful"],
      "confidence": "high|medium|low",
      "evidence": "short reason grounded in nearby subtitle context"
    }}
  ]
}}

Source correction rules:
- Include only corrections with grounded evidence in the subtitles.
- Use enforcement="hard" only for named entities or historically anchored proper nouns: people, places, streets, inns, organizations, institutions.
- Use enforcement="soft" for ordinary vocabulary, clothing, foods, style choices, or uncertain period terms.
- If there are no useful corrections, return "source_corrections": [].
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

TRANSLATE_SEMANTIC_UNITS_PROMPT = """You are a professional subtitle translator tasked with translating semantic subtitle units into {target_language}.

These semantic units may later be split back onto several timed subtitle cues. Translate the full meaning of each unit naturally.

**Context:**
{context}

**Readonly Boundary Context:**
{boundary_context}

**Semantic Translation Units ({chunk_size} units):**
{unit_text}

**Instructions:**
1. Translate each semantic unit as a complete thought.
2. Preserve every unit_id exactly once.
3. Do not split one unit into multiple output items.
4. Do not output readonly boundary context units.
5. Output valid JSON only. No markdown fences, no explanations.

Required JSON shape:
{{
  "translations": [
    {{"unit_id": 1, "translation": "Translated text for unit 1"}},
    {{"unit_id": 2, "translation": "Translated text for unit 2"}}
  ]
}}

Before answering, silently verify that the JSON contains exactly {chunk_size} translations with unit_id 1 through {chunk_size}.
"""

TRANSLATE_SEMANTIC_TIMED_CUES_PROMPT = """You are a senior subtitle translator specializing in {target_language}.

Translate with full semantic context, but output one translation per timed subtitle cue.

**Context:**
{context}

**Readonly Boundary Context:**
{boundary_context}

**Semantic units with timed cues ({semantic_unit_count} units, {cue_count} output cues):**
{unit_text}

**Instructions:**
1. Use the full semantic unit source to understand meaning, references, sentence continuation, and terminology.
2. Output exactly one translation for each timed cue, using cue_id values 1 through {cue_count}.
3. Preserve one-timed-cue-in, one-timed-cue-out alignment. Do not move meaning into neighboring cues.
4. If a cue is only a fragment, translate it as a natural fragment that connects to adjacent cues.
5. Preserve names, places, dates, numbers, and speaker intent. Use the provided context for likely ASR corrections when the source is clearly inconsistent with the context.
6. For any hard source correction in the context that applies to a cue, the translation must preserve the corrected named entity using one of the target aliases.
7. Do not output readonly boundary context cues.
8. Output valid JSON only. No markdown fences, no explanations.

Required JSON shape:
{{
  "translations": [
    {{"cue_id": 1, "translation": "Translated text for timed cue 1"}},
    {{"cue_id": 2, "translation": "Translated text for timed cue 2"}}
  ]
}}

Hard output constraints:
- there are exactly {cue_count} translations;
- every cue_id from 1 to {cue_count} appears once;
- no translation is empty, placeholder text, source text, punctuation-only, or explanatory commentary.
"""

REFINE_SEMANTIC_UNITS_PROMPT = """You are a professional subtitle translator specializing in {target_language}. Refine rough translations for semantic subtitle units.

**Context:**
{context}

**Readonly Boundary Context:**
{boundary_context}

**Original Semantic Units ({chunk_size} units):**
{unit_text}

**Rough Translation Reference:**
{rough_translation}

**Instructions:**
1. Refine each translation using the full semantic source unit.
2. Preserve every unit_id exactly once.
3. Do not split, merge, reorder, omit, or renumber units.
4. Keep the translation natural, faithful, and concise enough for subtitles.
5. Output valid JSON only. No markdown fences, no explanations.

Required JSON shape:
{{
  "translations": [
    {{"unit_id": 1, "translation": "Refined translated text for unit 1"}},
    {{"unit_id": 2, "translation": "Refined translated text for unit 2"}}
  ]
}}

Before answering, silently verify that the JSON contains exactly {chunk_size} translations with unit_id 1 through {chunk_size}.
"""

REPAIR_SEMANTIC_TIMED_CUES_PROMPT = """You are a senior subtitle repair translator specializing in {target_language}.

The user is reviewing timed subtitle cues and requested a repair retranslation. Your task is to repair the target-language text for the timed cues in the output range.

**Context:**
{context}

**Readonly Boundary Context:**
{boundary_context}

**Repair Brief:**
{repair_brief}

**Instructions:**
1. Translate from the source text and semantic context. Do not translate from the current translation.
2. Use the current translation only as a failure reference and to preserve good terminology when it is not part of the failure.
3. Output exactly {chunk_size} timed cue translations, numbered [1] through [{chunk_size}] in order.
4. The local output index [1] maps to the first timed cue listed under "Timed cues to repair and output".
5. Do not merge, delete, split, skip, reorder, renumber, or output readonly anchors/context.
6. Do not repeat any deterministic failures named in the Repair Brief.
7. Do not output placeholders, empty entries, source text, punctuation-only entries, explanations, or markdown.
8. Output only the XML block below.

Required XML format:
<response>
<translation>
[1]
[Repaired translation for output timed cue 1]
[2]
[Repaired translation for output timed cue 2]
...
[{chunk_size}]
[Repaired translation for output timed cue {chunk_size}]
</translation>
</response>

Hard output constraints:
- there are exactly {chunk_size} entries;
- every index from [1] to [{chunk_size}] appears once;
- no entry is empty, placeholder text, source text, or punctuation-only;
- no readonly anchor, boundary context, or non-output timed cue is output.

Now provide the repaired timed cue translations:
"""

SOURCE_CORRECTION_REPAIR_PROMPT = """You are a senior subtitle repair translator specializing in {target_language}.

Repair exactly one timed subtitle cue because a hard source correction was not preserved.

**Source Correction To Enforce:**
{correction}

**Nearby Cues:**
{nearby_cues}

**Instructions:**
1. Repair only cue {cue_id}.
2. Preserve the one-cue timing segmentation.
3. Preserve all non-erroneous meaning from the source cue, including discourse markers such as "so", "okay", fillers, dates, numbers, and tone.
4. Use the corrected source phrase and include one of the target aliases when natural.
5. Output valid JSON only. No markdown fences, no explanations.

Required JSON shape:
{{"cue_id": {cue_id}, "translation": "Repaired translation for cue {cue_id}"}}
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

ALIGNMENT_DRIFT_RETRANSLATE_PROMPT = """You are a senior subtitle translator specializing in {target_language}.

The user marked an alignment drift point in this subtitle chunk. Starting at that point, the previous translation may be shifted, missing lines, duplicated, or attached to the wrong source entry. Your task is to rebuild the translation for the drift range from the original text.

Context:
{context}

Stable alignment anchors before the drift point (readonly, do not output):
{stable_anchors}

Readonly Boundary Context:
{boundary_context}

Original drift range ({chunk_size} entries):
{original_text}

Previous flawed translation for the drift range, provided only to understand the failure pattern:
{translation_reference}

Instructions:
1. Use the stable anchors to understand the last correct alignment before the drift.
2. Translate from the Original drift range, not from the flawed translation.
3. Use the flawed translation only to avoid repeating alignment failures.
4. Output exactly {chunk_size} translated entries, numbered [1] through [{chunk_size}] in order.
5. Preserve one-entry-in, one-entry-out alignment inside the drift range. Do not merge, split, skip, reorder, renumber, or output anchor/context entries.
6. Each translated entry must be natural {target_language}, concise enough for subtitles, and faithful to the corresponding original entry.
7. Do not output placeholders, empty entries, source text, punctuation-only entries, explanations, or markdown.
8. Output only the XML block below.

Required XML format:
<response>
<translation>
[1]
[Translated text for drift entry 1]
[2]
[Translated text for drift entry 2]
...
[{chunk_size}]
[Translated text for drift entry {chunk_size}]
</translation>
</response>

Before answering, silently verify:
- there are exactly {chunk_size} entries;
- every index from [1] to [{chunk_size}] appears once;
- no entry is empty, placeholder text, source text, or punctuation-only;
- no stable anchor or readonly boundary context entry is output.

Now provide the corrected alignment-aware re-translation:
"""
