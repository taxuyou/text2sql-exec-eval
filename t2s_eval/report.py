"""Console summary and Markdown report."""

from __future__ import annotations

from .evaluator import PairResult, Summary
from .execution import Verdict
from .taxonomy import CATEGORY_HELP

MODE_HELP = {
    "strict": "multiset of rows; ordered comparison when gold has a top-level ORDER BY",
    "bird": "set of rows, order ignored (official BIRD EX rule)",
}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _reason(r: PairResult) -> str:
    c = r.comparison
    if r.verdict is Verdict.CORRECT:
        text = c.message
        if not r.exact_match:
            text += "; SQL text differs from gold"
        return text
    if r.tags:
        return " / ".join(f"[{t.category}] {t.reason}" for t in r.tags) + f" ({c.message})"
    return c.message


def render_console(results: list[PairResult], s: Summary, *, mode: str) -> str:
    lines = [
        f"Evaluated {s.total} pairs (mode={mode})",
        "",
        f"  Execution accuracy (EX)  {s.correct:>3}/{s.total}  {_pct(s.ex):>6}",
        f"  Exact string match       {s.exact_match:>3}/{s.total}  {_pct(s.em):>6}"
        "   <- for contrast only",
        "",
        "Verdicts",
    ]
    lines += [f"  {v.value:<17}{n:>3}" for v, n in s.verdicts.items()]
    n_mismatch = s.verdicts[Verdict.RESULT_MISMATCH]
    lines += ["", f"Mismatch causes ({n_mismatch} result_mismatch pairs; a pair can have several)"]
    lines += [f"  {cat:<17}{n:>3}" for cat, n in s.tags.items()]
    lines += ["", "Per pair"]
    width = max((len(r.pair.id) for r in results), default=2)
    for r in results:
        tags = ", ".join(t.category for t in r.tags)
        lines.append(f"  {r.pair.id:<{width}}  {r.verdict.value:<17}{tags}".rstrip())
    return "\n".join(lines)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_markdown(
    results: list[PairResult], s: Summary, *, mode: str, pairs_path: str, db: str | None
) -> str:
    out = [
        "# Text-to-SQL execution evaluation",
        "",
        f"- Pairs: `{pairs_path}` ({s.total})",
    ]
    if db:
        out.append(f"- Database: `{db}`")
    out += [
        f"- Comparison mode: `{mode}` ({MODE_HELP[mode]})",
        f"- **Execution accuracy (EX): {s.correct}/{s.total} = {_pct(s.ex)}**",
        f"- Exact string match, for contrast: {s.exact_match}/{s.total} = {_pct(s.em)}",
        "",
        "## Verdicts",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    out += [f"| `{v.value}` | {n} |" for v, n in s.verdicts.items()]
    out += [
        "",
        "## Mismatch causes",
        "",
        "Counted over `result_mismatch` pairs; one pair can have several tags.",
        "",
        "| Tag | Count | Meaning |",
        "|---|---:|---|",
    ]
    out += [f"| `{c}` | {n} | {CATEGORY_HELP[c]} |" for c, n in s.tags.items()]
    out += [
        "",
        "## Pairs",
        "",
        "| ID | Question | Verdict | Tags | Reason |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        tags = ", ".join(f"`{t.category}`" for t in r.tags)
        out.append(
            f"| {_cell(r.pair.id)} | {_cell(r.pair.question)} | `{r.verdict.value}` "
            f"| {tags} | {_cell(_reason(r))} |"
        )
    out += ["", "## Details", ""]
    for r in results:
        c = r.comparison
        out += [f"### {r.pair.id} · `{r.verdict.value}`", ""]
        if r.pair.question:
            out += [f"> {r.pair.question}", ""]
        out += [
            "```sql",
            "-- gold",
            r.pair.gold_sql.strip(),
            "-- pred",
            r.pair.pred_sql.strip(),
            "```",
            "",
            f"- Execution: {c.message}",
        ]
        out += [f"- `{t.category}`: {t.reason}" for t in r.tags]
        if c.only_in_gold:
            out.append(f"- Example row only in gold: `{c.only_in_gold[0]!r}`")
        if c.only_in_pred:
            out.append(f"- Example row only in pred: `{c.only_in_pred[0]!r}`")
        out.append("")
    return "\n".join(out)
