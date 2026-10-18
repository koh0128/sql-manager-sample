"""build/anomalies.json を GitHub Issue に同期する（gh CLI を使用）。

- 新しく見つかった異常 → Issue を作成（ラベル snowflake-anomaly）
- 解消した異常 → Issue をクローズ
同じ異常かどうかは本文に埋め込んだキー（<!-- anomaly-key: drift:DB.SCHEMA.NAME -->）で判定する。
level が info の異常（未デプロイ）は Issue にしない。

使い方:
    python tools/sync_issues.py --dry-run     # 何をするか表示するだけ
    python tools/sync_issues.py               # Actions では GH_TOKEN を渡して実行
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BUILD_DIR, read_json  # noqa: E402

LABEL = "snowflake-anomaly"
KEY_RE = re.compile(r"<!-- anomaly-key: (\S+) -->")


def gh(*args: str, input: str | None = None) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, input=input).stdout


def body(a: dict, site_url: str) -> str:
    lines = [f"<!-- anomaly-key: {a['key']} -->", f"**{a['label']}**: `{a['object']}`", "", a["title"], ""]
    if a.get("user"):
        lines += [f"- 実行者: `{a['user']}`（{a.get('role', '')}）", f"- 日時: {a.get('at', '')}", ""]
    if a.get("error"):
        lines += ["```", a["error"], "```", ""]
    if a.get("diff"):
        lines += ["<details><summary>差分（GitHub → Snowflake）</summary>", "", "```diff", a["diff"], "```", "</details>", ""]
    if a.get("sql"):
        lines += ["<details><summary>実行された SQL</summary>", "", "```sql", a["sql"], "```", "</details>", ""]
    if site_url:
        lines.append(f"[データフローマップで見る]({site_url.rstrip('/')}/#{a['object']})")
    lines += ["", "_このIssueは site ワークフローが自動で作成・クローズします。_"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anomalies", type=Path, default=BUILD_DIR / "anomalies.json")
    ap.add_argument("--site-url", default="", help="Pages の URL（Issue にリンクを入れる）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    current = {a["key"]: a for a in read_json(args.anomalies) if a["level"] != "info"}
    if args.dry_run:
        existing = {}
    else:
        gh("label", "create", LABEL, "--color", "D93F0B", "--description", "Snowflake 監視で検出した異常", "--force")
        issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--limit", "500",
                               "--json", "number,body"))
        existing = {m.group(1): i["number"] for i in issues if (m := KEY_RE.search(i.get("body") or ""))}

    for key, a in current.items():
        if key in existing:
            continue
        title = f"[{a['label']}] {a['object']}"
        print("CREATE", title)
        if not args.dry_run:
            gh("issue", "create", "--title", title, "--label", LABEL, "--body-file", "-", input=body(a, args.site_url))
    for key, num in existing.items():
        if key not in current:
            print("CLOSE ", f"#{num}", key)
            if not args.dry_run:
                gh("issue", "close", str(num), "--comment", "次回の監視で解消を確認したためクローズします。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
