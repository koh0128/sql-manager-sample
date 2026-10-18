"""「あるべき姿」（graph.json）と「実際の姿」（state.json）を突き合わせ、
画面表示用の build/lineage.json と異常一覧の build/anomalies.json を作る。

state.json が無い場合はリポジトリの情報だけで lineage.json を作る（状態は「未取得」）。

使い方:
    python tools/compare.py                                  # build/state.json があれば使う
    python tools/compare.py --state examples/state.sample.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (BUILD_DIR, REPO_ROOT, SQL_DIR_NAME, TYPE_TO_FOLDER, humanize_schedule, match_any, normalize_sql,  # noqa: E402
                    procedure_body, read_json, read_yaml, task_body, write_json)

PROC_TYPES = ("task", "procedure")
KIND_LABEL = {
    "task_failed": "タスク失敗",
    "suspended": "タスク停止中",
    "drift": "手動変更",
    "manual_ddl": "手動デプロイ",
    "unregistered": "GitHub未登録",
    "undeployed": "未デプロイ",
    "hidden_flow": "コード外の流れ",
}


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    return dt.datetime.fromisoformat(s)


def unified_diff(repo_sql: str, sf_sql: str) -> str:
    return "\n".join(difflib.unified_diff(repo_sql.strip().splitlines(), sf_sql.strip().splitlines(),
                                          "GitHub", "Snowflake", lineterm=""))


def comparable(node: dict) -> tuple[str, str] | None:
    """ドリフト比較に使う (リポジトリ側, Snowflake側のキー)。"""
    t = node["type"]
    if t == "task":
        return task_body(node["sql"]), "body"
    if t == "procedure":
        return procedure_body(node["sql"]), "body"
    if t in ("view", "dynamic_table", "stream"):
        return node["sql"], "ddl"
    return None


def build(graph: dict, state: dict | None, cfg: dict) -> tuple[dict, list]:
    nodes = {n["id"]: n for n in graph["nodes"]}
    st_objs = (state or {}).get("objects", {})
    st_tasks = (state or {}).get("tasks", {})
    synced = parse_ts((state or {}).get("synced_at"))
    ignore = cfg.get("ignore_objects") or []
    anomalies: list[dict] = []

    def anomaly(obj: str, kind: str, level: str, title: str, **detail):
        a = {"key": f"{kind}:{obj}", "object": obj, "kind": kind, "label": KIND_LABEL[kind], "level": level,
             "title": title, **detail}
        anomalies.append(a)
        return a

    # ---- エッジ（リポジトリ由来 + Snowflake 観測）
    edges: dict[tuple, dict] = {}
    for e in graph["edges"]:
        edges[(e["source"], e["target"])] = {"source": e["source"], "target": e["target"], "via": e["via"], "ev": {"s"}}
    for d in (state or {}).get("dependencies", []):
        edges.setdefault((d["source"], d["target"]), {"source": d["source"], "target": d["target"], "via": None, "ev": set()})["ev"].add("d")
    for a in (state or {}).get("access_edges", []):
        key = (a["source"], a["target"])
        if key not in edges:
            edges[key] = {"source": a["source"], "target": a["target"], "via": None, "ev": set()}
            if state and not match_any(a["target"], ignore):
                anomaly(a["target"], "hidden_flow", "warn",
                        f"{a['source']} → {a['target']} の読み書きがありますが、リポジトリの SQL にありません",
                        source=a["source"], at=a.get("last_at"))
        edges[key]["ev"].add("a")
        edges[key]["last_at"] = a.get("last_at")

    # ---- Snowflake にだけあるオブジェクト（GitHub 未登録）
    for oid, so in st_objs.items():
        if oid in nodes or match_any(oid, ignore):
            continue
        db, schema, name = oid.split(".")
        nodes[oid] = {"id": oid, "db": db, "schema": schema, "name": name, "type": so["type"], "path": "",
                      "sql": so.get("ddl") or so.get("body") or "", "description": "", "owner": "", "tags": [],
                      "meta": {}, "schedule": "", "target_lag": "",
                      "columns": [{"name": c[0], "type": c[1], "description": c[2]} for c in so.get("columns", [])],
                      "in_repo": False}
        anomaly(oid, "unregistered", "warn", "Snowflake 上にだけ存在し、リポジトリに SQL がありません",
                suggest_path=f"{SQL_DIR_NAME}/{db}/{schema}/{TYPE_TO_FOLDER.get(so['type'], so['type'] + 's')}/{name}.sql")

    # ---- 書き込み元（経由するタスク／プロシージャ）とタスクの呼び出し
    writers: dict[str, set] = {}
    for e in edges.values():
        if e["via"] in nodes:
            writers.setdefault(e["target"], set()).add(e["via"])
    callers: dict[str, set] = {}
    for c in graph["calls"]:
        callers.setdefault(c["target"], set()).add(c["source"])

    # ---- 状態判定
    status: dict[str, str] = {}
    error: dict[str, str] = {}
    last_run: dict[str, str] = {}
    for oid, n in nodes.items():
        if not state:
            status[oid] = "unknown"
            continue
        if oid not in st_objs:
            status[oid] = "unknown"
            if n.get("in_repo", True):
                anomaly(oid, "undeployed", "info", "リポジトリにありますが Snowflake にありません（未デプロイ）")
            continue
        status[oid] = "ok"
        if n["type"] == "task" and oid in st_tasks:
            t = st_tasks[oid]
            lr = t.get("last_run") or {}
            last_run[oid] = lr.get("at")
            if lr.get("status") == "FAILED":
                status[oid] = "failed"
                error[oid] = lr.get("error", "")
                anomaly(oid, "task_failed", "error", f"最終実行（{lr.get('at')}）が失敗しました", error=lr.get("error", ""))
            if t.get("state") == "suspended":
                anomaly(oid, "suspended", "warn", "タスクが SUSPENDED のため実行されていません")
    for oid, n in nodes.items():  # プロシージャは呼び出し元タスクの成否を引き継ぐ
        if n["type"] == "procedure":
            failed = [c for c in callers.get(oid, ()) if status.get(c) == "failed"]
            if failed:
                status[oid] = "failed"
                error[oid] = error.get(failed[0], "")
                last_run[oid] = last_run.get(failed[0])
            elif callers.get(oid):
                last_run[oid] = max((last_run.get(c) or "" for c in callers[oid]), default=None)

    # 書き込み元が失敗 → 更新遅延。下流へも伝播させる
    downstream: dict[str, set] = {}
    for e in edges.values():
        downstream.setdefault(e["source"], set()).add(e["target"])
    stack = [oid for oid in nodes if any(status.get(w) == "failed" for w in writers.get(oid, ()))]
    seen = set()
    while stack:
        oid = stack.pop()
        if oid in seen:
            continue
        seen.add(oid)
        if status.get(oid) == "ok":
            status[oid] = "delayed"
        stack += [d for d in downstream.get(oid, ()) if nodes.get(d, {}).get("type") not in PROC_TYPES]

    # ---- 鮮度（ビューは最も古い上流のデータ時刻）
    upstream: dict[str, list] = {}
    for e in edges.values():
        upstream.setdefault(e["target"], []).append(e["source"])

    def fresh(oid: str, guard=frozenset()) -> dt.datetime | None:
        n = nodes.get(oid)
        if not n or oid in guard:
            return None
        if n["type"] in PROC_TYPES:
            return parse_ts(last_run.get(oid))
        if n["type"] == "view":
            ts = [t for t in (fresh(u, guard | {oid}) for u in upstream.get(oid, [])) if t]
            return min(ts) if ts else None
        return parse_ts(st_objs.get(oid, {}).get("last_altered"))

    stale_days = cfg.get("stale_days", 30)
    for oid, n in nodes.items():
        so = st_objs.get(oid)
        if not so or status[oid] != "ok" or n["type"] in PROC_TYPES or not synced:
            continue
        ref = parse_ts(so.get("last_read")) if n["type"] == "view" else parse_ts(so.get("last_altered"))
        if ref and (synced - ref).days >= stale_days and not so.get("reads_30d"):
            status[oid] = "stale"

    # ---- ドリフト（手動変更）
    drift: dict[str, str] = {}
    for oid, n in nodes.items():
        so = st_objs.get(oid)
        if not so or not n.get("in_repo", True):
            continue
        cmp = comparable(n)
        if cmp and so.get(cmp[1]):
            if normalize_sql(cmp[0]) != normalize_sql(so[cmp[1]]):
                drift[oid] = unified_diff(cmp[0], so[cmp[1]])
        elif n["type"] == "table" and so.get("columns"):
            repo_cols = [c["name"] for c in n["columns"]]
            sf_cols = [c[0].upper() for c in so["columns"]]
            if repo_cols and repo_cols != sf_cols:
                drift[oid] = unified_diff("\n".join(repo_cols), "\n".join(sf_cols))
    for oid, diff in drift.items():
        anomaly(oid, "drift", "warn", "Snowflake の定義がリポジトリと異なります（GitHub を通さずに変更された可能性）", diff=diff)

    # ---- 手動デプロイ（許可リスト外の DDL 実行）
    deploy_users = {u.upper() for u in cfg.get("deploy_users") or []}
    strict = cfg.get("mode") == "strict"
    for h in (state or {}).get("ddl_history", []):
        user = h["user"].upper()
        if user in deploy_users or match_any(h["object"], ignore):
            continue
        allowed = any(user == a["user"].upper() and match_any(h["object"], a.get("objects") or ["*"])
                      for a in cfg.get("allowed_ddl") or [])
        if allowed and not strict:
            continue
        anomaly(h["object"], "manual_ddl", "error" if strict else "warn",
                f"{h['user']}（{h['role']}）が {h['at']} に {h['query_type']} を実行しました",
                user=h["user"], role=h["role"], at=h["at"], query_type=h["query_type"], sql=h.get("query_text", ""))

    # ---- 表示用に組み立て
    by_obj: dict[str, list] = {}
    for a in anomalies:
        by_obj.setdefault(a["object"], []).append(a)
    out_nodes = []
    for oid, n in nodes.items():
        so = st_objs.get(oid, {})
        meta = n.get("meta") or {}
        writer_tasks = sorted(writers.get(oid, ()))
        cad = (humanize_schedule(n.get("schedule")) or (f"{humanize_schedule(n['target_lag'])}" if n.get("target_lag") else "")
               or meta.get("schedule", ""))
        if not cad and writer_tasks:
            w = nodes[writer_tasks[0]]
            cad = humanize_schedule(w.get("schedule")) or next(
                (humanize_schedule(nodes[c].get("schedule")) for c in callers.get(w["id"], ()) if c in nodes), "")
        if not cad and n["type"] == "stream":
            cad = "随時"
        if n["type"] == "procedure" and not cad:
            cad = "タスクから呼出" if callers.get(oid) else ""
        refresh = meta.get("refresh") or (" → ".join(nodes[w]["name"] for w in writer_tasks) if writer_tasks else "")
        if n["type"] == "dynamic_table" and n.get("target_lag"):
            refresh = refresh or f"TARGET_LAG = '{n['target_lag']}'"
        cols = n["columns"]
        sf_cols = {c[0].upper(): c for c in so.get("columns", [])}
        f = fresh(oid) if state and (so or n["type"] in PROC_TYPES) else None
        out_nodes.append({
            "id": oid, "db": n["db"], "schema": n["schema"], "name": n["name"], "type": n["type"],
            "path": n.get("path", ""), "inRepo": n.get("in_repo", True), "deployed": bool(so) if state else None,
            "desc": n.get("description", ""), "owner": n.get("owner", ""), "tags": n.get("tags", []),
            "status": status[oid], "error": error.get(oid, ""),
            "freshAt": f.isoformat() if f else None,
            "lastAltered": so.get("last_altered") or last_run.get(oid), "lastRead": so.get("last_read"),
            "rows": so.get("rows"), "reads": so.get("reads_30d") if state and so else None,
            "refresh": refresh, "cad": cad, "sla": meta.get("sla", ""),
            "cols": [[c["name"], c["type"] or (sf_cols.get(c["name"]) or [None, ""])[1], c.get("description", "")]
                     for c in cols],
            "sql": n["sql"], "anomalies": by_obj.get(oid, []),
        })
    out_edges = [[e["source"], e["target"], e["via"], "".join(sorted(e["ev"])), e.get("last_at")]
                 for e in edges.values()]
    lineage = {
        "generated_at": graph["generated_at"], "commit": graph.get("commit", ""),
        "synced_at": (state or {}).get("synced_at"), "account": (state or {}).get("account"),
        "databases": graph.get("databases", {}),
        "nodes": out_nodes, "edges": out_edges,
        "calls": [[c["source"], c["target"]] for c in graph["calls"]],
        "col_edges": [[c["source"], c["target"], c["transform"]] for c in graph["column_edges"]],
        "kind_labels": KIND_LABEL,
    }
    return lineage, anomalies


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", type=Path, default=BUILD_DIR / "graph.json")
    ap.add_argument("--state", type=Path, default=BUILD_DIR / "state.json")
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "monitor.yml")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "lineage.json")
    ap.add_argument("--anomalies", type=Path, default=BUILD_DIR / "anomalies.json")
    args = ap.parse_args()
    state = read_json(args.state) if args.state.exists() else None
    lineage, anomalies = build(read_json(args.graph), state, read_yaml(args.config))
    write_json(args.out, lineage)
    write_json(args.anomalies, anomalies)
    print(f"{len(lineage['nodes'])} objects, {len(anomalies)} anomalies"
          f" ({'state: ' + str(args.state) if state else 'Snowflake 状態なし'}) -> {args.out}")
    for a in anomalies:
        print(f"  [{a['level']}] {a['label']}: {a['object']} — {a['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
