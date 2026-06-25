from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

from subtitle_llm.llm.types import ChatClient, CompletionUsage
from subtitle_llm.pipeline.llm_trace import LlmTraceRecorder
from subtitle_llm.pipeline.prompts import GENERATE_SUMMARY_PROMPT
from subtitle_llm.pipeline.source_corrections import (
    SourceCorrection,
    parse_source_corrections,
    source_corrections_to_json,
)
from subtitle_llm.pipeline.text import extract_json_payload
from subtitle_llm.settings import ModelConfig


class InputWithTimeout:
    def __init__(self, prompt: str, timeout: float):
        self.prompt = prompt
        self.timeout = timeout
        self.input: str | None = None
        self.input_received = threading.Event()

    def _get_input(self) -> None:
        try:
            self.input = input(self.prompt)
        except EOFError:
            self.input = None
        finally:
            self.input_received.set()

    def get_input(self) -> str | None:
        thread = threading.Thread(target=self._get_input, daemon=True)
        thread.start()
        self.input_received.wait(self.timeout)
        return self.input if self.input_received.is_set() else None


class ContextService:
    def __init__(
        self,
        client: ChatClient,
        model_config: ModelConfig,
        review_enabled: bool = False,
        trace_recorder: LlmTraceRecorder | None = None,
    ):
        self.client = client
        self.model_config = model_config
        self.review_enabled = review_enabled
        self.trace_recorder = trace_recorder

    def build_context(self, source_text: str, target_language: str) -> tuple[str, CompletionUsage]:
        prompt = GENERATE_SUMMARY_PROMPT.format(target_language=target_language, content=source_text)
        started_at = time.perf_counter()
        try:
            result = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])
        except Exception as exc:
            self._record_trace(
                prompt=prompt,
                response="",
                usage=CompletionUsage(),
                duration_ms=elapsed_ms(started_at),
                status="failed",
                error=str(exc),
            )
            raise
        self._record_trace(
            prompt=prompt,
            response=result.content,
            usage=result.usage,
            duration_ms=elapsed_ms(started_at),
        )

        summary, terms, corrections, source_corrections = parse_context_components(result.content)
        context = (
            f"Overall summary: {summary}\n"
            f"Short Terms: {', '.join(terms) if terms else '(none)'}\n"
            f"Source corrections JSON: {source_corrections_to_json(source_corrections)}\n"
            f"Likely ASR corrections: {', '.join(corrections) if corrections else '(none)'}"
        )
        if self.review_enabled:
            context = self.review_context_in_console(context)
        return context, result.usage

    def save_context(self, context: str, path: str | Path) -> None:
        context_path = Path(path)
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(context, encoding="utf-8")

    def review_context_in_console(self, context: str) -> str:
        print("\n===== Context Review =====")
        print(context)
        print("==========================\n")

        while True:
            user_input = InputWithTimeout("Choose an action: (Y) Proceed, (E) Edit, (A) Abort: ", 60).get_input()
            if user_input is None:
                print("\n60秒已到，自动继续后续流程。")
                return context

            normalized = user_input.strip().lower()
            if normalized == "y":
                return context
            if normalized == "a":
                sys.exit(0)
            if normalized == "e":
                print("Enter your edited context. Press Enter on an empty line to finish.")
                edited_lines: list[str] = []
                while True:
                    line = input()
                    if line == "":
                        break
                    edited_lines.append(line)
                edited_context = "\n".join(edited_lines)
                return edited_context or context
            print("Invalid input. Please enter 'Y', 'E', or 'A'.")

    def _record_trace(
        self,
        *,
        prompt: str,
        response: str,
        usage: CompletionUsage,
        duration_ms: int,
        status: str | None = None,
        error: str | None = None,
    ) -> None:
        if self.trace_recorder is None:
            return
        self.trace_recorder.record_call(
            stage="summary-context",
            prompt=prompt,
            response=response,
            model_config=self.model_config,
            usage=usage,
            duration_ms=duration_ms,
            status=status,  # type: ignore[arg-type]
            error=error,
        )


def elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def parse_context_response(content: str) -> tuple[str, list[str], list[str]]:
    summary, terms, corrections, _source_corrections = parse_context_components(content)
    return summary, terms, corrections


def parse_context_components(content: str) -> tuple[str, list[str], list[str], list[SourceCorrection]]:
    json_components = parse_json_context_components(content)
    if json_components is not None:
        return json_components

    summary_section, terms_section, corrections_section = split_context_sections(content)
    summary = summary_section.replace("总结:", "").strip()
    terms = parse_bullet_lines(terms_section)
    corrections = [
        item
        for item in parse_bullet_lines(corrections_section)
        if item.strip().lower() not in {"(none)", "none", "无", "无。"}
    ]
    source_corrections = parse_source_corrections(corrections)
    return summary, terms, corrections, source_corrections


def parse_json_context_components(content: str) -> tuple[str, list[str], list[str], list[SourceCorrection]] | None:
    try:
        payload = extract_json_payload(content)
    except ValueError:
        return None
    try:
        import json

        data = json.loads(payload)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    summary = str(data.get("summary", data.get("总结", ""))).strip()
    terms = parse_json_terms(data.get("terms", data.get("短语术语", [])))
    raw_corrections = data.get("source_corrections", data.get("asr_corrections", data.get("疑似ASR修正", [])))
    source_corrections = parse_source_corrections(raw_corrections)
    corrections = format_source_correction_summaries(source_corrections) or parse_json_terms(raw_corrections)
    return summary, terms, corrections, source_corrections


def parse_json_terms(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    terms: list[str] = []
    for item in value:
        if isinstance(item, dict):
            source = str(item.get("source", item.get("term", ""))).strip()
            target = str(item.get("target", item.get("translation", ""))).strip()
            if source and target:
                terms.append(f"{source}({target})")
            elif source:
                terms.append(source)
            continue
        text = str(item).strip()
        if text:
            terms.append(text)
    return terms


def format_source_correction_summaries(corrections: list[SourceCorrection]) -> list[str]:
    summaries: list[str] = []
    for correction in corrections:
        alias = correction.target_aliases[0] if correction.target_aliases else ""
        alias_text = f"({alias})" if alias else ""
        confidence = f" [confidence: {correction.confidence}]" if correction.confidence else ""
        summaries.append(f"{correction.observed} -> {correction.corrected}{alias_text}{confidence}")
    return summaries


def split_context_sections(content: str) -> tuple[str, str, str]:
    terms_match = re.search(r"(?:短语术语|术语|Terms)\s*[:：]", content, re.IGNORECASE)
    corrections_match = re.search(r"(?:疑似ASR修正|ASR修正|疑似修正|Likely ASR corrections)\s*[:：]", content, re.IGNORECASE)

    if not terms_match:
        return content, "", section_after(content, corrections_match)

    summary = content[:terms_match.start()]
    if corrections_match and corrections_match.start() > terms_match.end():
        terms = content[terms_match.end():corrections_match.start()]
        corrections = content[corrections_match.end():]
    else:
        terms = content[terms_match.end():]
        corrections = ""
    return summary, terms, corrections


def section_after(content: str, match: re.Match[str] | None) -> str:
    if not match:
        return ""
    return content[match.end():]


def parse_bullet_lines(section: str) -> list[str]:
    return [
        line.strip().lstrip("-").strip()
        for line in section.strip().splitlines()
        if line.strip()
    ]
