"""サンプルの Snowflake 情報（examples/state.sample.json）を作る。snapshot.py の出力と同じ形式。

使い方: python tools/repo_graph.py && python examples/make_sample_state.py .
（同期時刻 2026-10-18 06:40 JST の想定。V_DAILY_SALES_BY_PREF は未作成の想定で含めない）
"""
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
g = json.loads((repo / "build/graph.json").read_text())

J = "+09:00"
LAST = {"ORDERS_RAW": "2026-10-18T06:35", "ORDERS_RAW_STREAM": "2026-10-18T06:35", "ORDER_ITEMS_RAW": "2026-10-18T06:35",
        "PRODUCTS_RAW": "2026-10-18T02:00", "CUSTOMERS_RAW": "2026-10-18T02:00", "PREF_MASTER": "2025-04-01T09:00",
        "ACCOUNT": "2026-10-18T01:10", "OPPORTUNITY": "2026-10-18T01:10", "PAGE_VIEWS": "2026-10-18T06:00",
        "SESSIONS_LEGACY": "2026-06-02T04:00", "FCT_ORDERS": "2026-10-18T06:35", "DIM_CUSTOMER": "2026-10-18T02:20",
        "DIM_PRODUCT": "2026-10-18T02:05", "FCT_ORDER_ITEMS": "2026-10-18T03:01", "DIM_ACCOUNT": "2026-10-18T01:30",
        "FCT_PAGE_VIEWS": "2026-10-18T06:10", "FCT_SESSIONS_OLD": "2026-06-02T05:00", "DAILY_SALES": "2026-10-18T06:31"}
LAST_READ = {"V_CUSTOMER_LTV": "2026-10-18T05:12", "V_CATEGORY_SALES": "2026-10-18T04:40", "V_PIPELINE": "2026-10-17T18:02",
             "V_CHANNEL_CONVERSION": "2026-10-18T06:15", "V_SESSION_REPORT_OLD": "2026-06-30T09:00"}
ROWS = {"ORDERS_RAW": 1284331, "ORDER_ITEMS_RAW": 3902118, "PRODUCTS_RAW": 18420, "CUSTOMERS_RAW": 402775, "PREF_MASTER": 47,
        "ACCOUNT": 9812, "OPPORTUNITY": 41006, "PAGE_VIEWS": 88301552, "SESSIONS_LEGACY": 12055190, "FCT_ORDERS": 1284331,
        "DIM_CUSTOMER": 402775, "DIM_PRODUCT": 18420, "FCT_ORDER_ITEMS": 3902118, "DIM_ACCOUNT": 9812,
        "FCT_PAGE_VIEWS": 88301552, "FCT_SESSIONS_OLD": 12055190, "DAILY_SALES": 512330}
READS = {"ORDERS_RAW": 8640, "ORDERS_RAW_STREAM": 8640, "ORDER_ITEMS_RAW": 31, "PRODUCTS_RAW": 744, "CUSTOMERS_RAW": 30,
         "PREF_MASTER": 30, "ACCOUNT": 30, "OPPORTUNITY": 412, "PAGE_VIEWS": 30, "SESSIONS_LEGACY": 0, "FCT_ORDERS": 5120,
         "DIM_CUSTOMER": 3380, "DIM_PRODUCT": 1960, "FCT_ORDER_ITEMS": 880, "DIM_ACCOUNT": 402, "FCT_PAGE_VIEWS": 2210,
         "FCT_SESSIONS_OLD": 0, "DAILY_SALES": 14200, "V_CUSTOMER_LTV": 1830, "V_CATEGORY_SALES": 960, "V_PIPELINE": 2470,
         "V_CHANNEL_CONVERSION": 3105, "V_SESSION_REPORT_OLD": 0}

objects = {}
for n in g["nodes"]:
    if n["type"] not in ("table", "view", "dynamic_table", "stream") or n["name"] == "V_DAILY_SALES_BY_PREF":
        continue
    o = {"type": n["type"], "columns": [[c["name"], c["type"] or "VARCHAR"] for c in n["columns"]]}
    if n["name"] in LAST:
        o["last_altered"] = LAST[n["name"]] + J
    if n["name"] in ROWS:
        o["rows"] = ROWS[n["name"]]
    if n["name"] in READS:
        o["reads_30d"] = READS[n["name"]]
    if n["name"] in LAST_READ:
        o["last_read"] = LAST_READ[n["name"]] + J
    objects[n["id"]] = o

state = {"synced_at": "2026-10-18T06:40:00" + J, "account": "SAMPLE", "objects": objects}
out = repo / "examples/state.sample.json"
out.write_text(json.dumps(state, ensure_ascii=False, indent=2))
print("objects", len(objects), "->", out)
