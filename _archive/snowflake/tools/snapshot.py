"""Snowflake から参考情報を取得して build/state.json に保存する（読み取りのみ）。

取得するもの（config.yml の databases が対象）:
  - テーブル／ビュー／ダイナミックテーブルの行数・最終更新・カラム型（INFORMATION_SCHEMA）
  - 直近30日の参照回数と最終参照（ACCOUNT_USAGE.ACCESS_HISTORY。Enterprise Edition 以上）

必要な権限: 対象 DB の USAGE・REFERENCES、参照回数を取るなら SNOWFLAKE DB の IMPORTED PRIVILEGES。
ACCESS_HISTORY が使えない場合は --no-access-history（参照回数は「未取得」表示になる）。

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


def q_tables(db: str) -> str:
    return f"""
SELECT table_schema, table_name, table_type, is_dynamic, row_count, last_altered
FROM {db}.INFORMATION_SCHEMA.TABLES
WHERE table_schema <> 'INFORMATION_SCHEMA'"""


def q_columns(db: str) -> str:
    return f"""
SELECT table_schema, table_name, column_name, data_type, character_maximum_length, numeric_precision, numeric_scale
FROM {db}.INFORMATION_SCHEMA.COLUMNS
WHERE table_schema <> 'INFORMATION_SCHEMA'
ORDER BY table_schema, table_name, ordinal_position"""


def q_reads() -> str:
    return """
SELECT obj.value:objectName::string AS object_name, COUNT(DISTINCT ah.query_id) AS reads_30d,
       MAX(ah.query_start_time) AS last_read
FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah, LATERAL FLATTEN(ah.direct_objects_accessed) obj
WHERE ah.query_start_time >= DATEADD('day', -30, CURRENT_TIMESTAMP())
GROUP BY 1"""


def connect():
    import snowflake.connector  # 実行時のみ必要
    from cryptography.hazmat.primitives import serialization

    pem = os.environ.get("SNOWFLAKE_PRIVATE_KEY")
    if not pem and os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        pem = Path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]).expanduser().read_text()
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
    conn = connect()
    cur = conn.cursor()
    objects: dict[str, dict] = {}
    for db in dbs:
        for r in rows(cur, q_tables(db)):
            oid = fqn(db, r["table_schema"], r["table_name"])
            if match_any(oid, ignore):
                continue
            type_ = "view" if r["table_type"] == "VIEW" else "dynamic_table" if r.get("is_dynamic") == "YES" else "table"
            objects[oid] = {"type": type_, "columns": [], "rows": r["row_count"], "last_altered": iso(r["last_altered"])}
        for r in rows(cur, q_columns(db)):
            oid = fqn(db, r["table_schema"], r["table_name"])
            if oid in objects:
                objects[oid]["columns"].append([r["column_name"], col_type(r)])
    if use_access_history:
        for r in rows(cur, q_reads()):
            oid = (r["object_name"] or "").upper()
            if oid in objects:
                objects[oid]["reads_30d"] = r["reads_30d"]
                objects[oid]["last_read"] = iso(r["last_read"])
    conn.close()
    return {"synced_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds"),
            "account": os.environ.get("SNOWFLAKE_ACCOUNT", ""), "objects": objects}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "config.yml")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "state.json")
    ap.add_argument("--no-access-history", action="store_true", help="参照回数を取らない（Standard Edition 向け）")
    ap.add_argument("--print-sql", action="store_true", help="実行する SQL を表示して終了")
    args = ap.parse_args()
    cfg = read_yaml(args.config)
    if args.print_sql:
        db = (cfg.get("databases") or ["<DB>"])[0]
        for title, sql in [("tables", q_tables(db)), ("columns", q_columns(db)), ("reads", q_reads())]:
            print(f"-- {title}{sql};\n")
        return 0
    state = snapshot(cfg, not args.no_access_history)
    write_json(args.out, state)
    print(f"{len(state['objects'])} objects -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
