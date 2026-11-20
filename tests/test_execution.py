from __future__ import annotations

import sqlite3

import pytest

from t2s_eval.execution import (
    SQLiteExecutor,
    Verdict,
    compare,
    has_top_level_order_by,
    rows_equal,
)

# --------------------------------------------------------------------------- #
# rows_equal: the comparison rule itself
# --------------------------------------------------------------------------- #


def test_unordered_comparison_ignores_row_order():
    assert rows_equal([(1,), (2,)], [(2,), (1,)], ordered=False)


def test_ordered_comparison_respects_row_order():
    assert not rows_equal([(1,), (2,)], [(2,), (1,)], ordered=True)
    assert rows_equal([(1,), (2,)], [(1,), (2,)], ordered=True)


def test_strict_mode_counts_duplicates():
    assert not rows_equal([(1,), (1,)], [(1,)], ordered=False)


def test_bird_mode_is_set_comparison():
    assert rows_equal([(1,), (1,)], [(1,)], ordered=False, mode="bird")
    # BIRD ignores order even if the caller asks for an ordered comparison.
    assert rows_equal([(1,), (2,)], [(2,), (1,)], ordered=True, mode="bird")


def test_column_order_matters():
    assert not rows_equal([("Kim", 3)], [(3, "Kim")], ordered=False)


def test_float_noise_is_tolerated_but_real_differences_are_not():
    assert rows_equal([(0.1 + 0.2,)], [(0.3,)], ordered=False)
    assert rows_equal([(3,)], [(3.0,)], ordered=False)
    assert not rows_equal([(0.3,)], [(0.31,)], ordered=False)


def test_null_values_compare_equal():
    assert rows_equal([(None, 1)], [(None, 1)], ordered=False)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        rows_equal([], [], ordered=False, mode="fuzzy")


# --------------------------------------------------------------------------- #
# ORDER BY detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT a FROM t ORDER BY a", True),
        ("select a from t order  by a desc limit 1", True),
        ("SELECT a FROM t", False),
        ("SELECT a FROM (SELECT a FROM t ORDER BY a) LIMIT 2", False),
        ("SELECT a, ROW_NUMBER() OVER (ORDER BY a) FROM t", False),
        ("SELECT 'order by' FROM t", False),
        ("SELECT a FROM t -- ORDER BY a", False),
        ("SELECT a FROM t UNION SELECT b FROM u ORDER BY 1", True),
    ],
)
def test_has_top_level_order_by(sql, expected):
    assert has_top_level_order_by(sql) is expected


# --------------------------------------------------------------------------- #
# compare(): verdicts
# --------------------------------------------------------------------------- #


@pytest.fixture
def ex(tiny_db):
    with SQLiteExecutor(tiny_db, timeout=2.0) as executor:
        yield executor


def test_correct_when_text_differs_but_result_is_equal(ex):
    c = compare(ex, "SELECT COUNT(*) FROM t WHERE grp = 'a'", "select count(id) from t as x where x.grp='a';")
    assert c.verdict is Verdict.CORRECT


def test_unordered_gold_accepts_any_row_order(ex):
    c = compare(ex, "SELECT id FROM t WHERE id < 3", "SELECT id FROM t WHERE id < 3 ORDER BY id DESC")
    assert c.verdict is Verdict.CORRECT
    assert not c.ordered


def test_gold_order_by_makes_comparison_ordered(ex):
    c = compare(ex, "SELECT id FROM t WHERE id < 3 ORDER BY id", "SELECT id FROM t WHERE id < 3 ORDER BY id DESC")
    assert c.ordered
    assert c.verdict is Verdict.RESULT_MISMATCH
    assert "different order" in c.message


def test_bird_mode_ignores_gold_order_by(ex):
    c = compare(
        ex,
        "SELECT id FROM t WHERE id < 3 ORDER BY id",
        "SELECT id FROM t WHERE id < 3 ORDER BY id DESC",
        mode="bird",
    )
    assert c.verdict is Verdict.CORRECT


def test_duplicate_rows_distinguish_distinct(ex):
    strict = compare(ex, "SELECT grp FROM t", "SELECT DISTINCT grp FROM t")
    bird = compare(ex, "SELECT grp FROM t", "SELECT DISTINCT grp FROM t", mode="bird")
    assert strict.verdict is Verdict.RESULT_MISMATCH
    assert bird.verdict is Verdict.CORRECT


def test_mismatch_reports_row_diff(ex):
    c = compare(ex, "SELECT id FROM t WHERE grp = 'a'", "SELECT id FROM t WHERE grp = 'b'")
    assert c.verdict is Verdict.RESULT_MISMATCH
    assert sorted(c.only_in_gold) == [(1,), (2,)]
    assert c.only_in_pred == [(3,), (3,)]


def test_syntax_error(ex):
    c = compare(ex, "SELECT id FROM t", "SELEC id FROM t")
    assert c.verdict is Verdict.SYNTAX_ERROR
    assert "syntax error" in c.message


def test_incomplete_input_is_a_syntax_error(ex):
    assert compare(ex, "SELECT id FROM t", "SELECT id FROM t WHERE (id = 1").verdict is Verdict.SYNTAX_ERROR


def test_empty_prediction_is_a_syntax_error(ex):
    assert compare(ex, "SELECT id FROM t", "  ; ").verdict is Verdict.SYNTAX_ERROR


def test_unknown_column_is_a_runtime_error(ex):
    c = compare(ex, "SELECT id FROM t", "SELECT ident FROM t")
    assert c.verdict is Verdict.RUNTIME_ERROR
    assert "no such column" in c.message


def test_unknown_table_is_a_runtime_error(ex):
    assert compare(ex, "SELECT id FROM t", "SELECT id FROM missing").verdict is Verdict.RUNTIME_ERROR


def test_timeout(tiny_db):
    endless = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
        "SELECT COUNT(*) FROM n"
    )
    with SQLiteExecutor(tiny_db, timeout=0.2) as executor:
        c = compare(executor, "SELECT 1", endless)
        assert c.verdict is Verdict.TIMEOUT
        # The connection is still usable afterwards.
        assert executor.run("SELECT COUNT(*) FROM t").rows == [(4,)]


def test_gold_failure_is_reported_separately(ex):
    c = compare(ex, "SELECT nope FROM t", "SELECT id FROM t")
    assert c.verdict is Verdict.GOLD_ERROR


def test_database_is_opened_read_only(ex, tiny_db):
    c = compare(ex, "SELECT COUNT(*) FROM t", "DELETE FROM t")
    assert c.verdict is Verdict.RUNTIME_ERROR
    conn = sqlite3.connect(tiny_db)
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone() == (4,)
    conn.close()


def test_missing_database_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SQLiteExecutor(tmp_path / "nope.sqlite")


def test_schema_is_lower_cased(ex):
    assert ex.schema() == {"t": ["id", "grp", "val"]}
