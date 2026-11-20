"""Run SQL on SQLite and decide whether a prediction is execution-equivalent.

Comparison rule (``mode="strict"``, the default):

* Rows are compared as a **multiset**: order-insensitive, but duplicates count.
* If the *gold* query has a top-level ``ORDER BY``, rows are compared as an
  **ordered list** instead, because the question then asks for an order.
* Rows are tuples, so the column order of the SELECT list matters (as in BIRD).
* The number of result columns must match, even when both results are empty.
* Floats are rounded to ``FLOAT_DIGITS`` decimals to absorb summation noise.

``mode="bird"`` reproduces BIRD's row-set rule, ``set(pred_rows) == set(gold_rows)``
on the raw rows: no float rounding, no column-count check, and order and
duplicates are ignored even when the gold query has ``ORDER BY``.
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import sqlglot
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

FLOAT_DIGITS = 6
MODES = ("strict", "bird")

# SQLite reports parse failures as OperationalError with one of these messages.
_SYNTAX_MARKERS = ("syntax error", "incomplete input", "unrecognized token")


class Verdict(str, Enum):
    CORRECT = "correct"
    RESULT_MISMATCH = "result_mismatch"
    SYNTAX_ERROR = "syntax_error"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    GOLD_ERROR = "gold_error"  # the reference query itself failed

    def __str__(self) -> str:
        return self.value


@dataclass
class ExecResult:
    """Outcome of running one SQL statement."""

    rows: list[tuple] | None = None
    columns: int | None = None  # from cursor.description, so known for empty results too
    error: Verdict | None = None  # SYNTAX_ERROR, RUNTIME_ERROR or TIMEOUT
    message: str = ""
    seconds: float = 0.0
    names: list[str] = field(default_factory=list)  # column names, for display only

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Comparison:
    """Verdict for one (gold, pred) pair plus the evidence behind it."""

    verdict: Verdict
    message: str
    gold: ExecResult
    pred: ExecResult
    ordered: bool = False
    only_in_gold: list[tuple] = field(default_factory=list)
    only_in_pred: list[tuple] = field(default_factory=list)


class SQLiteExecutor:
    """Read-only SQLite connection that enforces a wall-clock timeout per query."""

    def __init__(self, db_path: str | Path, timeout: float = 10.0) -> None:
        path = Path(db_path)
        if not path.is_file():
            raise FileNotFoundError(f"database not found: {path}")
        self.path = path
        self.timeout = timeout
        # mode=ro: a predicted DELETE/UPDATE fails instead of changing the data.
        self.conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SQLiteExecutor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def schema(self) -> dict[str, list[str]]:
        """Return ``{table: [column, ...]}`` with lower-cased names."""
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        schema = {}
        for (table,) in tables:
            quoted = table.replace('"', '""')
            info = self.conn.execute(f'PRAGMA table_info("{quoted}")').fetchall()
            schema[table.lower()] = [row[1].lower() for row in info]
        return schema

    def run(self, sql: str) -> ExecResult:
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return ExecResult(error=Verdict.SYNTAX_ERROR, message="empty SQL")
        deadline = time.monotonic() + self.timeout
        timed_out = False

        def on_progress() -> int:
            nonlocal timed_out
            if time.monotonic() > deadline:
                timed_out = True
                return 1  # non-zero aborts the statement
            return 0

        start = time.monotonic()
        self.conn.set_progress_handler(on_progress, 1000)
        try:
            cursor = self.conn.execute(sql)
            rows = cursor.fetchall()
            names = [d[0] for d in cursor.description or ()]
        except sqlite3.Error as e:  # OperationalError, ProgrammingError, ...
            elapsed = time.monotonic() - start
            if timed_out:
                return ExecResult(
                    error=Verdict.TIMEOUT, message=f"exceeded {self.timeout:g}s", seconds=elapsed
                )
            msg = str(e)
            kind = (
                Verdict.SYNTAX_ERROR
                if any(m in msg.lower() for m in _SYNTAX_MARKERS)
                else Verdict.RUNTIME_ERROR
            )
            return ExecResult(error=kind, message=msg, seconds=elapsed)
        finally:
            self.conn.set_progress_handler(None, 0)
        return ExecResult(
            rows=rows, columns=len(names), seconds=time.monotonic() - start, names=names
        )


def has_top_level_order_by(sql: str) -> bool:
    """True if ``ORDER BY`` appears outside any parentheses.

    Uses sqlglot's tokenizer (not the parser), so strings, comments and
    window clauses such as ``OVER (ORDER BY ...)`` are handled, and it still
    works for SQL that sqlglot cannot fully parse.
    """
    try:
        tokens = sqlglot.tokenize(sql, read="sqlite")
    except SqlglotError:
        return False
    depth = 0
    for tok in tokens:
        if tok.token_type == TokenType.L_PAREN:
            depth += 1
        elif tok.token_type == TokenType.R_PAREN:
            depth -= 1
        elif tok.token_type == TokenType.ORDER_BY and depth == 0:
            return True
    return False


def _norm_value(v: object) -> object:
    if isinstance(v, float):
        r = round(v, FLOAT_DIGITS)
        return 0.0 if r == 0 else r  # fold -0.0 into 0.0
    return v


def _norm_rows(rows: list[tuple]) -> list[tuple]:
    return [tuple(_norm_value(v) for v in row) for row in rows]


def rows_equal(
    gold: list[tuple],
    pred: list[tuple],
    *,
    ordered: bool,
    mode: str = "strict",
    columns: tuple[int | None, int | None] | None = None,
) -> bool:
    """Apply the comparison rule described in the module docstring.

    *columns* is ``(gold_column_count, pred_column_count)``; strict mode uses it
    so that two empty results with different shapes are not judged equal.
    """
    if mode == "bird":
        return set(gold) == set(pred)  # raw rows, exactly like BIRD
    if mode != "strict":
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    if columns is not None and None not in columns and columns[0] != columns[1]:
        return False
    g, p = _norm_rows(gold), _norm_rows(pred)
    if ordered:
        return g == p
    return Counter(g) == Counter(p)


def _row_diff(
    gold: list[tuple], pred: list[tuple], mode: str
) -> tuple[list[tuple], list[tuple]]:
    """Rows only in gold / only in pred, under the same rule as the verdict."""
    if mode == "bird":  # raw rows, duplicates do not count
        g, p = Counter(set(gold)), Counter(set(pred))
    else:
        g, p = Counter(_norm_rows(gold)), Counter(_norm_rows(pred))
    return list((g - p).elements()), list((p - g).elements())


def mark_rows(rows: list[tuple], only: list[tuple], *, mode: str = "strict") -> list[bool]:
    """Flag which of *rows* are the unmatched rows listed in *only*.

    *only* is ``Comparison.only_in_gold`` (for the gold rows) or
    ``only_in_pred`` (for the pred rows); rows are matched under the same rule
    as the verdict. In strict mode duplicates count, so if gold has a row three
    times and pred twice, one copy (the first) is flagged; in bird mode every
    copy of an unmatched row is flagged.
    """
    if mode == "bird":
        unmatched = set(only)
        return [row in unmatched for row in rows]
    left = Counter(only)
    flags = []
    for row in _norm_rows(rows):
        flags.append(left[row] > 0)
        if flags[-1]:
            left[row] -= 1
    return flags


def _plural(n: int, word: str = "row") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def compare(
    executor: SQLiteExecutor, gold_sql: str, pred_sql: str, *, mode: str = "strict"
) -> Comparison:
    """Execute both queries and return a :class:`Comparison`."""
    gold = executor.run(gold_sql)
    if not gold.ok:
        return Comparison(
            Verdict.GOLD_ERROR, f"gold SQL failed ({gold.error}): {gold.message}", gold, ExecResult()
        )

    pred = executor.run(pred_sql)
    if not pred.ok:
        return Comparison(pred.error, pred.message, gold, pred)

    ordered = mode == "strict" and has_top_level_order_by(gold_sql)
    assert gold.rows is not None and pred.rows is not None
    columns = (gold.columns, pred.columns)
    if rows_equal(gold.rows, pred.rows, ordered=ordered, mode=mode, columns=columns):
        return Comparison(
            Verdict.CORRECT, f"same result ({_plural(len(gold.rows))})", gold, pred, ordered
        )

    only_gold, only_pred = _row_diff(gold.rows, pred.rows, mode)
    if None not in columns and gold.columns != pred.columns:
        msg = (
            f"gold {_plural(len(gold.rows))} × {_plural(gold.columns, 'column')}, "
            f"pred {_plural(len(pred.rows))} × {_plural(pred.columns, 'column')}"
        )
    elif not only_gold and not only_pred:
        msg = f"same {_plural(len(gold.rows))} in a different order (gold has ORDER BY)"
    else:
        msg = (
            f"gold {_plural(len(gold.rows))}, pred {_plural(len(pred.rows))}; "
            f"{len(only_gold)} only in gold, {len(only_pred)} only in pred"
        )
    return Comparison(Verdict.RESULT_MISMATCH, msg, gold, pred, ordered, only_gold, only_pred)
