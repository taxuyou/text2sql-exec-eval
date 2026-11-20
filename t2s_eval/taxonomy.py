"""Tag the clauses where a predicted query differs from the gold query.

Both queries are parsed with sqlglot and reduced to a :class:`QueryShape`, a
normalised, clause-by-clause view of the outermost SELECT:

* identifiers are lower-cased and table aliases are replaced by table names,
  so ``c.name`` and ``customers.name`` compare equal;
* unqualified columns are qualified using the database schema;
* ``a = b`` and ``b = a`` are treated as the same predicate;
* column-to-column equalities across two tables count as join conditions,
  whether they are written in ``ON`` or in ``WHERE`` (implicit joins);
* ``ORDER BY``/``GROUP BY`` references to SELECT aliases or positions are
  resolved, so ``ORDER BY revenue`` and ``ORDER BY 2`` compare equal.

The two shapes are then diffed clause by clause. Every difference becomes a
:class:`Tag` with a short reason. Tags describe structural differences seen in
a pair whose results already differ; they explain the mismatch but do not
prove which difference caused it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

CATEGORIES = (
    "join",
    "where",
    "group_by",
    "aggregation",
    "order_limit",
    "select_columns",
    "other",
)

CATEGORY_HELP = {
    "join": "tables in FROM/JOIN, join type, or join conditions differ",
    "where": "filter predicates in WHERE or HAVING differ",
    "group_by": "GROUP BY keys differ",
    "aggregation": "aggregate function or its argument differs",
    "order_limit": "ORDER BY keys, direction or NULL ordering, or LIMIT/OFFSET differ",
    "select_columns": "returned columns, their order, or DISTINCT differ",
    "other": "no clause-level difference found (values, subqueries, CTEs, ...)",
}

_MAX_SNIPPET = 90


@dataclass(frozen=True)
class Tag:
    category: str
    reason: str

    def __str__(self) -> str:
        return f"{self.category}: {self.reason}"


@dataclass
class QueryShape:
    """Normalised clause-level view of the outermost SELECT."""

    tables: Counter = field(default_factory=Counter)  # base tables / derived tables
    outer_joins: dict[str, str] = field(default_factory=dict)  # table -> LEFT/RIGHT/FULL
    join_conditions: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)  # WHERE conjuncts
    having: set[str] = field(default_factory=set)  # HAVING conjuncts
    group_by: set[str] = field(default_factory=set)
    columns: list[str] = field(default_factory=list)  # SELECT list, aliases stripped
    column_is_aggregate: list[bool] = field(default_factory=list)
    aggregates: Counter = field(default_factory=Counter)
    distinct: bool = False
    # (key, direction): direction is ASC/DESC plus a non-default NULLS FIRST/LAST
    order_by: list[tuple[str, str]] = field(default_factory=list)
    limit: str | None = None
    offset: str | None = None
    ctes: str | None = None


class UnsupportedQuery(Exception):
    """Raised for queries whose top level is not a plain SELECT."""


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def _arg(node: exp.Expression, name: str):
    # sqlglot renamed some args (e.g. "from" -> "from_"); accept both spellings.
    value = node.args.get(f"{name}_")
    return value if value is not None else node.args.get(name)


def _sql(node: exp.Expression) -> str:
    return node.sql(dialect="sqlite")


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_SNIPPET else text[: _MAX_SNIPPET - 1] + "…"


def _code(text: str) -> str:
    return f"`{_short(text)}`"


def _enclosing_select(node: exp.Expression) -> exp.Expression | None:
    return node.find_ancestor(exp.Select)


def _sources(select: exp.Select) -> list[exp.Expression]:
    nodes = []
    from_ = _arg(select, "from")
    if from_ is not None:
        nodes.append(from_.this)
    nodes.extend(j.this for j in select.args.get("joins") or [])
    return nodes


def _source_key(src: exp.Expression) -> str:
    """Table name for a base table, canonical SQL (without alias) for a subquery."""
    if isinstance(src, exp.Table):
        return src.name
    src = src.copy()
    src.set("alias", None)
    return _canon(src)


def _resolve_scope(
    select: exp.Select,
    schema: dict[str, list[str]],
    inherited: dict[str, str],
    numbering: Counter,
) -> None:
    """Qualify columns with canonical table names inside one SELECT scope (recursive).

    A table alias becomes the table name (``c.name`` -> ``customers.name``).
    When that would be ambiguous -- a self-join, or a subquery reading the same
    table as an outer query -- the alias becomes ``<table>_<n>`` instead, numbered
    in order of appearance, so ``a``/``b`` and ``c1``/``c2`` still compare equal.
    """
    tables: dict[str, str] = {}  # alias (or name) -> table name
    for src in _sources(select):
        if isinstance(src, exp.Table):
            tables[src.alias_or_name] = src.name
        elif src.alias:
            tables[src.alias] = ""  # derived table: keep its alias

    used = Counter(t for t in tables.values() if t)
    outer_names = set(inherited) | set(inherited.values())

    rename = {**inherited}
    for alias, table in tables.items():
        if not table:
            rename[alias] = alias
        elif used[table] > 1 or table in outer_names:
            numbering[table] += 1
            rename[alias] = f"{table}_{numbering[table]}"
        else:
            rename[alias] = table

    aliases = {p.alias for p in select.expressions if isinstance(p, exp.Alias)}

    for col in list(select.find_all(exp.Column)):
        if _enclosing_select(col) is not select:
            continue
        if col.table:
            if col.table in rename:
                col.set("table", exp.to_identifier(rename[col.table]))
            continue
        in_modifier = col.find_ancestor(exp.Order, exp.Group, exp.Having) is not None
        if in_modifier and col.name in aliases:
            continue  # reference to a SELECT alias, resolved later
        owners = [a for a, t in tables.items() if t and col.name in schema.get(t, ())]
        if not owners and not schema and len(tables) == 1:
            owners = list(tables)
        if len(owners) == 1:
            col.set("table", exp.to_identifier(rename[owners[0]]))

    for src in _sources(select):
        if isinstance(src, exp.Table):
            new = rename[src.alias_or_name]
            alias = None if new == src.name else exp.TableAlias(this=exp.to_identifier(new))
            src.set("alias", alias)

    for sub in list(select.find_all(exp.Select)):
        if sub is not select and _enclosing_select(sub) is select:
            _resolve_scope(sub, schema, rename, numbering)


def _operand_key(node: exp.Expression) -> tuple[int, str]:
    return (0 if isinstance(node, exp.Column) else 1, _sql(node))


def _canon(node: exp.Expression) -> str:
    """Canonical SQL text: ``a = b`` and ``b = a`` (also ``<>``) render the same.

    Columns go left of literals, otherwise operands are ordered alphabetically.
    """
    node = node.copy()
    for cmp in list(node.find_all(exp.EQ, exp.NEQ)):
        left, right = cmp.this, cmp.expression
        if _operand_key(left) > _operand_key(right):
            cmp.set("this", right)
            cmp.set("expression", left)
    return _sql(node)


def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


def _is_join_condition(pred: exp.Expression) -> bool:
    if not isinstance(pred, exp.EQ):
        return False
    left, right = pred.this, pred.expression
    return (
        isinstance(left, exp.Column)
        and isinstance(right, exp.Column)
        and bool(left.table)
        and bool(right.table)
        and left.table != right.table
    )


def _position(node: exp.Expression) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    return None


def shape_of(sql: str, schema: dict[str, list[str]] | None = None) -> QueryShape:
    """Parse *sql* and build its :class:`QueryShape`."""
    schema = {t.lower(): [c.lower() for c in cols] for t, cols in (schema or {}).items()}
    tree = sqlglot.parse_one(sql, read="sqlite")
    if not isinstance(tree, exp.Select):
        raise UnsupportedQuery(f"top-level {tree.key.upper()} is not analysed clause by clause")

    for ident in tree.find_all(exp.Identifier):
        ident.set("this", ident.this.lower())
        ident.set("quoted", False)
    _resolve_scope(tree, schema, {}, Counter())

    shape = QueryShape()

    # SELECT list -------------------------------------------------------------
    alias_expr: dict[str, exp.Expression] = {}
    projections: list[exp.Expression] = []
    for proj in tree.expressions:
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(proj, exp.Alias):
            alias_expr[proj.alias] = inner
        projections.append(inner)
        shape.columns.append(_canon(inner))
        aggs = [a for a in inner.find_all(exp.AggFunc) if _enclosing_select(a) is tree]
        shape.column_is_aggregate.append(bool(aggs))
        shape.aggregates.update(_canon(a) for a in aggs)
    shape.distinct = tree.args.get("distinct") is not None

    def deref(node: exp.Expression) -> exp.Expression:
        """Replace SELECT-alias and positional references by the expression."""
        pos = _position(node)
        if pos is not None and 1 <= pos <= len(projections):
            return projections[pos - 1].copy()
        node = node.copy()
        for col in list(node.find_all(exp.Column)):
            if not col.table and col.name in alias_expr:
                replacement = alias_expr[col.name].copy()
                if col is node:
                    return replacement
                col.replace(replacement)
        return node

    # FROM / JOIN --------------------------------------------------------------
    for src in _sources(tree):
        shape.tables[_source_key(src)] += 1
    for join in tree.args.get("joins") or []:
        if join.side:
            shape.outer_joins[_source_key(join.this)] = f"{join.side} JOIN"
        for pred in _conjuncts(join.args.get("on")):
            shape.join_conditions.add(_canon(pred))
        using = join.args.get("using")
        if using:
            cols = ", ".join(_sql(c) for c in using)
            shape.join_conditions.add(f"USING ({cols}) -> {_sql(join.this)}")

    # WHERE / HAVING -----------------------------------------------------------
    where = tree.args.get("where")
    for pred in _conjuncts(where.this if where else None):
        if _is_join_condition(pred):
            shape.join_conditions.add(_canon(pred))
        else:
            shape.filters.add(_canon(pred))
    having = tree.args.get("having")
    for pred in _conjuncts(having.this if having else None):
        shape.having.add(_canon(deref(pred)))

    # GROUP BY -----------------------------------------------------------------
    group = tree.args.get("group")
    if group:
        shape.group_by = {_canon(deref(g)) for g in group.expressions}

    # ORDER BY / LIMIT / OFFSET -------------------------------------------------
    order = tree.args.get("order")
    for ordered in order.expressions if order else []:
        key = _canon(deref(ordered.this))
        if key in shape.columns:
            key = f"#{shape.columns.index(key) + 1}"
        desc = bool(ordered.args.get("desc"))
        direction = "DESC" if desc else "ASC"
        # sqlglot records where NULLs go. SQLite puts them first for ASC and last
        # for DESC; spell out only a non-default placement, so `x` == `x NULLS FIRST`.
        nulls_first = ordered.args.get("nulls_first")
        if nulls_first is not None and bool(nulls_first) == desc:
            direction += " NULLS FIRST" if nulls_first else " NULLS LAST"
        shape.order_by.append((key, direction))
    limit = tree.args.get("limit")
    if limit is not None:
        shape.limit = _sql(limit.expression)
        if limit.args.get("offset") is not None:
            shape.offset = _sql(limit.args["offset"])
    offset = tree.args.get("offset")
    if offset is not None:
        shape.offset = _sql(offset.expression)

    with_ = _arg(tree, "with")
    if with_ is not None:
        shape.ctes = _canon(with_)
    return shape


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #


def _fmt_multiset(items: Counter) -> str:
    parts = []
    for item, n in sorted(items.items()):
        parts.append(_code(item) + (f" ×{n}" if n > 1 else ""))
    return ", ".join(parts)


def _fmt_set_diff(label: str, gold: set[str] | Counter, pred: set[str] | Counter) -> str:
    g, p = Counter(gold), Counter(pred)
    only_g, only_p = g - p, p - g
    if only_g and only_p:
        return f"{label} differs: gold {_fmt_multiset(only_g)}, pred {_fmt_multiset(only_p)}"
    if only_g:
        return f"{label} missing in pred: {_fmt_multiset(only_g)}"
    if only_p:
        return f"extra {label} in pred: {_fmt_multiset(only_p)}"
    return ""


def _order_text(shape: QueryShape, key: str, direction: str) -> str:
    if key.startswith("#"):
        pos = int(key[1:])
        if pos <= len(shape.columns):
            key = shape.columns[pos - 1]
    return f"{_code(key)} {direction}"


def _diff_join(g: QueryShape, p: QueryShape) -> list[str]:
    reasons = []
    if g.tables != p.tables:
        reasons.append(_fmt_set_diff("table", g.tables, p.tables))
    for table in sorted(set(g.outer_joins) | set(p.outer_joins)):
        gk = g.outer_joins.get(table, "INNER JOIN")
        pk = p.outer_joins.get(table, "INNER JOIN")
        if gk != pk and table in g.tables and table in p.tables:
            reasons.append(f"join type for {_code(table)}: gold {gk}, pred {pk}")
    if g.join_conditions != p.join_conditions:
        reasons.append(_fmt_set_diff("join condition", g.join_conditions, p.join_conditions))
    return reasons


def _diff_where(g: QueryShape, p: QueryShape) -> list[str]:
    reasons = []
    if g.filters != p.filters:
        reasons.append(_fmt_set_diff("filter", g.filters, p.filters))
    if g.having != p.having:
        reasons.append(_fmt_set_diff("HAVING", g.having, p.having))
    return reasons


def _diff_group(g: QueryShape, p: QueryShape) -> list[str]:
    if g.group_by == p.group_by:
        return []
    return [_fmt_set_diff("GROUP BY key", g.group_by, p.group_by)]


def _diff_aggregation(g: QueryShape, p: QueryShape) -> list[str]:
    if g.aggregates == p.aggregates:
        return []
    return [_fmt_set_diff("aggregate", g.aggregates, p.aggregates)]


def _diff_order_limit(g: QueryShape, p: QueryShape) -> list[str]:
    reasons = []
    # ORDER BY only changes the outcome when results are compared in order
    # (gold has ORDER BY) or when LIMIT/OFFSET picks rows after sorting.
    order_matters = bool(g.order_by) or any(
        x is not None for x in (g.limit, p.limit, g.offset, p.offset)
    )
    if order_matters and g.order_by != p.order_by:
        g_keys = [k for k, _ in g.order_by]
        p_keys = [k for k, _ in p.order_by]
        if not p.order_by:
            reasons.append(
                "gold sorts by "
                + ", ".join(_order_text(g, k, d) for k, d in g.order_by)
                + "; pred has no ORDER BY"
            )
        elif not g.order_by:
            reasons.append(
                "pred sorts by "
                + ", ".join(_order_text(p, k, d) for k, d in p.order_by)
                + "; gold has no ORDER BY"
            )
        elif g_keys == p_keys:
            for (key, gd), (_, pd) in zip(g.order_by, p.order_by):
                if gd != pd:
                    shown = _order_text(g, key, "").rstrip()
                    what = "sort direction" if gd.split()[0] != pd.split()[0] else "NULL ordering"
                    reasons.append(f"{what} on {shown}: gold {gd}, pred {pd}")
        else:
            reasons.append(
                "sort keys differ: gold "
                + ", ".join(_order_text(g, k, d) for k, d in g.order_by)
                + "; pred "
                + ", ".join(_order_text(p, k, d) for k, d in p.order_by)
            )
    for name, gv, pv in (("LIMIT", g.limit, p.limit), ("OFFSET", g.offset, p.offset)):
        if gv != pv:
            reasons.append(
                f"gold {name + ' ' + gv if gv else 'has no ' + name}, "
                f"pred {name + ' ' + pv if pv else 'has no ' + name}"
            )
    return reasons


def _diff_select(g: QueryShape, p: QueryShape) -> list[str]:
    reasons = []
    if g.distinct != p.distinct:
        who = "gold" if g.distinct else "pred"
        reasons.append(f"only {who} uses SELECT DISTINCT")
    if len(g.columns) != len(p.columns):
        detail = _fmt_set_diff("column", Counter(g.columns), Counter(p.columns))
        n_gold = f"{len(g.columns)} column" + ("" if len(g.columns) == 1 else "s")
        reasons.append(
            f"gold returns {n_gold}, pred returns {len(p.columns)}"
            + (f" ({detail})" if detail else "")
        )
    elif g.columns != p.columns:
        if Counter(g.columns) == Counter(p.columns):
            reasons.append("same columns in a different order (rows are compared as tuples)")
        else:
            for i, (gc, pc) in enumerate(zip(g.columns, p.columns)):
                both_agg = g.column_is_aggregate[i] and p.column_is_aggregate[i]
                if gc != pc and not both_agg:  # aggregate changes go to "aggregation"
                    reasons.append(f"column {i + 1}: gold {_code(gc)}, pred {_code(pc)}")
    return reasons


_DIFFS = (
    ("join", _diff_join),
    ("where", _diff_where),
    ("group_by", _diff_group),
    ("aggregation", _diff_aggregation),
    ("order_limit", _diff_order_limit),
    ("select_columns", _diff_select),
)


def diff_shapes(gold: QueryShape, pred: QueryShape) -> list[Tag]:
    tags = []
    for category, fn in _DIFFS:
        reasons = [r for r in fn(gold, pred) if r]
        if reasons:
            tags.append(Tag(category, "; ".join(reasons)))
    if gold.ctes != pred.ctes:
        tags.append(Tag("other", "WITH (CTE) definitions differ"))
    return tags


def classify_mismatch(
    gold_sql: str, pred_sql: str, schema: dict[str, list[str]] | None = None
) -> list[Tag]:
    """Return one :class:`Tag` per differing category (never empty)."""
    try:
        gold, pred = shape_of(gold_sql, schema), shape_of(pred_sql, schema)
    except UnsupportedQuery as e:
        return [Tag("other", str(e))]
    except SqlglotError as e:
        first_line = str(e).splitlines()[0] if str(e) else type(e).__name__
        return [Tag("other", f"sqlglot could not parse the query ({_short(first_line)})")]
    return diff_shapes(gold, pred) or [
        Tag(
            "other",
            "no clause-level difference found; check literal values, expressions "
            "inside subqueries, or ties in ORDER BY",
        )
    ]
