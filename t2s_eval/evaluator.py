"""Evaluate a JSONL file of (question, gold SQL, predicted SQL) pairs."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .execution import Comparison, SQLiteExecutor, Verdict, compare
from .taxonomy import CATEGORIES, Tag, classify_mismatch


@dataclass
class Pair:
    id: str
    question: str
    gold_sql: str
    pred_sql: str
    db: str | None = None


@dataclass
class PairResult:
    pair: Pair
    db: str
    comparison: Comparison
    tags: list[Tag] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        return self.comparison.verdict

    @property
    def exact_match(self) -> bool:
        return normalize_sql_text(self.pair.gold_sql) == normalize_sql_text(self.pair.pred_sql)


@dataclass
class Summary:
    total: int
    correct: int
    exact_match: int
    verdicts: Counter
    tags: Counter

    @property
    def ex(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def em(self) -> float:
        return self.exact_match / self.total if self.total else 0.0


def normalize_sql_text(sql: str) -> str:
    """Lenient string normalisation used only for the exact-match reference metric."""
    return re.sub(r"\s+", " ", sql.strip().rstrip(";").strip().lower())


def load_pairs(path: str | Path) -> list[Pair]:
    """Read pairs from JSONL: ``{"id", "question", "gold_sql", "pred_sql", "db"?}``."""
    pairs = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON ({e.msg})") from None
            missing = [k for k in ("gold_sql", "pred_sql") if not isinstance(obj.get(k), str)]
            if missing:
                raise ValueError(f"{path}:{lineno}: missing field(s) {', '.join(missing)}")
            pairs.append(
                Pair(
                    id=str(obj.get("id", f"#{len(pairs) + 1}")),
                    question=str(obj.get("question", "")),
                    gold_sql=obj["gold_sql"],
                    pred_sql=obj["pred_sql"],
                    db=obj.get("db"),
                )
            )
    return pairs


def evaluate(
    pairs: list[Pair],
    db: str | Path | None = None,
    *,
    timeout: float = 10.0,
    mode: str = "strict",
) -> list[PairResult]:
    """Run every pair; ``pair.db`` overrides the default *db* when present."""
    executors: dict[str, SQLiteExecutor] = {}
    schemas: dict[str, dict[str, list[str]]] = {}
    results = []
    try:
        for pair in pairs:
            db_path = pair.db or (str(db) if db is not None else None)
            if db_path is None:
                raise ValueError(f"pair {pair.id}: no database (use --db or a 'db' field)")
            if db_path not in executors:
                executors[db_path] = SQLiteExecutor(db_path, timeout=timeout)
                schemas[db_path] = executors[db_path].schema()
            cmp = compare(executors[db_path], pair.gold_sql, pair.pred_sql, mode=mode)
            tags = []
            if cmp.verdict is Verdict.RESULT_MISMATCH:
                tags = classify_mismatch(pair.gold_sql, pair.pred_sql, schemas[db_path])
            results.append(PairResult(pair, db_path, cmp, tags))
    finally:
        for ex in executors.values():
            ex.close()
    return results


def summarize(results: list[PairResult]) -> Summary:
    verdicts = Counter({v: 0 for v in Verdict if v is not Verdict.GOLD_ERROR})
    verdicts.update(r.verdict for r in results)
    tags = Counter({c: 0 for c in CATEGORIES})
    tags.update(t.category for r in results for t in r.tags)
    return Summary(
        total=len(results),
        correct=verdicts[Verdict.CORRECT],
        exact_match=sum(r.exact_match for r in results),
        verdicts=verdicts,
        tags=tags,
    )
