"""Ragas 分批评分 / checkpoint / 断点续跑的无 LLM 单测。"""
import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL_DIR))

import ragas_eval  # noqa: E402


class FakeResult:
    def __init__(self, value):
        self.value = value


class FakeMetric:
    def __init__(
            self,
            value: float = 0.5,
            fail_batch: bool = False,
            fail_single: bool = False,
    ) -> None:
        self.value = value
        self.fail_batch = fail_batch
        self.fail_single = fail_single
        self.calls: list[int] = []

    def batch_score(self, inputs):
        self.calls.append(len(inputs))
        if self.fail_batch:
            raise RuntimeError("batch down")
        if self.fail_single:
            raise RuntimeError("single down")
        return [FakeResult(self.value) for _ in inputs]


def _row(**overrides):
    row = {
        "question": "q",
        "answer": "a",
        "contexts": ["c"],
        "ground_truth": "g",
    }
    row.update(overrides)
    return row


def _metrics(metric):
    return {
        name: (metric, lambda row: {"row": row})
        for name in ragas_eval.METRIC_NAMES
    }


def test_batches_by_metric_and_writes_checkpoint(tmp_path):
    rows = [_row() for _ in range(5)]
    fake = FakeMetric(value=0.5)
    checkpoint = tmp_path / "cp.jsonl"

    scores, stats = ragas_eval.score_rows_checkpointed(
        rows,
        metrics=_metrics(fake),
        checkpoint_path=checkpoint,
        batch_size=2,
    )

    # 4 个指标，每个指标 5 条，按 2/2/1 分批。
    assert fake.calls == [2, 2, 1] * 4
    for name in ragas_eval.METRIC_NAMES:
        assert scores[name] == [0.5] * 5
        assert stats[name]["ok"] == 5
        assert stats[name]["missing"] == 0

    lines = checkpoint.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 20


def test_resume_skips_completed(tmp_path):
    rows = [_row() for _ in range(3)]
    checkpoint = tmp_path / "cp.jsonl"

    first = FakeMetric(value=0.4)
    ragas_eval.score_rows_checkpointed(
        rows,
        metrics=_metrics(first),
        checkpoint_path=checkpoint,
        batch_size=2,
    )

    second = FakeMetric(value=0.9)
    scores, stats = ragas_eval.score_rows_checkpointed(
        rows,
        metrics=_metrics(second),
        checkpoint_path=checkpoint,
        batch_size=2,
    )

    assert second.calls == []
    assert scores["faithfulness"] == [0.4, 0.4, 0.4]
    assert stats["faithfulness"]["ok"] == 3


def test_skips_unevaluable_rows(tmp_path):
    rows = [
        _row(contexts=[]),
        _row(answer=""),
    ]
    fake = FakeMetric(value=0.7)
    checkpoint = tmp_path / "cp.jsonl"

    scores, stats = ragas_eval.score_rows_checkpointed(
        rows,
        metrics=_metrics(fake),
        checkpoint_path=checkpoint,
    )

    # 第 1 条没有 contexts，第 2 条没有 answer。
    assert stats["faithfulness"]["skipped"] == 2
    assert stats["answer_relevancy"]["skipped"] == 1
    assert stats["answer_relevancy"]["ok"] == 1
    assert stats["context_precision"]["skipped"] == 1
    assert stats["context_precision"]["ok"] == 1
    assert stats["context_recall"]["skipped"] == 1
    assert stats["context_recall"]["ok"] == 1
    assert scores["faithfulness"] == [None, None]


def test_error_then_retry_errors(tmp_path):
    rows = [_row()]
    checkpoint = tmp_path / "cp.jsonl"

    failing = FakeMetric(fail_batch=True, fail_single=True)
    scores, stats = ragas_eval.score_rows_checkpointed(
        rows,
        metrics=_metrics(failing),
        checkpoint_path=checkpoint,
        batch_size=1,
    )
    assert stats["faithfulness"]["error"] == 1

    # 默认不重试 error。
    good = FakeMetric(value=0.8)
    scores, stats = ragas_eval.score_rows_checkpointed(
        rows,
        metrics=_metrics(good),
        checkpoint_path=checkpoint,
    )
    assert good.calls == []
    assert stats["faithfulness"]["error"] == 1

    # --retry-errors 才重试。
    scores, stats = ragas_eval.score_rows_checkpointed(
        rows,
        metrics=_metrics(good),
        checkpoint_path=checkpoint,
        retry_errors=True,
    )
    assert good.calls == [1] * 4
    assert scores["faithfulness"] == [0.8]
    assert stats["faithfulness"]["ok"] == 1


def test_batch_failure_falls_back_to_single_rows(tmp_path):
    class BatchFailsMetric(FakeMetric):
        def batch_score(self, inputs):
            self.calls.append(len(inputs))
            if len(inputs) > 1:
                raise RuntimeError("batch down")
            return [FakeResult(0.6) for _ in inputs]

    rows = [_row() for _ in range(3)]
    fake = BatchFailsMetric()
    checkpoint = tmp_path / "cp.jsonl"

    scores, stats = ragas_eval.score_rows_checkpointed(
        rows,
        metrics={"faithfulness": (fake, lambda row: {"row": row})},
        checkpoint_path=checkpoint,
        batch_size=2,
    )

    assert fake.calls == [2, 1, 1, 1]
    assert scores["faithfulness"] == [0.6, 0.6, 0.6]
    assert stats["faithfulness"]["ok"] == 3


def test_checkpoint_rows_hash_mismatch_is_rejected(tmp_path):
    checkpoint = tmp_path / "cp.jsonl"
    checkpoint.write_text(
        json.dumps({
            "rows_hash": "other-rows",
            "row_index": 0,
            "metric": "faithfulness",
            "status": "ok",
            "value": 0.1,
        }) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        ragas_eval.score_rows_checkpointed(
            [_row()],
            metrics=_metrics(FakeMetric()),
            checkpoint_path=checkpoint,
        )
