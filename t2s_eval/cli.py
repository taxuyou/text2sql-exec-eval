"""Command line entry point: ``python -m t2s_eval --db DB --pairs PAIRS.jsonl``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .evaluator import evaluate, load_pairs, summarize
from .execution import MODES
from .report import render_console, render_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m t2s_eval",
        description="Execution-based Text-to-SQL evaluation (EX) with an error taxonomy.",
    )
    p.add_argument("--pairs", required=True, help="JSONL with gold_sql / pred_sql per line")
    p.add_argument("--db", help="SQLite database (a per-line 'db' field overrides it)")
    p.add_argument("--out", help="write a Markdown report to this path")
    p.add_argument(
        "--timeout", type=float, default=10.0, help="seconds per query (default: 10)"
    )
    p.add_argument(
        "--mode",
        choices=MODES,
        default="strict",
        help="strict: multiset, ordered if gold has ORDER BY (default); "
        "bird: set comparison like the official BIRD script",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pairs = load_pairs(args.pairs)
        results = evaluate(pairs, args.db, timeout=args.timeout, mode=args.mode)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    summary = summarize(results)
    print(render_console(results, summary, mode=args.mode))
    if args.out:
        md = render_markdown(
            results, summary, mode=args.mode, pairs_path=args.pairs, db=args.db
        )
        Path(args.out).write_text(md + "\n", encoding="utf-8")
        print(f"\nMarkdown report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
