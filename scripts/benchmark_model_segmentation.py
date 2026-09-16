"""离线比较同一份字幕的新旧请求开销；不会调用模型或产生 API 费用。"""

import argparse
import json
from pathlib import Path

import tiktoken

from subtitle_llm.io.subtitles import SubtitleIO
from subtitle_llm.pipeline.chunks import ChunkPlanner
from subtitle_llm.pipeline.model_segmentation import SegmentationSource, plan_model_chunks, segmentation_prompt
from subtitle_llm.pipeline.normalization import NormalizationOptions, normalize_subtitle
from subtitle_llm.pipeline.prompts import TRANSLATE_SEMANTIC_TIMED_CUES_PROMPT
from subtitle_llm.pipeline.semantic_units import build_semantic_units, format_semantic_timed_cues_json, semantic_entries
from subtitle_llm.settings import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--config")
    parser.add_argument("--target-language", default="Chinese")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    options = config.pipeline
    subtitle = SubtitleIO.read(args.input)
    context = Path(args.context).read_text(encoding="utf-8")
    encoder = tiktoken.get_encoding("cl100k_base")
    normalized = normalize_subtitle(subtitle, NormalizationOptions(
        mode="always", max_cue_chars=options.normalize_max_cue_chars,
        max_line_chars=options.normalize_max_line_chars,
        max_duration_seconds=options.normalize_max_duration,
        min_duration_seconds=options.normalize_min_duration,
    ))
    units = build_semantic_units(normalized.subtitle.entries, options.semantic_max_cues_per_unit)
    by_index = {unit.index: unit for unit in units}
    common = dict(chunk_size=options.chunk_size, context_window_size=options.context_window_size,
                  max_output_tokens=int(config.translation_model.max_tokens * 0.8), encoder=encoder)
    old_chunks = ChunkPlanner(
        **common, ignore_subtitle_length=options.ignore_subtitle_length,
        output_text_resolver=lambda entry: [cue.original_text for cue in by_index[entry.index].entries],
    ).plan(semantic_entries(units))
    old_prompts = []
    for chunk in old_chunks:
        selected = [by_index[entry.index] for entry in chunk.entries]
        old_prompts.append(TRANSLATE_SEMANTIC_TIMED_CUES_PROMPT.format(
            target_language=args.target_language, context=context, boundary_context=chunk.boundary_context,
            unit_text=format_semantic_timed_cues_json(selected), semantic_unit_count=len(selected),
            cue_count=sum(len(unit.entries) for unit in selected),
        ))
    source = SegmentationSource(subtitle.entries)
    chunks = plan_model_chunks(subtitle.entries, options, int(config.translation_model.max_tokens * 0.8), encoder)
    prompts = [segmentation_prompt(source, *source.bounds(chunk.entries), context, args.target_language, options)
               for chunk in chunks]
    old_tokens = sum(len(encoder.encode(prompt)) for prompt in old_prompts)
    new_tokens = sum(len(encoder.encode(prompt)) for prompt in prompts)
    report = {
        "measurement": "offline cl100k_base prompt estimate; excludes responses, reasoning, repairs and billing",
        "source_entries": len(subtitle.entries), "source_positions": len(source.positions),
        "legacy": {"chunks": len(old_chunks), "normalized_cues": len(normalized.subtitle.entries),
                   "prompt_tokens": old_tokens},
        "model_segmentation": {"chunks": len(chunks), "prompt_tokens": new_tokens},
        "estimated_input_reduction_percent": round((1 - new_tokens / max(1, old_tokens)) * 100, 1),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
