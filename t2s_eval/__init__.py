"""Execution-based Text-to-SQL evaluation with an explainable error taxonomy."""

from .evaluator import Pair, PairResult, Summary, evaluate, load_pairs, summarize
from .execution import Comparison, ExecResult, SQLiteExecutor, Verdict, compare, rows_equal
from .taxonomy import CATEGORIES, Tag, classify_mismatch

__version__ = "0.1.0"

__all__ = [
    "CATEGORIES",
    "Comparison",
    "ExecResult",
    "Pair",
    "PairResult",
    "SQLiteExecutor",
    "Summary",
    "Tag",
    "Verdict",
    "classify_mismatch",
    "compare",
    "evaluate",
    "load_pairs",
    "rows_equal",
    "summarize",
]
