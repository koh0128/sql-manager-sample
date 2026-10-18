"""変更したファイルの下流への影響を Markdown で出力する（PR コメント用）。

ベースブランチと PR の両方でグラフを作り、変更オブジェクトについて
  - 下流のオブジェクト（全体）
  - 削除・名前変更されたカラムを使っている下流のカラム
を列挙する。

使い方:
    python tools/impact.py --base origin/main            # git diff の変更ファイルから
    python tools/impact.py --files sql/.../X.sql   # ファイル指定
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_graph  # noqa: E402
from common import REPO_ROOT, SQL_DIR, SQL_DIR_NAME, fqn  # noqa: E402


def changed_files(base: str) -> list[str]:
    out = subprocess.check_output(["git", "diff", "--name-only", f"{base}...HEAD", "--", f"{SQL_DIR_NAME}/"],
                                  cwd=REPO_ROOT, text=True)
    return [l for l in out.splitlines() if l.endswith((".sql", ".yml"))]


def graph_at(ref: str) -> dict:
    """指定コミットの sql/ でグラフを作る（git archive で一時展開）。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(["git", "archive", ref, SQL_DIR_NAME], cwd=REPO_ROOT, capture_output=True)
        if p.returncode != 0:
            return {"nodes": [], "edges": [], "column_edges": []}
        subprocess.run(["tar", "-x", "-C", tmp], input=p.stdout, check=True)
        return repo_graph.build(Path(tmp) / SQL_DIR_NAME)


def downstream(graph: dict, start: str) -> list[str]:
    nxt: dict[str, set] = {}
    for e in graph["edges"]:
        nxt.setdefault(e["source"], set()).add(e["target"])
    seen, stack = [], [start]
    while stack:
        for t in sorted(nxt.get(stack.pop(), ())):
            if t not in seen:
                seen.append(t)
                stack.append(t)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--files", nargs="*")
    args = ap.parse_args()

    files = args.files if args.files is not None else changed_files(args.base)
    objs = []
    for f in files:
        p = Path(f)
        if len(p.parts) >= 5 and p.parts[0] == SQL_DIR_NAME and not p.name.startswith("_"):
            objs.append(fqn(p.parts[1], p.parts[2], p.stem))
    objs = sorted(set(objs))
    if not objs:
        print("sql/ 配下の変更はありません。")
        return 0

    head = repo_graph.build(SQL_DIR)
    base = graph_at(args.base) if args.files is None else head
    head_cols = {n["id"]: {c["name"] for c in n["columns"]} for n in head["nodes"]}
    base_cols = {n["id"]: {c["name"] for c in n["columns"]} for n in base["nodes"]}

    lines = ["## 変更の影響範囲", "", "| 変更したオブジェクト | 下流（全体） |", "|---|---|"]
    col_warn = []
    for o in objs:
        ds = downstream(head, o) or downstream(base, o)
        lines.append(f"| `{o}` | {len(ds)} 件 " + (" ".join(f"`{d}`" for d in ds[:15]) + (" …" if len(ds) > 15 else "")) + " |")
        removed = base_cols.get(o, set()) - head_cols.get(o, set())
        for c in sorted(removed):
            users = [e["target"] for e in base["column_edges"] if e["source"] == f"{o}.{c}"]
            if users:
                col_warn.append(f"- `{o}.{c}` が無くなります。使っている下流: " + ", ".join(f"`{u}`" for u in users))
    if col_warn:
        lines += ["", "### ⚠ 削除されるカラムを下流が使っています", *col_warn]
    if head["warnings"]:
        lines += ["", "<details><summary>解析できなかった箇所</summary>", "", *[f"- {w}" for w in head["warnings"]], "", "</details>"]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
