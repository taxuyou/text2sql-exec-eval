"""Local web page for scoring one pair: ``python -m t2s_eval.web --db DB [--pairs PAIRS]``.

Standard library only (``http.server``). The page is static HTML/JS; every
verdict, tag and highlighted row comes from :func:`evaluate` on the server.

    GET  /               the page
    GET  /api/pairs      demo pairs from --pairs (id, question, gold_sql, pred_sql)
    POST /api/evaluate   {"question", "gold_sql", "pred_sql"} -> verdict, tags, both results
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from .evaluator import Pair, PairResult, evaluate, load_pairs
from .execution import MODES, ExecResult, mark_rows

MAX_ROWS = 500  # rows sent per side; the counts are always exact
MAX_BODY = 1_000_000


def _cell(value: object) -> object:
    if isinstance(value, bytes):
        return f"x'{value.hex()}'"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _side(res: ExecResult, only: list[tuple], mode: str) -> dict:
    if res.error is not None:
        return {"status": "error", "error": res.error.value, "message": res.message}
    if res.rows is None:
        return {"status": "not_run"}
    flags = mark_rows(res.rows, only, mode=mode)
    return {
        "status": "ok",
        "names": res.names,
        "row_count": len(res.rows),
        "only_count": sum(flags),
        "rows": [[_cell(v) for v in row] for row in res.rows[:MAX_ROWS]],
        "only": flags[:MAX_ROWS],
        "truncated": len(res.rows) > MAX_ROWS,
    }


def result_to_json(r: PairResult, mode: str) -> dict:
    c = r.comparison
    return {
        "verdict": r.verdict.value,
        "message": c.message,
        "tags": [{"category": t.category, "reason": t.reason} for t in r.tags],
        "exact_match": r.exact_match,
        "ordered": c.ordered,
        "mode": mode,
        "gold": _side(c.gold, c.only_in_gold, mode),
        "pred": _side(c.pred, c.only_in_pred, mode),
    }


def score(payload: object, db: str | Path, *, mode: str = "strict", timeout: float = 10.0) -> dict:
    """Evaluate one ``{"question", "gold_sql", "pred_sql"}`` request with :func:`evaluate`."""
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    missing = [k for k in ("gold_sql", "pred_sql") if not isinstance(payload.get(k), str)]
    if missing:
        raise ValueError(f"missing field(s) {', '.join(missing)}")
    pair = Pair(
        id="web",
        question=str(payload.get("question", "")),
        gold_sql=payload["gold_sql"],
        pred_sql=payload["pred_sql"],
    )
    [result] = evaluate([pair], db, timeout=timeout, mode=mode)
    return result_to_json(result, mode)


class Handler(BaseHTTPRequestHandler):
    server: App

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, self.server.page.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/pairs":
            self._json(200, self.server.pairs)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/evaluate":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self._json(400, {"error": "missing or too large request body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            body = score(payload, self.server.db, mode=self.server.mode, timeout=self.server.timeout)
        except (ValueError, UnicodeDecodeError) as e:  # JSONDecodeError is a ValueError
            self._json(400, {"error": str(e)})
            return
        except Exception as e:  # e.g. the database file went away
            self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return
        self._json(200, body)

    def _json(self, status: int, obj: object) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8")

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        if not self.server.quiet:
            super().log_message(format, *args)


class App(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        db: str | Path,
        pairs: list[Pair],
        *,
        mode: str = "strict",
        timeout: float = 10.0,
        quiet: bool = False,
    ) -> None:
        if not Path(db).is_file():
            raise FileNotFoundError(f"database not found: {db}")
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
        self.db = str(db)
        self.mode = mode
        self.timeout = timeout
        self.quiet = quiet
        self.pairs = [
            {"id": p.id, "question": p.question, "gold_sql": p.gold_sql, "pred_sql": p.pred_sql}
            for p in pairs
        ]
        self.page = resources.files(__package__).joinpath("static/index.html").read_text("utf-8")
        super().__init__(address, Handler)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m t2s_eval.web",
        description="Local web page: run a gold and a predicted SQL and compare the results.",
    )
    p.add_argument("--db", required=True, help="SQLite database to run the queries on")
    p.add_argument("--pairs", help="JSONL of demo pairs for the drop-down (optional)")
    p.add_argument("--host", default="127.0.0.1", help="default: 127.0.0.1")
    p.add_argument("--port", type=int, default=8000, help="default: 8000")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds per query (default: 10)")
    p.add_argument("--mode", choices=MODES, default="strict", help="comparison rule (default: strict)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pairs = load_pairs(args.pairs) if args.pairs else []
        app = App(
            (args.host, args.port), args.db, pairs, mode=args.mode, timeout=args.timeout
        )
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    host, port = app.server_address[:2]
    print(f"Serving on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
