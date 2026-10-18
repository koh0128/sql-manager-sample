"""グラフ生成 → Snowflake 状態との突き合わせ → 公開用サイト（build/site/）までを一度に行う。

使い方:
    python tools/build_site.py                                   # リポジトリの SQL だけで作る
    python tools/build_site.py --state examples/state.sample.json  # サンプル状態で作る
    python tools/build_site.py --state build/state.json            # snapshot.py の出力で作る

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
import compare  # noqa: E402
import repo_graph  # noqa: E402
from common import BUILD_DIR, REPO_ROOT, SQL_DIR, read_json, read_yaml, write_json  # noqa: E402


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", type=Path, default=None, help="Snowflake 状態（省略時はリポジトリのみ）")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "site")
    args = ap.parse_args()

    graph = repo_graph.build(SQL_DIR)
    write_json(BUILD_DIR / "graph.json", graph)
    state = read_json(args.state) if args.state else None
    lineage, anomalies = compare.build(graph, state, read_yaml(REPO_ROOT / "monitor.yml"))
    lineage["repo_url"], lineage["branch"] = repo_url()
    write_json(BUILD_DIR / "lineage.json", lineage)
    write_json(BUILD_DIR / "anomalies.json", anomalies)

    if args.out.exists():
        shutil.rmtree(args.out)
    shutil.copytree(REPO_ROOT / "site", args.out)
    shutil.copy(BUILD_DIR / "lineage.json", args.out / "lineage.json")
    (args.out / ".nojekyll").touch()

    print(f"site -> {args.out}  ({len(lineage['nodes'])} objects, {len(anomalies)} anomalies,"
          f" {len(graph['warnings'])} warnings)")
    for w in graph["warnings"]:
        print("WARN", w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
