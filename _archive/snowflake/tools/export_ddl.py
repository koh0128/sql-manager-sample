"""Snowflake の既存オブジェクトを sql/<DB>/<SCHEMA>/<種別>/<NAME>.sql に書き出す（初回ベースライン用）。

GET_DDL で定義を取り、同名の .yml（説明・オーナー等の雛形）も作る。既存ファイルは上書きしない（--overwrite で上書き）。
接続方法は snapshot.py と同じ環境変数。

使い方:
    python tools/export_ddl.py                       # config.yml の databases をすべて
    python tools/export_ddl.py --database DWH_DB --schema CORE
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO_ROOT, SQL_DIR, TYPE_TO_FOLDER, fqn, match_any, read_yaml  # noqa: E402
from snapshot import connect, rows  # noqa: E402

# (SHOW コマンド, GET_DDL の種別, 種別)
KINDS = [
    ("TABLES", "TABLE", "table"),
    ("VIEWS", "VIEW", "view"),
    ("DYNAMIC TABLES", "DYNAMIC_TABLE", "dynamic_table"),
    ("STREAMS", "STREAM", "stream"),
    ("TASKS", "TASK", "task"),
    ("PROCEDURES", "PROCEDURE", "procedure"),
]


def yml_skeleton(name: str, type_: str, comment: str, columns: list) -> str:
    meta = {"name": name, "type": type_, "description": comment or "", "owner": "", "tags": []}
    if columns:
        meta["columns"] = [{"name": c, "description": d or ""} for c, d in columns]
    return yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", action="append", help="対象 DB（複数可）。省略時は config.yml の databases")
    ap.add_argument("--schema", help="対象スキーマ（1つ）")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    cfg = read_yaml(REPO_ROOT / "config.yml")
    dbs = [d.upper() for d in (args.database or cfg.get("databases") or [])]
    ignore = cfg.get("ignore_objects") or []

    conn = connect()
    cur = conn.cursor()
    written = skipped = 0
    for db in dbs:
        scope = f"SCHEMA {db}.{args.schema}" if args.schema else f"DATABASE {db}"
        for show, ddl_kind, type_ in KINDS:
            for r in rows(cur, f"SHOW {show} IN {scope}"):
                schema = r["schema_name"]
                if schema == "INFORMATION_SCHEMA":
                    continue
                if show == "TABLES" and r.get("is_dynamic") == "Y":
                    continue  # DYNAMIC TABLES 側で扱う
                name = r["name"]
                oid = fqn(db, schema, name)
                if match_any(oid, ignore):
                    continue
                if type_ == "procedure":
                    # 引数の型が必要: arguments 列は "NAME(VARCHAR) RETURN ..." の形
                    sig = r["arguments"].split(" RETURN ")[0]
                    target = f"{db}.{schema}.{sig}"
                else:
                    target = oid
                cur.execute("SELECT GET_DDL(%s, %s, TRUE)", (ddl_kind, target))
                ddl = cur.fetchone()[0].strip()
                path = SQL_DIR / db / schema / TYPE_TO_FOLDER[type_] / f"{name.upper()}.sql"
                if path.exists() and not args.overwrite:
                    skipped += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(ddl + "\n", encoding="utf-8")
                cols = []
                if type_ in ("table", "view", "dynamic_table"):
                    cols = [(c["column_name"], c.get("comment")) for c in rows(cur,
                            f"SELECT column_name, comment FROM {db}.INFORMATION_SCHEMA.COLUMNS "
                            f"WHERE table_schema = '{schema}' AND table_name = '{name}' ORDER BY ordinal_position")]
                yml = path.with_suffix(".yml")
                if not yml.exists():
                    yml.write_text(yml_skeleton(name.upper(), type_, r.get("comment") or "", cols), encoding="utf-8")
                written += 1
        dbyml = SQL_DIR / db / "_database.yml"
        if not dbyml.exists() and dbyml.parent.exists():
            dbyml.write_text(yaml.safe_dump({"name": db, "description": "", "owner": ""}, allow_unicode=True,
                                            sort_keys=False), encoding="utf-8")
    conn.close()
    print(f"{written} files written, {skipped} skipped (already exist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
