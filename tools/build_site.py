"""リポジトリの SQL からグラフを作り、公開用サイト（build/site/）までを一度に行う。

使い方:
    python tools/build_site.py

ローカル確認:  python -m http.server -d build/site 8000  → http://localhost:8000
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_graph  # noqa: E402
from common import BUILD_DIR, REPO_ROOT, SQL_DIR, humanize_schedule, write_json  # noqa: E402


def repo_url() -> tuple[str, str]:
    """GitHub のファイルへのリンク用。Actions では環境変数、ローカルでは git remote から。"""
    if os.environ.get("GITHUB_REPOSITORY"):
        return (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}",
                os.environ.get("GITHUB_REF_NAME", "main"))
    try:
        url = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=REPO_ROOT, text=True,
                                      stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "", "main"
    if url.startswith("git@"):
        host, path = url[4:].split(":", 1)
        url = f"https://{host}/{path}"
    return url.removesuffix(".git"), "main"


def to_lineage(graph: dict) -> dict:
    """graph.json を画面表示用の lineage.json の形にする。"""
    nodes = {n["id"]: n for n in graph["nodes"]}

    writers: dict[str, set] = {}
    for e in graph["edges"]:
        if e["via"] in nodes:
            writers.setdefault(e["target"], set()).add(e["via"])
    callers: dict[str, set] = {}
    for c in graph["calls"]:
        callers.setdefault(c["target"], set()).add(c["source"])

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
        meta = n.get("meta") or {}
        wr = sorted(writers.get(oid, ()))
        refresh = meta.get("refresh") or (" → ".join(nodes[w]["name"] for w in wr) if wr else "")
        if n["type"] == "dynamic_table" and n.get("target_lag"):
            refresh = refresh or f"TARGET_LAG = '{n['target_lag']}'"
        out.append({
            "id": oid, "db": n["db"], "schema": n["schema"], "name": n["name"], "type": n["type"], "path": n["path"],
            "desc": n.get("description", ""), "owner": n.get("owner", ""), "tags": n.get("tags", []),
            "refresh": refresh, "cad": schedule(n), "sla": meta.get("sla", ""),
            "cols": [[c["name"], c["type"] or "", c.get("description", "")] for c in n["columns"]],
            "sql": n["sql"],
        })
    return {
        "generated_at": graph["generated_at"], "commit": graph.get("commit", ""),
        "databases": graph.get("databases", {}),
        "nodes": out,
        "edges": [[e["source"], e["target"], e["via"]] for e in graph["edges"]],
        "calls": [[c["source"], c["target"]] for c in graph["calls"]],
        "col_edges": [[c["source"], c["target"], c["transform"]] for c in graph["column_edges"]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "site")
    args = ap.parse_args()

    graph = repo_graph.build(SQL_DIR)
    write_json(BUILD_DIR / "graph.json", graph)
    lineage = to_lineage(graph)
    lineage["repo_url"], lineage["branch"] = repo_url()
    write_json(BUILD_DIR / "lineage.json", lineage)

    if args.out.exists():
        shutil.rmtree(args.out)
    shutil.copytree(REPO_ROOT / "site", args.out)
    shutil.copy(BUILD_DIR / "lineage.json", args.out / "lineage.json")
    (args.out / ".nojekyll").touch()

    print(f"site -> {args.out}  ({len(lineage['nodes'])} objects, {len(graph['warnings'])} warnings)")
    for w in graph["warnings"]:
        print("WARN", w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
