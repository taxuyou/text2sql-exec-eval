"""End-to-end: the demo pairs produce the verdicts and tags shown in the README."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from t2s_eval.cli import main
from t2s_eval.evaluator import evaluate, load_pairs, summarize
from t2s_eval.execution import Verdict

PAIRS = Path(__file__).resolve().parents[1] / "demo" / "pairs.jsonl"

EXPECTED = {
    "q01": ("correct", []),
    "q02": ("correct", []),
    "q03": ("correct", []),
    "q04": ("correct", []),
    "q05": ("syntax_error", []),
    "q06": ("runtime_error", []),
    "q07": ("result_mismatch", ["join"]),
    "q08": ("result_mismatch", ["where"]),
    "q09": ("result_mismatch", ["group_by"]),
    "q10": ("result_mismatch", ["aggregation"]),
    "q11": ("result_mismatch", ["order_limit"]),
    "q12": ("result_mismatch", ["where", "order_limit"]),
    "q13": ("result_mismatch", ["join"]),
    "q14": ("result_mismatch", ["select_columns"]),
}


@pytest.fixture(scope="module")
def results(shop_db):
    return evaluate(load_pairs(PAIRS), shop_db)


def test_every_demo_pair_has_an_expectation():
    assert [p.id for p in load_pairs(PAIRS)] == list(EXPECTED)


def test_demo_verdicts_and_tags(results):
    got = {r.pair.id: (r.verdict.value, [t.category for t in r.tags]) for r in results}
    assert got == EXPECTED


def test_demo_summary(results):
    s = summarize(results)
    assert (s.correct, s.total) == (4, 14)
    assert s.exact_match == 0  # every correct prediction is worded differently from gold
    assert s.verdicts[Verdict.TIMEOUT] == 0


def test_demo_results_are_identical_in_bird_mode(shop_db):
    bird = evaluate(load_pairs(PAIRS), shop_db, mode="bird")
    assert [r.verdict.value for r in bird] == [v for v, _ in EXPECTED.values()]


def test_cli_writes_markdown_report(shop_db, tmp_path, capsys):
    out = tmp_path / "report.md"
    code = main(["--db", str(shop_db), "--pairs", str(PAIRS), "--out", str(out)])
    assert code == 0
    stdout = capsys.readouterr().out
    assert "Execution accuracy (EX)    4/14" in stdout
    md = out.read_text(encoding="utf-8")
    assert "| ID | Question | Verdict | Tags | Reason |" in md
    assert md.count("\n| q") == len(EXPECTED)


def test_per_line_db_overrides_default(shop_db, tmp_path):
    pairs = tmp_path / "pairs.jsonl"
    line = {"id": "x", "gold_sql": "SELECT 1", "pred_sql": "SELECT 1", "db": str(shop_db)}
    pairs.write_text(json.dumps(line) + "\n", encoding="utf-8")
    [r] = evaluate(load_pairs(pairs), db=None)
    assert r.verdict is Verdict.CORRECT


def test_cli_reports_bad_input(tmp_path, capsys):
    pairs = tmp_path / "bad.jsonl"
    pairs.write_text('{"gold_sql": "SELECT 1"}\n', encoding="utf-8")
    assert main(["--pairs", str(pairs), "--db", "unused.sqlite"]) == 2
    assert "missing field(s) pred_sql" in capsys.readouterr().err
