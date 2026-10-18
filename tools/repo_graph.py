"""リポジトリの SQL と yml から「あるべき姿」のグラフ（build/graph.json）を作る。

Snowflake には接続しない。各 .sql を sqlglot で解析して
  - オブジェクト（テーブル・ビュー・タスク等）とカラム
  - テーブル間の依存（どこから読んで、どこへ書くか。経由するタスク／プロシージャ）
  - カラム間の依存と変換式
を取り出す。

使い方:
    python tools/repo_graph.py            # build/graph.json を出力
    python tools/repo_graph.py --check    # 解析できなかった SQL があれば終了コード 1
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage as sqlglot_lineage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (BUILD_DIR, DIALECT, FOLDER_TO_TYPE, REPO_ROOT, SQL_DIR,  # noqa: E402
                    fqn, procedure_body, read_yaml, sql_hash, strip_comments, task_body, write_json)


@dataclass
class Obj:
    id: str
    db: str
    schema: str
    name: str
    type: str
    path: str
    sql: str
    meta: dict
    columns: list = field(default_factory=list)   # [{name, type, description}]
    schedule: str = ""
    target_lag: str = ""


@dataclass
class Write:
    """1つの文が行う書き込み。"""
    target: str
    reads: set
    via: str | None
    col_edges: list   # [(source_col_fqn, target_col_fqn, transform)]
    out_columns: list  # CTAS / VIEW の出力カラム名


warnings: list[str] = []


# ---------------------------------------------------------------- 読み込み

def load_objects(root: Path) -> dict[str, Obj]:
    objs: dict[str, Obj] = {}
    for path in sorted(root.glob("*/*/*/*.sql")):
        db, schema, folder = path.parts[-4:-1]
        type_ = FOLDER_TO_TYPE.get(folder)
        if not type_:
            warnings.append(f"{path.relative_to(REPO_ROOT)}: 不明なフォルダ '{folder}'")
            continue
        name = path.stem
        oid = fqn(db, schema, name)
        objs[oid] = Obj(id=oid, db=db.upper(), schema=schema.upper(), name=name.upper(), type=type_,
                        path=str(path.relative_to(REPO_ROOT)), sql=path.read_text(encoding="utf-8"),
                        meta=read_yaml(path.with_suffix(".yml")))
    return objs


# ---------------------------------------------------------------- 名前解決

def table_id(t: exp.Table, db: str, schema: str) -> str:
    return fqn(t.catalog or db, t.db or schema, t.name)


def cte_names(e: exp.Expression) -> set[str]:
    return {c.alias_or_name.upper() for c in e.find_all(exp.CTE)}


def read_tables(e: exp.Expression, db: str, schema: str, exclude: set[str] = frozenset()) -> set[str]:
    ctes = cte_names(e)
    out = set()
    for t in e.find_all(exp.Table):
        if not t.name or t.name.upper() in ctes:
            continue
        tid = table_id(t, db, schema)
        if tid not in exclude:
            out.add(tid)
    return out


def qualify_select(select: exp.Expression, db: str, schema: str) -> exp.Expression:
    """未修飾のテーブル名を DB.SCHEMA で補う（lineage のスキーマ照合のため）。"""
    ctes = cte_names(select)
    select = select.copy()
    for t in select.find_all(exp.Table):
        if t.name and t.name.upper() not in ctes:
            if not t.args.get("db"):
                t.set("db", exp.to_identifier(schema))
            if not t.args.get("catalog"):
                t.set("catalog", exp.to_identifier(db))
    return select


# ---------------------------------------------------------------- カラム依存

def transform_text(e: exp.Expression) -> str:
    """変換式の表示用文字列。単純な列参照なら空文字。"""
    inner = e.this if isinstance(e, exp.Alias) else e
    if isinstance(inner, exp.Column):
        return ""
    return inner.sql(DIALECT)


def select_lineage(select: exp.Expression, target: str, target_cols: list[str] | None,
                   schema_map: dict, db: str, schema: str) -> tuple[list, list]:
    """SELECT の各出力カラムがどのテーブル.カラムから来るかを返す。

    target_cols を渡すと、出力カラムを位置で対応付ける（INSERT INTO t (a,b) SELECT ...）。
    """
    select = qualify_select(select, db, schema)
    projections = select.selects if isinstance(select, exp.Query) else []
    out_names = [p.alias_or_name.upper() for p in projections]
    edges = []
    for i, proj in enumerate(projections):
        out = (target_cols[i] if target_cols and i < len(target_cols) else out_names[i])
        if out == "*":
            continue
        try:
            # 位置で取り出すため、別名の無い式にも一時的な名前を付ける
            name = proj.alias_or_name if proj.alias_or_name and proj.alias_or_name != "*" else None
            sel = select
            if not isinstance(proj, (exp.Alias, exp.Column)) or not name:
                sel = select.copy()
                sel.selects[i].replace(exp.alias_(sel.selects[i].copy(), f"__C{i}"))
                name = f"__C{i}"
            node = sqlglot_lineage(name, sel, schema=schema_map, dialect=DIALECT)
        except Exception as ex:  # noqa: BLE001 - 解析できない列は警告にとどめる
            warnings.append(f"{target}.{out}: カラム依存を解析できませんでした ({ex.__class__.__name__})")
            continue
        tf = transform_text(proj)
        for leaf in node.walk():
            if leaf.downstream or not isinstance(leaf.source, exp.Table):
                continue
            src_tbl = table_id(leaf.source, db, schema)
            src_col = leaf.name.split(".")[-1].strip('"').upper()
            if src_col == "*":
                continue
            edges.append((f"{src_tbl}.{src_col}", f"{target}.{out}", tf))
    return dedupe(edges), out_names


def dedupe(items: list) -> list:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def merge_lineage(m: exp.Merge, target: str, target_cols: list[str], db: str, schema: str) -> list:
    using = m.args.get("using")
    alias_map: dict[str, str] = {}
    if isinstance(using, exp.Table):
        alias_map[(using.alias or using.name).upper()] = table_id(using, db, schema)
    tgt_alias = (m.this.alias or m.this.name).upper()
    edges = []

    def add(expr: exp.Expression, tcol: str):
        tf = transform_text(expr)
        for c in expr.find_all(exp.Column):
            src = alias_map.get((c.table or "").upper())
            if not src and not c.table and len(alias_map) == 1:
                src = next(iter(alias_map.values()))
            if src and (c.table or "").upper() != tgt_alias:
                edges.append((f"{src}.{c.name.upper()}", f"{target}.{tcol.upper()}", tf))

    whens = m.args.get("whens")
    for when in (whens.expressions if whens else []):
        then = when.args.get("then")
        if isinstance(then, exp.Update):
            for eq in then.expressions:
                add(eq.expression, eq.this.name)
        elif isinstance(then, exp.Insert):
            cols = [c.name.upper() for c in then.this.expressions] if isinstance(then.this, exp.Tuple) else target_cols
            vals = then.expression.expressions if isinstance(then.expression, exp.Tuple) else []
            for col, val in zip(cols, vals):
                add(val, col)
    return dedupe(edges)


# ---------------------------------------------------------------- 文の解析

def analyze_statement(sql: str, db: str, schema: str, via: str | None,
                      objs: dict[str, Obj], schema_map: dict, src: str) -> list[Write]:
    try:
        e = sqlglot.parse_one(sql, read=DIALECT)
    except sqlglot.errors.ParseError as ex:
        warnings.append(f"{src}: SQL を解析できませんでした: {str(ex).splitlines()[0]}")
        return []
    if e is None or isinstance(e, exp.Command):
        return []

    def cols_of(tid):
        o = objs.get(tid)
        return [c["name"] for c in o.columns] if o else []

    if isinstance(e, exp.Create) and isinstance(e.expression, exp.Query):
        target = table_id(e.this if isinstance(e.this, exp.Table) else e.this.this, db, schema)
        edges, outs = select_lineage(e.expression, target, None, schema_map, db, schema)
        return [Write(target, read_tables(e.expression, db, schema, {target}), via, edges, outs)]

    if isinstance(e, exp.Insert):
        tgt = e.this
        cols = None
        if isinstance(tgt, exp.Schema):
            cols = [c.name.upper() for c in tgt.expressions]
            tgt = tgt.this
        target = table_id(tgt, db, schema)
        cols = cols or cols_of(target)
        edges, _ = select_lineage(e.expression, target, cols, schema_map, db, schema) if e.expression else ([], [])
        return [Write(target, read_tables(e.expression, db, schema, {target}) if e.expression else set(), via, edges, [])]

    if isinstance(e, exp.Merge):
        target = table_id(e.this, db, schema)
        edges = merge_lineage(e, target, cols_of(target), db, schema)
        return [Write(target, read_tables(e, db, schema, {target}), via, edges, [])]

    if isinstance(e, (exp.Update, exp.Delete)):
        target = table_id(e.this, db, schema)
        return [Write(target, read_tables(e, db, schema, {target}), via, [], [])]
    return []


CALL_RE = re.compile(r"\bCALL\s+([\w$.\"]+)\s*\(", re.I)
SCHEDULE_RE = re.compile(r"\bSCHEDULE\s*=\s*'([^']+)'", re.I)
TARGET_LAG_RE = re.compile(r"\bTARGET_LAG\s*=\s*'([^']+)'", re.I)
STREAM_ON_RE = re.compile(r"\bON\s+(?:TABLE|VIEW)\s+([\w$.\"]+)", re.I)
AFTER_RE = re.compile(r"\bAFTER\s+([\w$.\", ]+?)\s+(?:WHEN|AS)\b", re.I)


def split_statements(body: str) -> list[str]:
    return [s.strip() for s in body.split(";") if s.strip()]


def resolve_name(name: str, db: str, schema: str) -> str:
    parts = [p.strip('"') for p in name.split(".")]
    while len(parts) < 3:
        parts.insert(0, schema if len(parts) == 1 else db)
    return fqn(*parts)


# ---------------------------------------------------------------- メイン

def ddl_columns(o: Obj) -> list:
    try:
        e = sqlglot.parse_one(strip_comments(o.sql), read=DIALECT)
    except sqlglot.errors.ParseError:
        return []
    if not isinstance(e, exp.Create) or not isinstance(e.this, exp.Schema):
        return []
    cols = []
    for cd in e.this.expressions:
        if not isinstance(cd, exp.ColumnDef):
            continue
        comment = next((c.kind.this.name for c in cd.constraints
                        if isinstance(c.kind, exp.CommentColumnConstraint)), "")
        cols.append({"name": cd.name.upper(), "type": cd.args["kind"].sql(DIALECT) if cd.args.get("kind") else "",
                     "description": comment})
    return cols


def build(root: Path) -> dict:
    objs = load_objects(root)

    # 1) DDL からカラム定義を取得（lineage のスキーマとしても使う）
    for o in objs.values():
        o.columns = ddl_columns(o)
        m = SCHEDULE_RE.search(o.sql)
        o.schedule = m.group(1) if m else ""
        m = TARGET_LAG_RE.search(o.sql)
        o.target_lag = m.group(1) if m else ""

    def schema_map():
        sm: dict = {}
        for o in objs.values():
            if o.columns:
                sm.setdefault(o.db, {}).setdefault(o.schema, {})[o.name] = {c["name"]: c["type"] or "VARCHAR" for c in o.columns}
        return sm

    # 2) 各オブジェクトの SQL を解析。CTAS/VIEW の出力カラムを先に確定させるため2周する
    writes: list[Write] = []
    calls: list[tuple[str, str]] = []
    for pass_no in (1, 2):
        writes, calls = [], []
        sm = schema_map()
        for o in objs.values():
            src = o.path
            text = strip_comments(o.sql)
            if o.type == "task":
                body = task_body(o.sql)
                for c in CALL_RE.findall(body):
                    calls.append((o.id, resolve_name(c, o.db, o.schema)))
                for st in split_statements(body):
                    if not st.upper().startswith("CALL"):
                        writes += analyze_statement(st, o.db, o.schema, o.id, objs, sm, src)
                for pred in AFTER_RE.findall(text):
                    for p in pred.split(","):
                        calls.append((resolve_name(p.strip(), o.db, o.schema), o.id))
            elif o.type == "procedure":
                body = re.sub(r"\b(BEGIN|END)\b\s*;?", ";", strip_comments(procedure_body(o.sql)), flags=re.I)
                for st in split_statements(body):
                    if re.match(r"(RETURN|LET|IF|FOR|DECLARE|ELSE|END)\b", st, re.I):
                        continue
                    for c in CALL_RE.findall(st):
                        calls.append((o.id, resolve_name(c, o.db, o.schema)))
                    if not st.upper().startswith("CALL"):
                        writes += analyze_statement(st, o.db, o.schema, o.id, objs, sm, src)
            elif o.type == "stream":
                m = STREAM_ON_RE.search(text)
                if m:
                    base = resolve_name(m.group(1), o.db, o.schema)
                    base_cols = [c["name"] for c in objs[base].columns] if base in objs else []
                    writes.append(Write(o.id, {base}, None,
                                        [(f"{base}.{c}", f"{o.id}.{c}", "") for c in base_cols], base_cols))
            else:
                for st in split_statements(text):
                    via = (o.meta.get("meta") or {}).get("refresh") if o.type == "table" else None
                    writes += analyze_statement(st, o.db, o.schema, via, objs, sm, src)

        # CTAS / VIEW / ストリームの出力カラムを、DDL にカラム定義が無いオブジェクトに反映
        for w in writes:
            o = objs.get(w.target)
            if o and not o.columns and w.out_columns:
                o.columns = [{"name": c, "type": "", "description": ""} for c in w.out_columns if c != "*"]
        if pass_no == 1:
            warnings.clear()

    # 3) yml のカラム説明をマージ
    for o in objs.values():
        ymlcols = {c["name"].upper(): c for c in (o.meta.get("columns") or [])}
        for c in o.columns:
            y = ymlcols.get(c["name"])
            if y and y.get("description"):
                c["description"] = y["description"]
            if y and y.get("tests"):
                c["tests"] = y["tests"]

    # 4) 組み立て
    edges: dict[tuple, dict] = {}
    col_edges = []
    for w in writes:
        for r in w.reads:
            key = (r, w.target)
            e = edges.setdefault(key, {"source": r, "target": w.target, "via": w.via, "evidence": ["repo"]})
            e["via"] = e["via"] or w.via
        col_edges += [{"source": s, "target": t, "transform": tf} for s, t, tf in w.col_edges]

    nodes = []
    for o in objs.values():
        m = o.meta
        nodes.append({
            "id": o.id, "db": o.db, "schema": o.schema, "name": o.name, "type": o.type, "path": o.path,
            "sql": o.sql, "sql_hash": sql_hash(o.sql),
            "description": m.get("description", ""), "owner": m.get("owner", ""), "tags": m.get("tags") or [],
            "meta": m.get("meta") or {}, "schedule": o.schedule, "target_lag": o.target_lag, "columns": o.columns,
        })
    known = set(objs)
    for e in edges.values():
        for side in ("source", "target"):
            if e[side] not in known:
                warnings.append(f"{e[side]}: 参照されていますがリポジトリに定義がありません")
                known.add(e[side])

    databases = {}
    for d in sorted(root.glob("*/_database.yml")):
        y = read_yaml(d)
        databases[d.parent.name.upper()] = {"description": y.get("description", ""), "owner": y.get("owner", "")}

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "commit": git_commit(),
        "databases": databases,
        "nodes": nodes,
        "edges": list(edges.values()),
        "calls": [{"source": a, "target": b} for a, b in dedupe(calls)],
        "column_edges": dedupe_dicts(col_edges),
        "warnings": dedupe(warnings),
    }


def dedupe_dicts(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for x in items:
        k = tuple(sorted(x.items()))
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=SQL_DIR)
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "graph.json")
    ap.add_argument("--check", action="store_true", help="警告があれば終了コード 1")
    args = ap.parse_args()
    g = build(args.root)
    write_json(args.out, g)
    print(f"{len(g['nodes'])} objects, {len(g['edges'])} edges, {len(g['column_edges'])} column edges -> {args.out}")
    for w in g["warnings"]:
        print("WARN", w, file=sys.stderr)
    return 1 if args.check and g["warnings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
