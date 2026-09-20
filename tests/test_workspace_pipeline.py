import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_new_pipeline import FakeLLMClient, make_config
from subtitle_llm.pipeline import TranslationRequest, TranslationService
from subtitle_llm.pipeline.task_store import TranslationTaskStore
from subtitle_llm.workspace import WorkspaceStore
from subtitle_llm.workspace.budget import BudgetLedger


class WorkspacePipelineTest(unittest.TestCase):
    def test_all_first_pass_chunks_finish_before_reserved_extra_checks_and_resume_keeps_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.srt"
            source.write_text(
                "\n\n".join(
                    f"{i}\n00:00:{i * 2:02},000 --> 00:00:{i * 2 + 1:02},000\nThis is complete sentence {i}."
                    for i in range(1, 5)
                )
            )
            config = make_config()
            config.pipeline.semantic_quality = "jev"
            config.pipeline.semantic_translation = "off"
            config.pipeline.model_segmentation = "off"
            tasks = TranslationTaskStore(root / "tasks.sqlite3")
            calls = []

            class Client(FakeLLMClient):
                def create_completion(self, config, messages):
                    result = super().create_completion(config, messages)
                    if "Analyze the following subtitle content" not in messages[-1]["content"]:
                        result.usage.total_tokens = 100000
                    return result

            def evaluate(payload):
                names = payload["questions"]
                calls.append(list(names))
                if "defect" in names:
                    record = tasks.list_tasks()[0]
                    saved = tasks.load_resume_state(record.task_id)
                    self.assertEqual(len(saved["entries"]), 4)
                    self.assertTrue(all(e["translated_text"] for e in saved["entries"].values()))
                    budget = BudgetLedger(tasks.db_path).state(record.task_id + ":auto")
                    self.assertGreater(budget["reserved"], 0)
                    self.assertTrue(
                        any(
                            [e["index"] for e in payload["state"]["context"]] == check["cue_ids"]
                            for check in saved["report"]["semantic_quality_checks"]
                        )
                    )
                    return {
                        "answers": {name: {"type": "boolean", "probability": 0.1} for name in names},
                        "usage": {"totalTokens": 20},
                    }
                answers = {}
                for name, question in names.items():
                    if question["type"] == "boolean":
                        answers[name] = {"type": "boolean", "probability": 0.97 if name == "meaning_exists" else 0}
                    else:
                        candidates = question["criteria"]
                        first = next(iter(candidates))
                        answers[name] = {
                            "type": "choice",
                            "choice": first,
                            "probabilities": {key: float(key == first) for key in candidates},
                        }
                return {"answers": answers, "usage": {"totalTokens": 20}}

            with (
                patch("subtitle_llm.pipeline.semantic_quality.gateway_evaluate", evaluate),
                patch("subtitle_llm.workspace.operations.gateway_evaluate", evaluate),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                service = TranslationService(
                    config, translation_client=Client(), summary_client=Client(), task_store=tasks
                )
                result = service.translate(TranslationRequest(str(source), str(root / "first.srt"), "Chinese"))
                task_id = result.report.task_id
                assert isinstance(task_id, str)
                first_budget = BudgetLedger(tasks.db_path).state(task_id + ":auto")
                self.assertGreater(first_budget["spent"], 0)
                self.assertTrue(result.report.translation_complete)
                self.assertEqual(calls[:2], [list(calls[0]), list(calls[0])])
                self.assertNotIn("defect", calls[0])
                extra_calls = len([names for names in calls if "defect" in names])
                service.translate(TranslationRequest(None, None, "", resume=True, task_id=task_id))
                second_budget = BudgetLedger(tasks.db_path).state(task_id + ":auto")
                self.assertEqual(first_budget, second_budget)
                self.assertEqual(len([names for names in calls if "defect" in names]), extra_calls)
                workspace = WorkspaceStore(tasks)
                self.assertTrue(workspace.version(task_id)["complete"])
                self.assertEqual(len(workspace.version(task_id)["artifacts"]), 2)
