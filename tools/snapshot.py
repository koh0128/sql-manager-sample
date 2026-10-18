"""Snowflake の「実際の姿」を取得して build/state.json に保存する（読み取りのみ）。

取得するもの（monitor.yml の databases が対象）:
  objects       … テーブル／ビュー／ダイナミックテーブル／ストリーム／タスク／プロシージャの定義・カラム・行数・最終更新
  tasks         … 状態（started/suspended）とスケジュール、直近の実行結果（INFORMATION_SCHEMA.TASK_HISTORY）
  ddl_history   … 誰がいつ DDL を実行したか（ACCOUNT_USAGE.ACCESS_HISTORY.object_modified_by_ddl × QUERY_HISTORY）
  access_edges  … 実際に読んで書いた流れ（ACCESS_HISTORY.objects_modified の directSources）
  dependencies  … ビュー等の依存（ACCOUNT_USAGE.OBJECT_DEPENDENCIES）
  reads_30d / last_read … 直近30日の参照回数と最終参照（ACCESS_HISTORY.base_objects_accessed）

必要な権限: 対象 DB の USAGE・REFERENCES（MONITOR はタスク履歴用）、SNOWFLAKE DB の IMPORTED PRIVILEGES。
ACCESS_HISTORY は Enterprise Edition 以上。無い場合は --no-access-history で該当項目を省略する。

接続は環境変数（キーペア認証）:
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE,
  SNOWFLAKE_PRIVATE_KEY（PEM 本文）または SNOWFLAKE_PRIVATE_KEY_PATH, 任意で SNOWFLAKE_PRIVATE_KEY_PASSPHRASE

使い方:
    python tools/snapshot.py                 # build/state.json を出力
    python tools/snapshot.py --print-sql     # 実行する SQL を表示するだけ（接続しない）
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BUILD_DIR, REPO_ROOT, fqn, match_any, read_yaml, write_json  # noqa: E402

AU = "SNOWFLAKE.ACCOUNT_USAGE"


def q_tables(db: str) -> str:
    return f"""
SELECT t.table_schema, t.table_name, t.table_type, t.is_dynamic, t.row_count, t.last_altered, v.view_definition
FROM {db}.INFORMATION_SCHEMA.TABLES t
LEFT JOIN {db}.INFORMATION_SCHEMA.VIEWS v
  ON v.table_schema = t.table_schema AND v.table_name = t.table_name
WHERE t.table_schema <> 'INFORMATION_SCHEMA'"""


def q_columns(db: str) -> str:
    return f"""
SELECT table_schema, table_name, column_name, data_type, character_maximum_length, numeric_precision, numeric_scale, comment
FROM {db}.INFORMATION_SCHEMA.COLUMNS
WHERE table_schema <> 'INFORMATION_SCHEMA'
ORDER BY table_schema, table_name, ordinal_position"""


def q_procedures(db: str) -> str:
    return f"""
SELECT procedure_schema, procedure_name, procedure_definition, last_altered
FROM {db}.INFORMATION_SCHEMA.PROCEDURES"""


def q_task_history(db: str) -> str:
    return f"""
SELECT database_name, schema_name, name, state, scheduled_time, completed_time, error_message
FROM TABLE({db}.INFORMATION_SCHEMA.TASK_HISTORY(
    SCHEDULED_TIME_RANGE_START => DATEADD('day', -7, CURRENT_TIMESTAMP()), RESULT_LIMIT => 10000))
WHERE state IN ('SUCCEEDED', 'FAILED')
QUALIFY ROW_NUMBER() OVER (PARTITION BY database_name, schema_name, name ORDER BY scheduled_time DESC) = 1"""


def q_ddl_history(days: int) -> str:
    return f"""
SELECT ah.object_modified_by_ddl:objectName::string AS object_name,
       ah.object_modified_by_ddl:objectDomain::string AS object_domain,
       qh.start_time, qh.user_name, qh.role_name, qh.query_type, LEFT(qh.query_text, 20000) AS query_text
FROM {AU}.ACCESS_HISTORY ah
JOIN {AU}.QUERY_HISTORY qh ON qh.query_id = ah.query_id
WHERE ah.query_start_time >= DATEADD('day', -{days}, CURRENT_TIMESTAMP())
  AND ah.object_modified_by_ddl IS NOT NULL
  AND qh.execution_status = 'SUCCESS'"""


def q_access_edges(days: int) -> str:
    return f"""
SELECT src.value:objectName::string AS source, om.value:objectName::string AS target,
       MAX(ah.query_start_time) AS last_at
FROM {AU}.ACCESS_HISTORY ah,
     LATERAL FLATTEN(ah.objects_modified) om,
     LATERAL FLATTEN(om.value:columns) col,
     LATERAL FLATTEN(col.value:directSources) src
WHERE ah.query_start_time >= DATEADD('day', -{days}, CURRENT_TIMESTAMP())
  AND src.value:objectName IS NOT NULL
GROUP BY 1, 2"""


def q_reads() -> str:
    return f"""
SELECT bo.value:objectName::string AS object_name, COUNT(DISTINCT ah.query_id) AS reads_30d,
       MAX(ah.query_start_time) AS last_read
FROM {AU}.ACCESS_HISTORY ah, LATERAL FLATTEN(ah.direct_objects_accessed) bo
WHERE ah.query_start_time >= DATEADD('day', -30, CURRENT_TIMESTAMP())
GROUP BY 1"""


def q_dependencies() -> str:
    return f"""
SELECT referenced_database || '.' || referenced_schema || '.' || referenced_object_name AS source,
       referencing_database || '.' || referencing_schema || '.' || referencing_object_name AS target
FROM {AU}.OBJECT_DEPENDENCIES"""


def connect():
    import snowflake.connector  # 実行時のみ必要
    from cryptography.hazmat.primitives import serialization

    pem = os.environ.get("SNOWFLAKE_PRIVATE_KEY")
    if not pem and os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        pem = Path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]).read_text()
    if not pem:
        raise SystemExit("SNOWFLAKE_PRIVATE_KEY か SNOWFLAKE_PRIVATE_KEY_PATH を設定してください")
    pw = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    key = serialization.load_pem_private_key(pem.encode(), password=pw.encode() if pw else None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"], private_key=der,
        role=os.environ.get("SNOWFLAKE_ROLE"), warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
        session_parameters={"QUERY_TAG": "sql-management/snapshot", "TIMEZONE": "Asia/Tokyo"})


def rows(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [c[0].lower() for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def iso(v) -> str | None:
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


def col_type(r: dict) -> str:
    t = r["data_type"]
    if t == "NUMBER" and r.get("numeric_precision") is not None:
        return f"NUMBER({r['numeric_precision']},{r['numeric_scale']})"
    if t == "TEXT":
        return f"VARCHAR({r['character_maximum_length']})" if r.get("character_maximum_length") else "VARCHAR"
    return t


def snapshot(cfg: dict, use_access_history: bool) -> dict:
    dbs = [d.upper() for d in cfg.get("databases") or []]
    ignore = cfg.get("ignore_objects") or []
    days = cfg.get("lookback_days", 7)
    in_scope = lambda oid: oid.split(".")[0] in dbs and not match_any(oid, ignore)  # noqa: E731

    conn = connect()
    cur = conn.cursor()
    objects: dict[str, dict] = {}
    tasks: dict[str, dict] = {}
    for db in dbs:
        for r in rows(cur, q_tables(db)):
            oid = fqn(db, r["table_schema"], r["table_name"])
            type_ = "view" if r["table_type"] == "VIEW" else "dynamic_table" if r.get("is_dynamic") == "YES" else "table"
            o = {"type": type_, "columns": [], "rows": r["row_count"], "last_altered": iso(r["last_altered"])}
            if r.get("view_definition"):
                o["ddl"] = r["view_definition"]
            objects[oid] = o
        for r in rows(cur, q_columns(db)):
            oid = fqn(db, r["table_schema"], r["table_name"])
            if oid in objects:
                objects[oid]["columns"].append([r["column_name"], col_type(r), r.get("comment") or ""])
        for r in rows(cur, f"SHOW DYNAMIC TABLES IN DATABASE {db}"):
            oid = fqn(db, r["schema_name"], r["name"])
            if oid in objects:
                objects[oid]["ddl"] = r.get("text")
        for r in rows(cur, f"SHOW STREAMS IN DATABASE {db}"):
            oid = fqn(db, r["schema_name"], r["name"])
            objects[oid] = {"type": "stream", "columns": [], "base": r.get("table_name"),
                            "last_altered": iso(r.get("created_on"))}
        for r in rows(cur, f"SHOW TASKS IN DATABASE {db}"):
            oid = fqn(db, r["schema_name"], r["name"])
            objects[oid] = {"type": "task", "columns": [], "body": r.get("definition")}
            tasks[oid] = {"state": (r.get("state") or "").lower(), "schedule": r.get("schedule"),
                          "predecessors": r.get("predecessors")}
        for r in rows(cur, q_task_history(db)):
            oid = fqn(r["database_name"], r["schema_name"], r["name"])
            if oid in tasks:
                tasks[oid]["last_run"] = {"at": iso(r["completed_time"] or r["scheduled_time"]), "status": r["state"],
                                          "error": r.get("error_message") or ""}
        for r in rows(cur, q_procedures(db)):
            oid = fqn(db, r["procedure_schema"], r["procedure_name"])
            objects[oid] = {"type": "procedure", "columns": [], "body": r["procedure_definition"],
                            "last_altered": iso(r["last_altered"])}

    dependencies = [r for r in rows(cur, q_dependencies()) if in_scope(r["target"])]
    ddl_history, access_edges = [], []
    if use_access_history:
        for r in rows(cur, q_ddl_history(days)):
            oid = (r["object_name"] or "").upper()
            if in_scope(oid):
                ddl_history.append({"object": oid, "at": iso(r["start_time"]), "user": r["user_name"],
                                    "role": r["role_name"], "query_type": r["query_type"], "query_text": r["query_text"]})
        access_edges = [{"source": r["source"].upper(), "target": r["target"].upper(), "last_at": iso(r["last_at"])}
                        for r in rows(cur, q_access_edges(days)) if in_scope(r["target"].upper())]
        for r in rows(cur, q_reads()):
            oid = (r["object_name"] or "").upper()
            if oid in objects:
                objects[oid]["reads_30d"] = r["reads_30d"]
                objects[oid]["last_read"] = iso(r["last_read"])
    conn.close()
    return {"synced_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds"),
            "account": os.environ.get("SNOWFLAKE_ACCOUNT", ""), "objects": objects, "tasks": tasks,
            "ddl_history": ddl_history, "access_edges": access_edges, "dependencies": dependencies, "query_errors": []}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "monitor.yml")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "state.json")
    ap.add_argument("--no-access-history", action="store_true", help="Standard Edition 向け（ACCESS_HISTORY を使わない）")
    ap.add_argument("--print-sql", action="store_true", help="実行する SQL を表示して終了")
    args = ap.parse_args()
    cfg = read_yaml(args.config)
    if args.print_sql:
        db = (cfg.get("databases") or ["<DB>"])[0]
        days = cfg.get("lookback_days", 7)
        for title, sql in [("tables", q_tables(db)), ("columns", q_columns(db)), ("procedures", q_procedures(db)),
                           ("task_history", q_task_history(db)), ("ddl_history", q_ddl_history(days)),
                           ("access_edges", q_access_edges(days)), ("reads", q_reads()), ("dependencies", q_dependencies())]:
            print(f"-- {title}{sql};\n")
        return 0
    state = snapshot(cfg, not args.no_access_history)
    write_json(args.out, state)
    print(f"{len(state['objects'])} objects, {len(state['tasks'])} tasks, {len(state['ddl_history'])} DDL -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
