# Text-to-SQL execution evaluation

- Pairs: `demo/pairs.jsonl` (14)
- Database: `demo/shop.sqlite`
- Comparison mode: `strict` (multiset of rows; ordered comparison when gold has a top-level ORDER BY)
- **Execution accuracy (EX): 4/14 = 28.6%**
- Exact string match, for contrast: 0/14 = 0.0%

## Verdicts

| Verdict | Count |
|---|---:|
| `correct` | 4 |
| `result_mismatch` | 8 |
| `syntax_error` | 1 |
| `runtime_error` | 1 |
| `timeout` | 0 |

## Mismatch causes

Counted over `result_mismatch` pairs; one pair can have several tags.

| Tag | Count | Meaning |
|---|---:|---|
| `join` | 2 | tables in FROM/JOIN, join type, or join conditions differ |
| `where` | 2 | filter predicates in WHERE or HAVING differ |
| `group_by` | 1 | GROUP BY keys differ |
| `aggregation` | 1 | aggregate function or its argument differs |
| `order_limit` | 2 | ORDER BY keys/direction or LIMIT/OFFSET differ |
| `select_columns` | 1 | returned columns, their order, or DISTINCT differ |
| `other` | 0 | no clause-level difference found (values, subqueries, CTEs, ...) |

## Pairs

| ID | Question | Verdict | Tags | Reason |
|---|---|---|---|---|
| q01 | 서울에 사는 고객은 몇 명인가요? | `correct` |  | same result (1 row); SQL text differs from gold |
| q02 | 'Books' 카테고리에 속한 상품의 이름을 모두 보여 주세요. | `correct` |  | same result (5 rows); SQL text differs from gold |
| q03 | 가장 비싼 상품의 이름은 무엇인가요? | `correct` |  | same result (1 row); SQL text differs from gold |
| q04 | 2026년 3월에 주문한 적이 있는 고객은 몇 명인가요? | `correct` |  | same result (1 row); SQL text differs from gold |
| q05 | 주문 상태별 주문 건수를 알려 주세요. | `syntax_error` |  | near "status": syntax error |
| q06 | 부산에 사는 고객의 이름과 가입일을 보여 주세요. | `runtime_error` |  | no such column: signup_date |
| q07 | 'Electronics' 카테고리 상품의 총 판매 수량은 얼마인가요? | `result_mismatch` | `join` | [join] join condition differs: gold `order_items.product_id = products.product_id`, pred `order_items.order_id = products.product_id` (gold 1 row, pred 1 row; 1 only in gold, 1 only in pred) |
| q08 | 배송 완료(delivered)된 주문의 총 매출액은 얼마인가요? | `result_mismatch` | `where` | [where] filter differs: gold `orders.status = 'delivered'`, pred `orders.status <> 'cancelled'` (gold 1 row, pred 1 row; 1 only in gold, 1 only in pred) |
| q09 | 고객별 주문 건수를 고객 이름과 함께 보여 주세요. | `result_mismatch` | `group_by` | [group_by] GROUP BY key differs: gold `customers.customer_id`, pred `customers.name` (gold 26 rows, pred 25 rows; 2 only in gold, 1 only in pred) |
| q10 | 카테고리별 총 판매 수량을 구해 주세요. | `result_mismatch` | `aggregation` | [aggregation] aggregate differs: gold `SUM(order_items.quantity)`, pred `COUNT(order_items.quantity)` (gold 5 rows, pred 5 rows; 5 only in gold, 5 only in pred) |
| q11 | 매출액 기준 상위 3개 상품과 각 매출액을 보여 주세요. | `result_mismatch` | `order_limit` | [order_limit] sort direction on `SUM(order_items.quantity * order_items.unit_price)`: gold DESC, pred ASC (gold 3 rows, pred 3 rows; 3 only in gold, 3 only in pred) |
| q12 | 서울 고객 중 취소되지 않은 주문의 금액 합계가 가장 큰 고객 3명은 누구인가요? | `result_mismatch` | `where`, `order_limit` | [where] filter missing in pred: `orders.status <> 'cancelled'` / [order_limit] gold LIMIT 3, pred LIMIT 5 (gold 3 rows, pred 5 rows; 3 only in gold, 5 only in pred) |
| q13 | 모든 카테고리의 상품 수를 보여 주세요. 상품이 없는 카테고리는 0으로 표시합니다. | `result_mismatch` | `join` | [join] join type for `products`: gold LEFT JOIN, pred INNER JOIN (gold 6 rows, pred 5 rows; 1 only in gold, 0 only in pred) |
| q14 | 2026년 6월에 들어온 주문의 주문 번호와 주문일을 보여 주세요. | `result_mismatch` | `select_columns` | [select_columns] gold returns 2 columns, pred returns 3 (extra column in pred: `orders.status`) (gold 14 rows × 2 columns, pred 14 rows × 3 columns) |

## Details

### q01 · `correct`

> 서울에 사는 고객은 몇 명인가요?

```sql
-- gold
SELECT COUNT(*) FROM customers WHERE city = 'Seoul'
-- pred
select count(c.customer_id) from customers as c where c.city = 'Seoul';
```

- Execution: same result (1 row)

### q02 · `correct`

> 'Books' 카테고리에 속한 상품의 이름을 모두 보여 주세요.

```sql
-- gold
SELECT p.name FROM products AS p JOIN categories AS c ON p.category_id = c.category_id WHERE c.name = 'Books'
-- pred
SELECT products.name FROM products, categories WHERE categories.name = 'Books' AND products.category_id = categories.category_id
```

- Execution: same result (5 rows)

### q03 · `correct`

> 가장 비싼 상품의 이름은 무엇인가요?

```sql
-- gold
SELECT name FROM products ORDER BY price DESC LIMIT 1
-- pred
SELECT name FROM products WHERE price = (SELECT MAX(price) FROM products)
```

- Execution: same result (1 row)

### q04 · `correct`

> 2026년 3월에 주문한 적이 있는 고객은 몇 명인가요?

```sql
-- gold
SELECT COUNT(DISTINCT customer_id) FROM orders WHERE order_date BETWEEN '2026-03-01' AND '2026-03-31'
-- pred
SELECT COUNT(*) FROM (SELECT DISTINCT customer_id FROM orders WHERE order_date >= '2026-03-01' AND order_date < '2026-04-01')
```

- Execution: same result (1 row)

### q05 · `syntax_error`

> 주문 상태별 주문 건수를 알려 주세요.

```sql
-- gold
SELECT status, COUNT(*) FROM orders GROUP BY status
-- pred
SELECT status, COUNT(*) FROM orders GROUP status
```

- Execution: near "status": syntax error

### q06 · `runtime_error`

> 부산에 사는 고객의 이름과 가입일을 보여 주세요.

```sql
-- gold
SELECT name, joined_at FROM customers WHERE city = 'Busan'
-- pred
SELECT name, signup_date FROM customers WHERE city = 'Busan'
```

- Execution: no such column: signup_date

### q07 · `result_mismatch`

> 'Electronics' 카테고리 상품의 총 판매 수량은 얼마인가요?

```sql
-- gold
SELECT SUM(oi.quantity) FROM order_items AS oi JOIN products AS p ON oi.product_id = p.product_id JOIN categories AS c ON p.category_id = c.category_id WHERE c.name = 'Electronics'
-- pred
SELECT SUM(oi.quantity) FROM order_items AS oi JOIN products AS p ON oi.order_id = p.product_id JOIN categories AS c ON p.category_id = c.category_id WHERE c.name = 'Electronics'
```

- Execution: gold 1 row, pred 1 row; 1 only in gold, 1 only in pred
- `join`: join condition differs: gold `order_items.product_id = products.product_id`, pred `order_items.order_id = products.product_id`
- Example row only in gold: `(59,)`
- Example row only in pred: `(23,)`

### q08 · `result_mismatch`

> 배송 완료(delivered)된 주문의 총 매출액은 얼마인가요?

```sql
-- gold
SELECT SUM(oi.quantity * oi.unit_price) FROM orders AS o JOIN order_items AS oi ON o.order_id = oi.order_id WHERE o.status = 'delivered'
-- pred
SELECT SUM(oi.quantity * oi.unit_price) FROM orders AS o JOIN order_items AS oi ON o.order_id = oi.order_id WHERE o.status <> 'cancelled'
```

- Execution: gold 1 row, pred 1 row; 1 only in gold, 1 only in pred
- `where`: filter differs: gold `orders.status = 'delivered'`, pred `orders.status <> 'cancelled'`
- Example row only in gold: `(9003000,)`
- Example row only in pred: `(13198000,)`

### q09 · `result_mismatch`

> 고객별 주문 건수를 고객 이름과 함께 보여 주세요.

```sql
-- gold
SELECT c.name, COUNT(*) FROM customers AS c JOIN orders AS o ON o.customer_id = c.customer_id GROUP BY c.customer_id
-- pred
SELECT c.name, COUNT(*) FROM customers AS c JOIN orders AS o ON o.customer_id = c.customer_id GROUP BY c.name
```

- Execution: gold 26 rows, pred 25 rows; 2 only in gold, 1 only in pred
- `group_by`: GROUP BY key differs: gold `customers.customer_id`, pred `customers.name`
- Example row only in gold: `('Kim Minjun', 2)`
- Example row only in pred: `('Kim Minjun', 4)`

### q10 · `result_mismatch`

> 카테고리별 총 판매 수량을 구해 주세요.

```sql
-- gold
SELECT c.name, SUM(oi.quantity) FROM categories AS c JOIN products AS p ON p.category_id = c.category_id JOIN order_items AS oi ON oi.product_id = p.product_id GROUP BY c.category_id
-- pred
SELECT c.name, COUNT(oi.quantity) FROM categories AS c JOIN products AS p ON p.category_id = c.category_id JOIN order_items AS oi ON oi.product_id = p.product_id GROUP BY c.category_id
```

- Execution: gold 5 rows, pred 5 rows; 5 only in gold, 5 only in pred
- `aggregation`: aggregate differs: gold `SUM(order_items.quantity)`, pred `COUNT(order_items.quantity)`
- Example row only in gold: `('Electronics', 59)`
- Example row only in pred: `('Electronics', 31)`

### q11 · `result_mismatch`

> 매출액 기준 상위 3개 상품과 각 매출액을 보여 주세요.

```sql
-- gold
SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items AS oi JOIN products AS p ON oi.product_id = p.product_id GROUP BY p.product_id ORDER BY revenue DESC LIMIT 3
-- pred
SELECT p.name, SUM(oi.quantity * oi.unit_price) AS total_sales FROM order_items AS oi JOIN products AS p ON oi.product_id = p.product_id GROUP BY p.product_id ORDER BY total_sales LIMIT 3
```

- Execution: gold 3 rows, pred 3 rows; 3 only in gold, 3 only in pred
- `order_limit`: sort direction on `SUM(order_items.quantity * order_items.unit_price)`: gold DESC, pred ASC
- Example row only in gold: `('27-inch Monitor', 3619000)`
- Example row only in pred: `('Sunscreen', 18000)`

### q12 · `result_mismatch`

> 서울 고객 중 취소되지 않은 주문의 금액 합계가 가장 큰 고객 3명은 누구인가요?

```sql
-- gold
SELECT c.name, SUM(oi.quantity * oi.unit_price) AS spent FROM customers AS c JOIN orders AS o ON o.customer_id = c.customer_id JOIN order_items AS oi ON oi.order_id = o.order_id WHERE c.city = 'Seoul' AND o.status <> 'cancelled' GROUP BY c.customer_id ORDER BY spent DESC LIMIT 3
-- pred
SELECT c.name, SUM(oi.quantity * oi.unit_price) AS spent FROM customers AS c JOIN orders AS o ON o.customer_id = c.customer_id JOIN order_items AS oi ON oi.order_id = o.order_id WHERE c.city = 'Seoul' GROUP BY c.customer_id ORDER BY spent DESC LIMIT 5
```

- Execution: gold 3 rows, pred 5 rows; 3 only in gold, 5 only in pred
- `where`: filter missing in pred: `orders.status <> 'cancelled'`
- `order_limit`: gold LIMIT 3, pred LIMIT 5
- Example row only in gold: `('Seo Yerin', 981000)`
- Example row only in pred: `('Seo Yerin', 1053000)`

### q13 · `result_mismatch`

> 모든 카테고리의 상품 수를 보여 주세요. 상품이 없는 카테고리는 0으로 표시합니다.

```sql
-- gold
SELECT c.name, COUNT(p.product_id) FROM categories AS c LEFT JOIN products AS p ON p.category_id = c.category_id GROUP BY c.category_id
-- pred
SELECT c.name, COUNT(p.product_id) FROM categories AS c JOIN products AS p ON p.category_id = c.category_id GROUP BY c.category_id
```

- Execution: gold 6 rows, pred 5 rows; 1 only in gold, 0 only in pred
- `join`: join type for `products`: gold LEFT JOIN, pred INNER JOIN
- Example row only in gold: `('Gift Cards', 0)`

### q14 · `result_mismatch`

> 2026년 6월에 들어온 주문의 주문 번호와 주문일을 보여 주세요.

```sql
-- gold
SELECT order_id, order_date FROM orders WHERE order_date LIKE '2026-06-%'
-- pred
SELECT order_id, order_date, status FROM orders WHERE order_date LIKE '2026-06-%'
```

- Execution: gold 14 rows × 2 columns, pred 14 rows × 3 columns
- `select_columns`: gold returns 2 columns, pred returns 3 (extra column in pred: `orders.status`)
- Example row only in gold: `(5, '2026-06-02')`
- Example row only in pred: `(5, '2026-06-02', 'pending')`

