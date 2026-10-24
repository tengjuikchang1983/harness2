import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

JUDGE_PATH = Path(__file__).parents[1] / "dependencies/job-bench-eval/eval/judge.py"
if not JUDGE_PATH.is_file():
    pytest.skip("JobBench dependency is not installed", allow_module_level=True)

spec = importlib.util.spec_from_file_location("job_bench_eval_judge", JUDGE_PATH)
assert spec is not None and spec.loader is not None
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)


class KimiCompletions:
    def create(self, **kwargs):
        if kwargs["temperature"] != 1:
            raise RuntimeError("HTTP 400 invalid temperature: only 1 is allowed for this model")
        verdict = {
            "criteria_results": [
                {
                    "index": 0,
                    "passed": True,
                    "reasoning": "The output satisfies the criterion.",
                    "evidence": "correct",
                }
            ],
            "rubric_passed": True,
            "overall_reasoning": "The criterion passed.",
        }
        return SimpleNamespace(
            id="chatcmpl-test",
            object="chat.completion",
            created=0,
            model=kwargs["model"],
            choices=[
                SimpleNamespace(
                    index=0,
                    message=SimpleNamespace(role="assistant", content=json.dumps(verdict)),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )


class KimiClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=KimiCompletions())


def test_judge_temperature_environment_supports_kimi(monkeypatch):
    monkeypatch.setenv("JUDGE_TEMPERATURE", "1")
    monkeypatch.setattr(judge, "get_openai_client", lambda *_: KimiClient())

    result, _ = judge.judge_rubric(
        rubric_index=0,
        rubric={
            "rubric": "The answer must be correct.",
            "criterion": ["The answer is correct."],
            "weight": 1.0,
        },
        file_contents="correct",
        judge_model="kimi-for-coding",
        api_base="https://example.invalid/v1",
        api_key="test-key",
    )

    assert result["result"]["score"] == 1.0
    assert result["result"]["criteria_results"][0]["passed"] is True
