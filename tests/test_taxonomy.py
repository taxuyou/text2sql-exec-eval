from __future__ import annotations

import pytest

from t2s_eval.taxonomy import CATEGORIES, classify_mismatch

SCHEMA = {
    "categories": ["category_id", "name"],
    "products": ["product_id", "name", "category_id", "price"],
    "customers": ["customer_id", "name", "city", "joined_at"],
    "orders": ["order_id", "customer_id", "order_date", "status"],
    "order_items": ["order_id", "product_id", "quantity", "unit_price"],
}

REVENUE_TOP3 = (
    "SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue "
    "FROM order_items AS oi JOIN products AS p ON oi.product_id = p.product_id "
    "GROUP BY p.product_id ORDER BY revenue DESC LIMIT 3"
)


def tags(gold: str, pred: str) -> dict[str, str]:
    result = classify_mismatch(gold, pred, SCHEMA)
    assert result, "classify_mismatch must never return an empty list"
    for tag in result:
        assert tag.category in CATEGORIES
        assert tag.reason.strip()
    return {t.category: t.reason for t in result}


# --------------------------------------------------------------------------- #
# join
# --------------------------------------------------------------------------- #


def test_join_wrong_condition():
    t = tags(
        "SELECT SUM(oi.quantity) FROM order_items oi JOIN products p ON oi.product_id = p.product_id",
        "SELECT SUM(oi.quantity) FROM order_items oi JOIN products p ON oi.order_id = p.product_id",
    )
    assert set(t) == {"join"}
    assert "order_items.order_id = products.product_id" in t["join"]


def test_join_missing_table():
    t = tags(
        "SELECT p.name FROM products p JOIN categories c ON p.category_id = c.category_id "
        "WHERE c.name = 'Books'",
        "SELECT p.name FROM products p WHERE p.category_id = 2",
    )
    assert "join" in t and "categories" in t["join"]
    assert "where" in t


def test_join_type_left_vs_inner():
    t = tags(
        "SELECT c.name, COUNT(p.product_id) FROM categories c "
        "LEFT JOIN products p ON p.category_id = c.category_id GROUP BY c.category_id",
        "SELECT c.name, COUNT(p.product_id) FROM categories c "
        "JOIN products p ON p.category_id = c.category_id GROUP BY c.category_id",
    )
    assert set(t) == {"join"}
    assert "LEFT JOIN" in t["join"] and "INNER JOIN" in t["join"]


def test_implicit_join_equals_explicit_join():
    # Same join written in WHERE vs ON, different aliases: only the filter differs.
    t = tags(
        "SELECT p.name FROM products AS p JOIN categories AS c "
        "ON p.category_id = c.category_id WHERE c.name = 'Books'",
        "SELECT products.name FROM products, categories "
        "WHERE categories.category_id = products.category_id AND categories.name = 'Sports'",
    )
    assert set(t) == {"where"}


def test_self_join_aliases_are_canonicalised():
    t = tags(
        "SELECT a.name, b.name FROM customers a JOIN customers b "
        "ON a.city = b.city AND a.customer_id < b.customer_id",
        "SELECT x.name, y.name FROM customers x JOIN customers y "
        "ON y.city = x.city AND x.customer_id <> y.customer_id",
    )
    assert set(t) == {"join"}
    assert "<>" in t["join"] and "city" not in t["join"]


# --------------------------------------------------------------------------- #
# where
# --------------------------------------------------------------------------- #


def test_where_predicate_differs():
    t = tags(
        "SELECT COUNT(*) FROM orders WHERE status = 'delivered'",
        "SELECT COUNT(*) FROM orders WHERE status <> 'cancelled'",
    )
    assert set(t) == {"where"}
    assert "orders.status = 'delivered'" in t["where"]
    assert "orders.status <> 'cancelled'" in t["where"]


def test_where_missing_predicate():
    t = tags(
        "SELECT COUNT(*) FROM orders WHERE status = 'delivered' AND order_date >= '2026-03-01'",
        "SELECT COUNT(*) FROM orders WHERE order_date >= '2026-03-01'",
    )
    assert set(t) == {"where"}
    assert "missing in pred" in t["where"]


def test_where_operand_order_is_ignored():
    t = tags(
        "SELECT name FROM customers WHERE city = 'Seoul' AND customer_id > 3",
        "SELECT name FROM customers WHERE 'Seoul' = city AND customer_id > 4",
    )
    assert "city" not in t["where"]


def test_having_counts_as_filter():
    t = tags(
        "SELECT city FROM customers GROUP BY city HAVING COUNT(*) > 3",
        "SELECT city FROM customers GROUP BY city HAVING COUNT(*) >= 3",
    )
    assert set(t) == {"where"}
    assert "HAVING" in t["where"]


# --------------------------------------------------------------------------- #
# group_by
# --------------------------------------------------------------------------- #


def test_group_by_key_differs():
    t = tags(
        "SELECT c.name, COUNT(*) FROM customers c JOIN orders o "
        "ON o.customer_id = c.customer_id GROUP BY c.customer_id",
        "SELECT c.name, COUNT(*) FROM customers c JOIN orders o "
        "ON o.customer_id = c.customer_id GROUP BY c.name",
    )
    assert set(t) == {"group_by"}
    assert "customers.customer_id" in t["group_by"] and "customers.name" in t["group_by"]


def test_group_by_position_and_alias_are_resolved():
    t = tags(
        "SELECT city AS c, COUNT(*) FROM customers GROUP BY 1 HAVING COUNT(*) > 1",
        "SELECT city AS town, COUNT(*) FROM customers GROUP BY town HAVING COUNT(*) > 2",
    )
    assert "group_by" not in t


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #


def test_aggregation_function_differs():
    t = tags(
        "SELECT category_id, SUM(price) FROM products GROUP BY category_id",
        "SELECT category_id, COUNT(price) FROM products GROUP BY category_id",
    )
    assert set(t) == {"aggregation"}
    assert "SUM(products.price)" in t["aggregation"]
    assert "COUNT(products.price)" in t["aggregation"]


def test_aggregation_argument_differs():
    t = tags(
        "SELECT COUNT(DISTINCT customer_id) FROM orders",
        "SELECT COUNT(customer_id) FROM orders",
    )
    assert set(t) == {"aggregation"}
    assert "DISTINCT" in t["aggregation"]


# --------------------------------------------------------------------------- #
# order_limit
# --------------------------------------------------------------------------- #


def test_order_direction_differs_even_with_different_alias():
    pred = REVENUE_TOP3.replace("AS revenue", "AS total").replace("ORDER BY revenue DESC", "ORDER BY total")
    t = tags(REVENUE_TOP3, pred)
    assert set(t) == {"order_limit"}
    assert "gold DESC, pred ASC" in t["order_limit"]


def test_order_by_position_equals_alias():
    pred = REVENUE_TOP3.replace("ORDER BY revenue DESC LIMIT 3", "ORDER BY 2 DESC LIMIT 5")
    t = tags(REVENUE_TOP3, pred)
    assert set(t) == {"order_limit"}
    assert "LIMIT 3" in t["order_limit"] and "LIMIT 5" in t["order_limit"]
    assert "sort" not in t["order_limit"]


def test_missing_order_by():
    t = tags(
        "SELECT name FROM products ORDER BY price DESC LIMIT 1",
        "SELECT name FROM products LIMIT 1",
    )
    assert set(t) == {"order_limit"}
    assert "no ORDER BY" in t["order_limit"]


def test_extra_order_by_without_limit_is_not_blamed():
    # Gold has no ORDER BY, so rows are compared unordered: pred's ORDER BY is harmless.
    t = tags("SELECT name FROM products WHERE price > 50000", "SELECT name FROM products ORDER BY name")
    assert set(t) == {"where"}


# --------------------------------------------------------------------------- #
# select_columns
# --------------------------------------------------------------------------- #


def test_extra_column():
    t = tags(
        "SELECT order_id, order_date FROM orders",
        "SELECT order_id, order_date, status FROM orders",
    )
    assert set(t) == {"select_columns"}
    assert "orders.status" in t["select_columns"]


def test_wrong_column():
    t = tags("SELECT name FROM customers", "SELECT city FROM customers")
    assert set(t) == {"select_columns"}
    assert "column 1" in t["select_columns"]


def test_column_order_swapped():
    t = tags("SELECT name, city FROM customers", "SELECT city, name FROM customers")
    assert set(t) == {"select_columns"}
    assert "different order" in t["select_columns"]


def test_distinct_differs():
    t = tags("SELECT DISTINCT city FROM customers", "SELECT city FROM customers")
    assert set(t) == {"select_columns"}
    assert "DISTINCT" in t["select_columns"]


# --------------------------------------------------------------------------- #
# other + general behaviour
# --------------------------------------------------------------------------- #


def test_multiple_tags():
    t = tags(
        "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS spent FROM customers c "
        "JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id "
        "WHERE c.city = 'Seoul' AND o.status <> 'cancelled' "
        "GROUP BY c.customer_id ORDER BY spent DESC LIMIT 3",
        "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS spent FROM customers c "
        "JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id "
        "WHERE c.city = 'Seoul' GROUP BY c.customer_id ORDER BY spent DESC LIMIT 5",
    )
    assert set(t) == {"where", "order_limit"}


def test_alias_and_qualification_differences_alone_produce_no_clause_tag():
    t = tags(
        "SELECT c.name FROM customers AS c WHERE c.city = 'Seoul'",
        "SELECT name FROM customers WHERE city = 'Seoul'",
    )
    assert set(t) == {"other"}


def test_top_level_union_is_other():
    t = tags("SELECT name FROM products", "SELECT name FROM products UNION SELECT name FROM categories")
    assert set(t) == {"other"}
    assert "UNION" in t["other"]


def test_unparsable_sql_is_other():
    t = tags("SELECT name FROM products", "SELECT name FROM products WHERE (")
    assert set(t) == {"other"}
    assert "could not parse" in t["other"]


def test_cte_difference_is_other():
    t = tags(
        "WITH x AS (SELECT * FROM orders WHERE status = 'delivered') SELECT COUNT(*) FROM x",
        "WITH x AS (SELECT * FROM orders WHERE status = 'shipped') SELECT COUNT(*) FROM x",
    )
    assert set(t) == {"other"}


@pytest.mark.parametrize("schema", [None, {}])
def test_works_without_schema(schema):
    result = classify_mismatch(
        "SELECT name FROM customers WHERE city = 'Seoul'",
        "SELECT name FROM customers WHERE city = 'Busan'",
        schema,
    )
    assert [t.category for t in result] == ["where"]
