"""The local web page: its JSON API returns what the evaluator decides."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from t2s_eval.evaluator import evaluate, load_pairs
from t2s_eval.execution import mark_rows
from t2s_eval.web import MAX_ROWS, App, main, score

PAIRS = Path(__file__).resolve().parents[1] / "demo" / "pairs.jsonl"
# Talk to the test server directly, even if an HTTP proxy is configured.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@pytest.fixture(scope="module")
def base_url(shop_db):
    app = App(("127.0.0.1", 0), shop_db, load_pairs(PAIRS), quiet=True)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{app.server_address[1]}"
    app.shutdown()
    app.server_close()


def _get(url: str) -> tuple[int, str, bytes]:
    try:
        with OPENER.open(url, timeout=10) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def _post(url: str, body: bytes) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _evaluate(base_url: str, **fields: str) -> dict:
    status, body = _post(base_url + "/api/evaluate", json.dumps(fields).encode())
    assert status == 200, body
    return body


def _demo(pair_id: str) -> dict:
    p = next(p for p in load_pairs(PAIRS) if p.id == pair_id)
    return {"question": p.question, "gold_sql": p.gold_sql, "pred_sql": p.pred_sql}


def _flagged(side: dict) -> list[list]:
    return [row for row, only in zip(side["rows"], side["only"]) if only]


def test_page_is_served(base_url):
    status, ctype, body = _get(base_url + "/")
    assert status == 200 and ctype.startswith("text/html")
    page = body.decode("utf-8")
    assert "실행해서 채점" in page and "/api/evaluate" in page
    assert "<script src" not in page and "<link" not in page  # one self-contained page
    assert _get(base_url + "/?x=1")[0] == 200


def test_pairs_endpoint_lists_demo_pairs(base_url):
    status, ctype, body = _get(base_url + "/api/pairs")
    assert status == 200 and ctype.startswith("application/json")
    pairs = json.loads(body)
    assert [p["id"] for p in pairs] == [f"q{i:02d}" for i in range(1, 15)]
    assert set(pairs[0]) == {"id", "question", "gold_sql", "pred_sql"}


def test_reworded_correct_pair(base_url):
    r = _evaluate(base_url, **_demo("q03"))
    assert r["verdict"] == "correct"
    assert r["message"] == "same result (1 row)"
    assert r["exact_match"] is False  # SQL text differs, results agree
    assert r["tags"] == []
    for side in (r["gold"], r["pred"]):
        assert side["status"] == "ok"
        assert side["names"] == ["name"]
        assert side["rows"] == [["27-inch Monitor"]]
        assert side["row_count"] == 1 and side["only"] == [False] and side["only_count"] == 0


def test_group_by_mismatch_marks_differing_rows(base_url):
    r = _evaluate(base_url, **_demo("q09"))
    assert r["verdict"] == "result_mismatch"
    assert r["exact_match"] is False
    assert [t["category"] for t in r["tags"]] == ["group_by"]
    assert "gold `customers.customer_id`, pred `customers.name`" in r["tags"][0]["reason"]
    gold, pred = r["gold"], r["pred"]
    assert gold["names"] == pred["names"] == ["name", "COUNT(*)"]
    assert (gold["row_count"], pred["row_count"]) == (26, 25)
    assert (gold["only_count"], pred["only_count"]) == (2, 1)
    assert _flagged(gold) == [["Kim Minjun", 2], ["Kim Minjun", 2]]
    assert _flagged(pred) == [["Kim Minjun", 4]]
    assert gold["truncated"] is False


def test_fixing_the_prediction_turns_it_correct(base_url):
    pair = _demo("q09")
    pair["pred_sql"] = pair["pred_sql"].replace("GROUP BY c.name", "GROUP BY c.customer_id")
    r = _evaluate(base_url, **pair)
    assert r["verdict"] == "correct"
    assert r["exact_match"] is True
    assert r["gold"]["only_count"] == r["pred"]["only_count"] == 0


@pytest.mark.parametrize(
    "pair_id, verdict, message",
    [("q05", "syntax_error", "syntax error"), ("q06", "runtime_error", "no such column: signup_date")],
)
def test_prediction_errors(base_url, pair_id, verdict, message):
    r = _evaluate(base_url, **_demo(pair_id))
    assert r["verdict"] == verdict and r["tags"] == []
    assert r["gold"]["status"] == "ok"
    assert r["pred"] == {"status": "error", "error": verdict, "message": r["message"]}
    assert message in r["message"]


def test_gold_error_skips_prediction(base_url):
    r = _evaluate(base_url, gold_sql="SELECT * FROM no_such_table", pred_sql="SELECT 1")
    assert r["verdict"] == "gold_error"
    assert r["gold"]["status"] == "error" and r["gold"]["error"] == "runtime_error"
    assert r["pred"] == {"status": "not_run"}


def test_api_agrees_with_evaluate_on_every_demo_pair(base_url, shop_db):
    for result in evaluate(load_pairs(PAIRS), shop_db):
        r = _evaluate(base_url, **_demo(result.pair.id))
        assert r["verdict"] == result.verdict.value, result.pair.id
        assert r["message"] == result.comparison.message
        assert r["tags"] == [{"category": t.category, "reason": t.reason} for t in result.tags]
        assert r["exact_match"] == result.exact_match


@pytest.mark.parametrize(
    "body, status, error",
    [
        (b"{not json", 400, "Expecting property name"),
        (b'{"gold_sql": "SELECT 1"}', 400, "missing field(s) pred_sql"),
        (b'["SELECT 1"]', 400, "expected a JSON object"),
        (b"", 400, "request body"),
    ],
)
def test_bad_requests(base_url, body, status, error):
    got_status, got = _post(base_url + "/api/evaluate", body)
    assert got_status == status
    assert error in got["error"]


def test_unknown_paths(base_url):
    assert _get(base_url + "/nope")[0] == 404
    assert _post(base_url + "/api/nope", b"{}")[0] == 404


def test_long_results_are_capped_but_counted(shop_db):
    sql = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 600) "
        "SELECT i FROM n"
    )
    r = score({"gold_sql": sql, "pred_sql": sql.replace("600", "601")}, shop_db)
    gold, pred = r["gold"], r["pred"]
    assert (gold["row_count"], pred["row_count"]) == (600, 601)
    assert len(gold["rows"]) == len(pred["only"]) == MAX_ROWS and gold["truncated"]
    assert pred["only_count"] == 1  # counted over all rows, not just the ones sent


def test_cells_are_json_safe(shop_db):
    r = score({"gold_sql": "SELECT x'00ff', NULL, 1.5", "pred_sql": "SELECT 1"}, shop_db)
    assert r["gold"]["rows"] == [["x'00ff'", None, 1.5]]


def test_main_reports_missing_database(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "missing.sqlite")]) == 2
    assert "database not found" in capsys.readouterr().err


def test_mark_rows_follows_the_comparison_rule():
    rows = [("a",), ("a",), ("a",), ("b",)]
    assert mark_rows(rows, [("a",)]) == [True, False, False, False]  # duplicates count
    assert mark_rows(rows, [("a",)], mode="bird") == [True, True, True, False]
    assert mark_rows([(0.1 + 0.2,)], [(0.3,)]) == [True]  # floats rounded as in the verdict
    assert mark_rows(rows, []) == [False] * 4
