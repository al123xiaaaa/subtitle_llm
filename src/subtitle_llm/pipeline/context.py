from __future__ import annotations

import sys
import threading
from pathlib import Path

from subtitle_llm.llm.types import ChatClient, CompletionUsage
from subtitle_llm.pipeline.prompts import GENERATE_SUMMARY_PROMPT
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
    def __init__(self, client: ChatClient, model_config: ModelConfig, review_enabled: bool = False):
        self.client = client
        self.model_config = model_config
        self.review_enabled = review_enabled

    def build_context(self, source_text: str, target_language: str) -> tuple[str, CompletionUsage]:
        prompt = GENERATE_SUMMARY_PROMPT.format(target_language=target_language, content=source_text)
        result = self.client.create_completion(self.model_config, [{"role": "user", "content": prompt}])

        try:
            summary, terms = result.content.split("短语术语:")
        except ValueError:
            summary = result.content
            terms = ""

        summary = summary.replace("总结:", "").strip()
        untranslatable_terms = [
            term.strip().strip("-") for term in terms.strip().split("\n") if term.strip()
        ]
        context = f"Overall summary: {summary}\nShort Terms: {', '.join(untranslatable_terms)}"
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
