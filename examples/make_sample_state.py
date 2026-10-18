"""サンプルの Snowflake 状態（examples/state.sample.json）を作る。snapshot.py の出力と同じ形式。

使い方: python tools/repo_graph.py && python examples/make_sample_state.py .

シナリオ（同期時刻 2026-10-18 06:40 JST）:
- T_BUILD_ITEMS が失敗 → FCT_ORDER_ITEMS / V_CATEGORY_SALES が更新遅延
- V_PIPELINE を TANAKA が Snowsight から直接変更（手動変更＋手動デプロイ）
- CUSTOMER_SEGMENT を SUZUKI が手動 CTAS で作成（GitHub 未登録＋コード外の流れ）
- V_DAILY_SALES_BY_PREF はリポジトリにあるが未デプロイ
- SESSIONS_LEGACY 系は 30日以上更新・参照なし
- DIM_CUSTOMER の日次 CTAS は SVC_AIRFLOW（許可リスト）なので異常にしない
"""
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
g = json.loads((repo / "build/graph.json").read_text())
sys.path.insert(0, str(repo / "tools"))
from common import procedure_body, task_body  # noqa: E402

J = "+09:00"
LAST = {"ORDERS_RAW": "2026-10-18T06:35", "ORDERS_RAW_STREAM": "2026-10-18T06:35", "ORDER_ITEMS_RAW": "2026-10-18T06:35",
        "PRODUCTS_RAW": "2026-10-18T02:00", "CUSTOMERS_RAW": "2026-10-18T02:00", "PREF_MASTER": "2025-04-01T09:00",
        "ACCOUNT": "2026-10-18T01:10", "OPPORTUNITY": "2026-10-18T01:10", "PAGE_VIEWS": "2026-10-18T06:00",
        "SESSIONS_LEGACY": "2026-06-02T04:00", "FCT_ORDERS": "2026-10-18T06:35", "DIM_CUSTOMER": "2026-10-18T02:20",
        "DIM_PRODUCT": "2026-10-18T02:05", "FCT_ORDER_ITEMS": "2026-10-17T03:01", "DIM_ACCOUNT": "2026-10-18T01:30",
        "FCT_PAGE_VIEWS": "2026-10-18T06:10", "FCT_SESSIONS_OLD": "2026-06-02T05:00", "DAILY_SALES": "2026-10-18T06:31"}
LAST_READ = {"V_CUSTOMER_LTV": "2026-10-18T05:12", "V_CATEGORY_SALES": "2026-10-18T04:40", "V_PIPELINE": "2026-10-17T18:02",
             "V_CHANNEL_CONVERSION": "2026-10-18T06:15", "V_SESSION_REPORT_OLD": "2026-06-30T09:00"}
ROWS = {"ORDERS_RAW": 1284331, "ORDER_ITEMS_RAW": 3902118, "PRODUCTS_RAW": 18420, "CUSTOMERS_RAW": 402775, "PREF_MASTER": 47,
        "ACCOUNT": 9812, "OPPORTUNITY": 41006, "PAGE_VIEWS": 88301552, "SESSIONS_LEGACY": 12055190, "FCT_ORDERS": 1284331,
        "DIM_CUSTOMER": 402775, "DIM_PRODUCT": 18420, "FCT_ORDER_ITEMS": 3897240, "DIM_ACCOUNT": 9812,
        "FCT_PAGE_VIEWS": 88301552, "FCT_SESSIONS_OLD": 12055190, "DAILY_SALES": 512330}
READS = {"ORDERS_RAW": 8640, "ORDERS_RAW_STREAM": 8640, "ORDER_ITEMS_RAW": 31, "PRODUCTS_RAW": 744, "CUSTOMERS_RAW": 30,
         "PREF_MASTER": 30, "ACCOUNT": 30, "OPPORTUNITY": 412, "PAGE_VIEWS": 30, "SESSIONS_LEGACY": 0, "FCT_ORDERS": 5120,
         "DIM_CUSTOMER": 3380, "DIM_PRODUCT": 1960, "FCT_ORDER_ITEMS": 880, "DIM_ACCOUNT": 402, "FCT_PAGE_VIEWS": 2210,
         "FCT_SESSIONS_OLD": 0, "DAILY_SALES": 14200, "V_CUSTOMER_LTV": 1830, "V_CATEGORY_SALES": 960, "V_PIPELINE": 2470,
         "V_CHANNEL_CONVERSION": 3105, "V_SESSION_REPORT_OLD": 0, "CUSTOMER_SEGMENT": 6400}

PIPELINE_MANUAL = """create or replace view MART_DB.SALES.V_PIPELINE as
select
    a.ACCOUNT_NAME,
    o.STAGE,
    o.AMOUNT,
    date_trunc('month', o.CLOSE_DATE) as CLOSE_MONTH
from RAW_DB.SALESFORCE.OPPORTUNITY o
join DWH_DB.CORE.DIM_ACCOUNT a on a.ACCOUNT_ID = o.ACCOUNT_ID
where o.STAGE <> 'Closed Lost';"""
SEGMENT = """CREATE OR REPLACE TABLE MART_DB.MARKETING.CUSTOMER_SEGMENT AS
SELECT CUSTOMER_ID,
       CASE WHEN LTV_AMOUNT >= 100000 THEN 'VIP'
            WHEN LTV_AMOUNT >= 20000  THEN 'LOYAL'
            ELSE 'LIGHT' END AS SEGMENT
FROM MART_DB.SALES.V_CUSTOMER_LTV;"""

objects = {}
for n in g["nodes"]:
    if n["name"] == "V_DAILY_SALES_BY_PREF":
        continue  # 未デプロイ
    o = {"type": n["type"], "columns": [[c["name"], c["type"] or "VARCHAR", c.get("description", "")] for c in n["columns"]]}
    if n["type"] in ("view", "dynamic_table", "stream"):
        o["ddl"] = n["sql"]
    elif n["type"] == "task":
        o["body"] = task_body(n["sql"])
    elif n["type"] == "procedure":
        o["body"] = procedure_body(n["sql"])
    if n["name"] in LAST:
        o["last_altered"] = LAST[n["name"]] + J
    if n["name"] in LAST_READ:
        o["last_read"] = LAST_READ[n["name"]] + J
    if n["name"] in ROWS:
        o["rows"] = ROWS[n["name"]]
    if n["name"] in READS:
        o["reads_30d"] = READS[n["name"]]
    objects[n["id"]] = o
objects["MART_DB.SALES.V_PIPELINE"]["ddl"] = PIPELINE_MANUAL
objects["MART_DB.MARKETING.CUSTOMER_SEGMENT"] = {
    "type": "table", "columns": [["CUSTOMER_ID", "NUMBER(38,0)", ""], ["SEGMENT", "VARCHAR(5)", ""]],
    "ddl": SEGMENT, "rows": 402775, "last_altered": "2026-10-01T10:14" + J, "reads_30d": 6400}

tasks = {
    "DWH_DB.CORE.T_LOAD_ORDERS": {"state": "started", "schedule": "5 MINUTE",
                                  "last_run": {"at": "2026-10-18T06:35" + J, "status": "SUCCEEDED"}},
    "DWH_DB.CORE.T_BUILD_ITEMS": {"state": "started", "schedule": "USING CRON 0 3 * * * Asia/Tokyo",
                                  "last_run": {"at": "2026-10-18T03:00" + J, "status": "FAILED",
                                               "error": "SQL compilation error: error line 4 at position 23\ninvalid identifier 'I.UNIT_PRICE'"}},
    "MART_DB.SALES.T_DAILY_SALES": {"state": "started", "schedule": "USING CRON 30 6 * * * Asia/Tokyo",
                                    "last_run": {"at": "2026-10-18T06:30" + J, "status": "SUCCEEDED"}},
}

ddl_history = [
    {"object": "MART_DB.SALES.V_PIPELINE", "at": "2026-10-16T15:22" + J, "user": "TANAKA", "role": "SYSADMIN",
     "query_type": "CREATE_VIEW", "query_text": PIPELINE_MANUAL},
    {"object": "MART_DB.MARKETING.CUSTOMER_SEGMENT", "at": "2026-10-01T10:14" + J, "user": "SUZUKI", "role": "ANALYST",
     "query_type": "CREATE_TABLE_AS_SELECT", "query_text": SEGMENT},
    {"object": "DWH_DB.CORE.DIM_CUSTOMER", "at": "2026-10-18T02:20" + J, "user": "SVC_AIRFLOW", "role": "ETL",
     "query_type": "CREATE_TABLE_AS_SELECT", "query_text": "CREATE OR REPLACE TABLE DWH_DB.CORE.DIM_CUSTOMER AS ..."},
]

# ACCESS_HISTORY から得た実際の読み書き（テーブルへの書き込みのみ。ビューは OBJECT_DEPENDENCIES 側）
access = []
for e in g["edges"]:
    t = e["target"]
    tname = t.split(".")[-1]
    if objects.get(t, {}).get("type") in ("view",) or t not in objects:
        continue
    at = LAST.get(tname)
    if at:
        access.append({"source": e["source"], "target": t, "last_at": at + J})
access.append({"source": "MART_DB.SALES.V_CUSTOMER_LTV", "target": "MART_DB.MARKETING.CUSTOMER_SEGMENT", "last_at": "2026-10-01T10:14" + J})

deps = [{"source": e["source"], "target": e["target"]} for e in g["edges"]
        if objects.get(e["target"], {}).get("type") in ("view", "dynamic_table", "stream")]

state = {"synced_at": "2026-10-18T06:40:00" + J, "account": "SAMPLE", "objects": objects, "tasks": tasks,
         "ddl_history": ddl_history, "access_edges": access, "dependencies": deps, "query_errors": []}
out = repo / "examples/state.sample.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(state, ensure_ascii=False, indent=2))
print("objects", len(objects), "access", len(access), "deps", len(deps))
