# text2sql-exec-eval

> **English summary.** A small, self-contained example of evaluating Text-to-SQL
> by *executing* SQL instead of comparing strings, as BIRD's Execution Accuracy
> (EX) does. Gold and predicted SQL run on SQLite with a timeout; result sets are
> compared (multiset, ordered only when the gold query has `ORDER BY`); failures
> are split into syntax / runtime / timeout. For wrong results, both queries are
> parsed with [sqlglot](https://github.com/tobymao/sqlglot) and diffed clause by
> clause to tag *why* they differ (`join`, `where`, `group_by`, `aggregation`,
> `order_limit`, `select_columns`, `other`), each with a one-line reason.
> Includes a deterministic generator for a small synthetic shop database,
> 14 demo pairs and tests. Python ≥ 3.10; the only dependency is sqlglot.

## 개요

Text-to-SQL 모델이 만든 SQL을 정답 SQL과 **문자열로 비교하지 않고 실제로 실행해서**
결과가 같은지로 채점하는 예제입니다. BIRD 벤치마크의 Execution Accuracy(EX)와 같은
방식입니다. 여기에 더해, 결과가 틀린 쿼리는 두 SQL을 AST로 파싱해 절(clause) 단위로
비교하고 "JOIN 조건이 다르다", "WHERE 필터가 빠졌다"처럼 **왜 틀렸는지를 사람이 읽을 수
있는 한 줄 이유와 함께 분류**합니다. 표준 라이브러리 `sqlite3`와 `sqlglot`만 사용하며,
설치 후에는 오프라인에서 동작합니다.

## 왜 실행 결과로 평가하나

같은 질문에 대한 올바른 SQL은 여러 형태로 쓸 수 있습니다. 데모의 `q03`:

```sql
-- 질문: 가장 비싼 상품의 이름은 무엇인가요?
-- gold
SELECT name FROM products ORDER BY price DESC LIMIT 1
-- pred
SELECT name FROM products WHERE price = (SELECT MAX(price) FROM products)
```

문자열은 거의 겹치지 않지만 실행 결과는 같으므로 정답입니다. 반대로 `q09`는 gold와
한 단어만 다릅니다.

```sql
-- gold: ... GROUP BY c.customer_id
-- pred: ... GROUP BY c.name
```

그런데 데모 DB에는 이름이 같은 고객(`Kim Minjun`)이 두 명 있어 pred는 두 사람의 주문을
합쳐 버립니다. 문자열 유사도로는 거의 정답처럼 보이지만 결과는 틀립니다.

데모 14쌍 기준으로 공백·대소문자를 정규화한 문자열 일치(exact match)는 **0/14**, 실행
정확도(EX)는 **4/14**입니다. 정답 4개가 모두 gold와 다른 문장으로 쓰여 있어 문자열
비교로는 하나도 맞았다고 판정되지 않습니다.

## 빠른 시작

```bash
pip install -e .                  # 의존성은 sqlglot 하나
python demo/build_shop_db.py      # 합성 데이터로 demo/shop.sqlite 생성
python -m t2s_eval --db demo/shop.sqlite --pairs demo/pairs.jsonl --out report.md
```

## 출력 예시

위 세 번째 명령의 실제 출력입니다.

```text
Evaluated 14 pairs (mode=strict)

  Execution accuracy (EX)    4/14   28.6%
  Exact string match         0/14    0.0%   <- for contrast only

Verdicts
  correct            4
  result_mismatch    8
  syntax_error       1
  runtime_error      1
  timeout            0

Mismatch causes (8 result_mismatch pairs; a pair can have several)
  join               2
  where              2
  group_by           1
  aggregation        1
  order_limit        2
  select_columns     1
  other              0

Per pair
  q01  correct
  q02  correct
  q03  correct
  q04  correct
  q05  syntax_error
  q06  runtime_error
  q07  result_mismatch  join
  q08  result_mismatch  where
  q09  result_mismatch  group_by
  q10  result_mismatch  aggregation
  q11  result_mismatch  order_limit
  q12  result_mismatch  where, order_limit
  q13  result_mismatch  join
  q14  result_mismatch  select_columns

Markdown report written to report.md
```

`report.md`에는 쌍별 표와 SQL 원문, 결과에서 달라진 행 예시가 들어갑니다. 일부 발췌
(전체는 [`demo/sample_report.md`](demo/sample_report.md)):

| ID | Question | Verdict | Tags | Reason |
|---|---|---|---|---|
| q03 | 가장 비싼 상품의 이름은 무엇인가요? | `correct` |  | same result (1 row); SQL text differs from gold |
| q06 | 부산에 사는 고객의 이름과 가입일을 보여 주세요. | `runtime_error` |  | no such column: signup_date |
| q09 | 고객별 주문 건수를 고객 이름과 함께 보여 주세요. | `result_mismatch` | `group_by` | [group_by] GROUP BY key differs: gold `customers.customer_id`, pred `customers.name` (gold 26 rows, pred 25 rows; 2 only in gold, 1 only in pred) |
| q11 | 매출액 기준 상위 3개 상품과 각 매출액을 보여 주세요. | `result_mismatch` | `order_limit` | [order_limit] sort direction on `SUM(order_items.quantity * order_items.unit_price)`: gold DESC, pred ASC (gold 3 rows, pred 3 rows; 3 only in gold, 3 only in pred) |
| q12 | 서울 고객 중 취소되지 않은 주문의 금액 합계가 가장 큰 고객 3명은 누구인가요? | `result_mismatch` | `where`, `order_limit` | [where] filter missing in pred: `orders.status <> 'cancelled'` / [order_limit] gold LIMIT 3, pred LIMIT 5 (gold 3 rows, pred 5 rows; 3 only in gold, 5 only in pred) |
| q13 | 모든 카테고리의 상품 수를 보여 주세요. 상품이 없는 카테고리는 0으로 표시합니다. | `result_mismatch` | `join` | [join] join type for `products`: gold LEFT JOIN, pred INNER JOIN (gold 6 rows, pred 5 rows; 1 only in gold, 0 only in pred) |

## 판정 규칙

| Verdict | 의미 |
|---|---|
| `correct` | 결과가 gold와 같음 (아래 비교 규칙 기준) |
| `result_mismatch` | 실행은 됐지만 결과가 다름 → 오류 분류 수행 |
| `syntax_error` | SQLite가 파싱하지 못함 (`syntax error`, `incomplete input`, `unrecognized token`) |
| `runtime_error` | 파싱은 됐지만 실행 실패 (`no such column`, `no such table`, 읽기 전용 DB에 쓰기 시도 등) |
| `timeout` | `--timeout` 초과 (기본 10초) |
| `gold_error` | gold SQL 자체가 실패. 데이터셋 문제이므로 따로 표시하며 EX 분모에는 포함 |

**결과 비교 규칙 (`--mode strict`, 기본값)**

1. 각 행은 튜플로 비교합니다. SELECT 열의 순서는 중요하고, 열 이름(별칭)은 보지 않습니다.
2. gold에 최상위 `ORDER BY`가 **없으면** 행 순서를 무시한 multiset 비교입니다.
   중복 행의 개수까지 같아야 하므로 `DISTINCT` 누락도 오답이 됩니다.
3. gold에 최상위 `ORDER BY`가 **있으면** 질문이 순서를 요구한다고 보고 행 순서까지
   비교합니다. 서브쿼리나 `OVER (ORDER BY ...)` 안의 `ORDER BY`는 해당하지 않습니다.
4. 실수(float)는 소수점 6자리로 반올림해 합산 순서에 따른 오차를 흡수합니다. `3`과 `3.0`은 같습니다.

**`--mode bird`** 는 BIRD 공식 평가 스크립트와 같은 `set(pred) == set(gold)` 비교입니다.
행 순서와 중복을 모두 무시하며, gold에 `ORDER BY`가 있어도 마찬가지입니다. 기본값을
더 엄격한 `strict`로 둔 이유는 `DISTINCT` 누락이나 정렬 오류를 정답으로 세지 않기
위해서입니다. 데모 14쌍은 두 모드에서 결과가 같습니다.

그 밖에 DB는 읽기 전용(`mode=ro`)으로 열기 때문에 모델이 `DELETE`/`UPDATE`를 만들어도
데이터가 바뀌지 않고, 타임아웃은 SQLite progress handler로 쿼리를 중단해 구현합니다.

## 오류 분류(taxonomy)는 어떻게 동작하나

`result_mismatch`인 쌍만 분류합니다. 두 SQL을 sqlglot으로 파싱한 뒤 바깥쪽 SELECT를
절별로 정규화된 형태(`QueryShape`)로 바꾸고 비교합니다.

**정규화** — 표기 차이 때문에 엉뚱한 태그가 붙지 않도록 먼저 맞춥니다.

- 식별자는 소문자로, 테이블 별칭은 실제 테이블 이름으로 바꿉니다 (`c.name` → `customers.name`).
  self-join이나 같은 테이블을 읽는 상관 서브쿼리처럼 별칭이 필요한 경우에는
  `customers_1`, `customers_2`처럼 등장 순서대로 번호를 붙입니다.
- 테이블 이름이 생략된 열은 DB 스키마를 보고 채웁니다 (`city` → `customers.city`).
- `a = b`와 `b = a`는 같은 조건으로 봅니다.
- 서로 다른 두 테이블의 열을 `=`로 잇는 조건은 `ON`에 있든 `WHERE`에 있든 조인
  조건으로 봅니다. 그래서 `FROM a, b WHERE a.id = b.id`와 `a JOIN b ON ...`는 같습니다.
- `ORDER BY revenue`, `ORDER BY 2`, `ORDER BY SUM(...)`처럼 SELECT 별칭이나 위치를
  가리키는 참조는 같은 대상으로 풉니다. `GROUP BY`, `HAVING`도 마찬가지입니다.

**태그** — 한 쌍에 여러 태그가 붙을 수 있고, 각 태그에는 한 줄 이유가 붙습니다.

| 태그 | 비교하는 것 | 데모 출력 예 |
|---|---|---|
| `join` | FROM/JOIN 테이블, 조인 종류(LEFT 등), 조인 조건 | `join condition differs: gold order_items.product_id = products.product_id, pred order_items.order_id = products.product_id` |
| `where` | `WHERE`·`HAVING`의 필터 조건 (AND로 나눈 집합) | `filter differs: gold orders.status = 'delivered', pred orders.status <> 'cancelled'` |
| `group_by` | `GROUP BY` 키 집합 | `GROUP BY key differs: gold customers.customer_id, pred customers.name` |
| `aggregation` | SELECT 절의 집계 함수와 인자 | `aggregate differs: gold SUM(order_items.quantity), pred COUNT(order_items.quantity)` |
| `order_limit` | `ORDER BY` 키·방향, `LIMIT`/`OFFSET` | `sort direction on SUM(...): gold DESC, pred ASC` |
| `select_columns` | 반환 열의 개수·내용·순서, `DISTINCT` | `gold returns 2 columns, pred returns 3 (extra column in pred: orders.status)` |
| `other` | 위에서 차이를 못 찾은 경우 (리터럴 값, CTE, 최상위 `UNION`, 파싱 실패 등) | `no clause-level difference found; ...` |

몇 가지 설계 원칙:

- 태그는 **구조적 차이**를 보고합니다. 결과가 다르다는 것은 실행으로 이미 확인했고,
  태그는 그 원인 후보를 알려 줄 뿐 각각이 결과를 바꿨다는 증명은 아닙니다.
- `ORDER BY` 차이는 gold에 `ORDER BY`가 있거나 어느 한쪽에 `LIMIT`/`OFFSET`이 있을
  때만 보고합니다. 순서를 무시하고 비교하는 경우에는 결과에 영향이 없기 때문입니다.
- 집계 함수가 다르면 `aggregation`만 붙이고, 같은 위치의 열 차이로 `select_columns`를
  중복해서 붙이지 않습니다.

## 입력 형식

한 줄에 한 쌍씩 JSONL로 넣습니다. `gold_sql`, `pred_sql`은 필수이고 나머지는 선택입니다.

```json
{"id": "q01", "question": "서울에 사는 고객은 몇 명인가요?", "gold_sql": "SELECT COUNT(*) FROM customers WHERE city = 'Seoul'", "pred_sql": "select count(c.customer_id) from customers as c where c.city = 'Seoul';"}
```

줄마다 `"db": "path/to/db.sqlite"`를 넣으면 `--db`보다 우선합니다. 여러 DB에 걸친
데이터셋도 파일 하나로 평가할 수 있습니다.

```text
python -m t2s_eval --pairs PAIRS.jsonl [--db DB] [--out REPORT.md] [--timeout 10] [--mode strict|bird]
```

Python에서 직접 쓸 수도 있습니다.

```python
from t2s_eval import SQLiteExecutor, compare, classify_mismatch

with SQLiteExecutor("demo/shop.sqlite", timeout=5) as ex:
    result = compare(ex, gold_sql, pred_sql)
    if result.verdict == "result_mismatch":
        for tag in classify_mismatch(gold_sql, pred_sql, ex.schema()):
            print(tag.category, "-", tag.reason)
```

## 공개 벤치마크에 적용하기

BIRD 등 공개 벤치마크의 dev 세트에도 같은 방식으로 적용할 수 있습니다. 이 저장소에는
벤치마크 데이터가 들어 있지 않으므로 각 벤치마크의 배포처에서 직접 받아야 합니다.
BIRD dev라면 `dev.json`의 `question`·`SQL`·`db_id`와 모델 예측을 합쳐 JSONL을 만들고,
`db`에 `dev_databases/<db_id>/<db_id>.sqlite` 경로를 넣으면 됩니다. 공식 점수와 같은
규칙으로 보려면 `--mode bird`를 쓰세요.

```python
import json

with open("dev.json", encoding="utf-8") as f:
    dev = json.load(f)
preds = [...]  # dev.json과 같은 순서의 모델 예측 SQL 목록

with open("bird_dev_pairs.jsonl", "w", encoding="utf-8") as f:
    for i, (item, pred) in enumerate(zip(dev, preds)):
        f.write(json.dumps({
            "id": str(i), "question": item["question"], "gold_sql": item["SQL"], "pred_sql": pred,
            "db": f"dev_databases/{item['db_id']}/{item['db_id']}.sqlite",
        }, ensure_ascii=False) + "\n")
```

## 한계

- **SQLite 전용입니다.** 실행과 파싱 모두 SQLite 방언을 기준으로 합니다.
- **결과가 같다고 SQL이 같은 의미라는 보장은 없습니다.** EX는 이 DB 하나의 데이터에서만
  결과를 비교하므로, 틀린 SQL이 우연히 같은 결과를 내면 정답으로 셉니다. 데모 DB가 작을수록
  이런 경우가 늘어납니다. 여러 DB로 교차 검증하는 방식(test-suite accuracy)이 이를 보완합니다.
- **값 수준 차이는 정규화하지 않습니다.** 대소문자(`'Seoul'`/`'seoul'`), 날짜 형식, 단위,
  반올림 자릿수가 다르면 오답입니다. 예외는 float 반올림(소수 6자리)뿐입니다. 이런 차이는
  분류 단계에서 보통 `where`나 `select_columns`로 드러납니다.
- **`ORDER BY` 규칙의 경계.** gold에 `ORDER BY`가 있으면 순서까지 비교하므로, 정렬 키가
  같은 행(동점)의 순서가 쿼리마다 달라지면 정답을 오답으로 볼 수 있습니다. 반대로
  `--mode bird`는 순서를 전혀 보지 않습니다.
- **열 순서와 열 개수에 엄격합니다.** BIRD와 같이 행을 튜플로 비교하므로 필요한 열을 모두
  맞게 반환해도 순서가 다르거나 열이 하나 더 많으면 오답입니다.
- **분류는 바깥쪽 SELECT를 기준으로 합니다.** 서브쿼리와 CTE 내부는 정규화된 SQL 텍스트로만
  비교하고, 최상위 `UNION`/`INTERSECT`/`EXCEPT`는 `other`로 분류합니다. 의미는 같지만
  형태가 다른 조건(`IN (...)`과 `OR`, `BETWEEN`과 `>= AND <=`)은 다른 조건으로 봅니다.
- **`syntax_error`와 `runtime_error`는 SQLite 오류 메시지로 구분합니다.** SQLite는 두 경우에
  같은 오류 코드를 쓰기 때문입니다.

## 구조

```text
t2s_eval/
  execution.py   SQLite 실행(읽기 전용, 타임아웃), 결과 비교 규칙, verdict
  taxonomy.py    sqlglot 기반 정규화 + 절별 diff → Tag(category, reason)
  evaluator.py   JSONL 로드, 쌍별 평가, 집계
  report.py      콘솔 요약, Markdown 리포트
  cli.py         python -m t2s_eval
demo/
  build_shop_db.py   합성 쇼핑몰 DB 생성 (5개 테이블, 결정적 시드)
  pairs.jsonl        질문·gold·pred 14쌍
  sample_report.md   위 데모의 전체 리포트
tests/               비교 규칙, 각 태그, 데모 end-to-end 테스트
```

데모 DB(`categories`, `products`, `customers`, `orders`, `order_items`)의 데이터는 모두
합성입니다. `random` 대신 작은 선형 합동 생성기를 써서 Python 버전과 관계없이 같은 데이터가
만들어집니다.

## 테스트

```bash
pip install -e ".[test]"
python -m pytest -q
```

## 라이선스

[MIT](LICENSE) © 2026 Taeksu Kim
