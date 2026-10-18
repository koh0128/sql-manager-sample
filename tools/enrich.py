"""リポジトリから作ったグラフ（graph.json）に、Snowflake から取った参考情報（state.json）を付けて
画面表示用の build/lineage.json を作る。

付ける情報: 最終更新・更新頻度・データの鮮度（ビューは上流の最も古い更新時刻）・行数・カラム型・30日参照回数。
state.json が無い場合はリポジトリの情報だけで作る（画面では「未取得」表示）。

使い方:
    python tools/enrich.py                                   # build/state.json があれば使う
    python tools/enrich.py --state examples/state.sample.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BUILD_DIR, humanize_schedule, read_json, write_json  # noqa: E402

PROC_TYPES = ("task", "procedure")


def parse_ts(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


def build(graph: dict, state: dict | None) -> dict:
    nodes = {n["id"]: n for n in graph["nodes"]}
    st = (state or {}).get("objects", {})

    upstream: dict[str, list] = {}
    writers: dict[str, set] = {}
    for e in graph["edges"]:
        upstream.setdefault(e["target"], []).append(e["source"])
        if e["via"] in nodes:
            writers.setdefault(e["target"], set()).add(e["via"])
    callers: dict[str, set] = {}
    for c in graph["calls"]:
        callers.setdefault(c["target"], set()).add(c["source"])

    def fresh(oid: str, guard=frozenset()) -> dt.datetime | None:
        """データの鮮度。ビューは自身が更新されないので、上流のうち最も古い更新時刻。"""
        n = nodes.get(oid)
        if not n or oid in guard:
            return None
        if n["type"] == "view":
            ts = [t for t in (fresh(u, guard | {oid}) for u in upstream.get(oid, [])) if t]
            return min(ts) if ts else None
        return parse_ts(st.get(oid, {}).get("last_altered"))

    def schedule(n: dict) -> str:
        """更新頻度: タスクの SCHEDULE、ダイナミックテーブルの TARGET_LAG、yml の meta.schedule、書き込むタスクの SCHEDULE の順。"""
        s = humanize_schedule(n.get("schedule")) or humanize_schedule(n.get("target_lag")) or (n.get("meta") or {}).get("schedule", "")
        if s:
            return s
        for w in sorted(writers.get(n["id"], ())):
            s = humanize_schedule(nodes[w].get("schedule")) or next(
                (humanize_schedule(nodes[c].get("schedule")) for c in callers.get(w, ()) if c in nodes), "")
            if s:
                return s
        if n["type"] == "stream":
            return "随時"
        if n["type"] == "procedure" and callers.get(n["id"]):
            return "タスクから呼出"
        return ""

    out = []
    for oid, n in nodes.items():
        so = st.get(oid, {})
        meta = n.get("meta") or {}
        wr = sorted(writers.get(oid, ()))
        refresh = meta.get("refresh") or (" → ".join(nodes[w]["name"] for w in wr) if wr else "")
        if n["type"] == "dynamic_table" and n.get("target_lag"):
            refresh = refresh or f"TARGET_LAG = '{n['target_lag']}'"
        sf_types = {c[0].upper(): c[1] for c in so.get("columns", [])}
        f = fresh(oid) if so and n["type"] not in PROC_TYPES else None
        out.append({
            "id": oid, "db": n["db"], "schema": n["schema"], "name": n["name"], "type": n["type"], "path": n["path"],
            "desc": n.get("description", ""), "owner": n.get("owner", ""), "tags": n.get("tags", []),
            "refresh": refresh, "cad": schedule(n), "sla": meta.get("sla", ""),
            "freshAt": f.isoformat() if f else None,
            "lastAltered": so.get("last_altered"), "lastRead": so.get("last_read"),
            "rows": so.get("rows"), "reads": so.get("reads_30d") if so else None,
            "cols": [[c["name"], c["type"] or sf_types.get(c["name"], ""), c.get("description", "")] for c in n["columns"]],
            "sql": n["sql"],
        })
    return {
        "generated_at": graph["generated_at"], "commit": graph.get("commit", ""),
        "synced_at": (state or {}).get("synced_at"), "account": (state or {}).get("account"),
        "databases": graph.get("databases", {}),
        "nodes": out,
        "edges": [[e["source"], e["target"], e["via"]] for e in graph["edges"]],
        "calls": [[c["source"], c["target"]] for c in graph["calls"]],
        "col_edges": [[c["source"], c["target"], c["transform"]] for c in graph["column_edges"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", type=Path, default=BUILD_DIR / "graph.json")
    ap.add_argument("--state", type=Path, default=BUILD_DIR / "state.json")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "lineage.json")
    args = ap.parse_args()
    state = read_json(args.state) if args.state.exists() else None
    lineage = build(read_json(args.graph), state)
    write_json(args.out, lineage)
    print(f"{len(lineage['nodes'])} objects ({'state: ' + str(args.state) if state else 'Snowflake 情報なし'}) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
