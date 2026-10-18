"""手元にある SQL ファイルを解析して、sql/<DB>/<SCHEMA>/<種別>/<NAME>.sql の正しい場所に配置する。

各ファイルの CREATE 文から作成対象（DB.SCHEMA.NAME と種別）を読み取り、リポジトリの既存ファイルと比べて
「新規 / 一致 / 内容が異なる」を報告する。CREATE を含まないファイル（分析クエリ等）は対象外として一覧に出すだけ。

使い方:
    python tools/place_sql.py ~/Desktop/my_sqls                    # 確認だけ（何も書き込まない）
    python tools/place_sql.py ~/Desktop/my_sqls --apply            # 新規のものだけ配置
    python tools/place_sql.py ~/Desktop/my_sqls --apply --overwrite # 内容が異なるものも上書き
    python tools/place_sql.py ... --default-db DWH_DB --default-schema CORE  # 名前が DB/スキーマ省略のとき
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CREATE_RE, DIALECT, REPO_ROOT, SQL_DIR, TYPE_TO_FOLDER, detect, strip_comments  # noqa: E402

import sqlglot  # noqa: E402


def normalize_sql(sql: str) -> str:
    """整形・大文字小文字・コメントの違いを吸収した比較用の文字列にする。

    sqlglot で解析できる文は再生成して揃え、解析できない文（TASK など）は
    コメント除去・空白圧縮・大文字化で揃える。
    """
    body = strip_comments(sql).strip().rstrip(";").strip()
    try:
        parsed = sqlglot.parse(body, read=DIALECT)
        if parsed and all(p is not None and type(p).__name__ != "Command" for p in parsed):
            # normalize=True は引用符なし識別子だけを揃える（文字列リテラルは変えない）
            return ";".join(p.sql(DIALECT, normalize=True, pretty=False) for p in parsed)
    except sqlglot.errors.ParseError:
        pass
    text = re.sub(r"\s+", " ", body).upper()
    return re.sub(r"\s*([(),=;])\s*", r"\1", text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--apply", action="store_true", help="実際に配置する")
    ap.add_argument("--overwrite", action="store_true", help="内容が異なる既存ファイルも上書きする")
    ap.add_argument("--default-db")
    ap.add_argument("--default-schema")
    args = ap.parse_args()

    report = {"new": [], "same": [], "differs": [], "skipped": [], "unknown_scope": []}
    for f in sorted(args.folder.rglob("*.sql")):
        sql = f.read_text(encoding="utf-8", errors="replace")
        d = detect(sql, args.default_db and args.default_db.upper(), args.default_schema and args.default_schema.upper())
        if d is None:
            key = "unknown_scope" if CREATE_RE.search(strip_comments(sql)) else "skipped"
            report[key].append((f, None))
            continue
        db, schema, name, kind = d
        dest = SQL_DIR / db / schema / TYPE_TO_FOLDER[kind] / f"{name}.sql"
        if not dest.exists():
            status = "new"
        elif normalize_sql(dest.read_text(encoding="utf-8")) == normalize_sql(sql):
            status = "same"
        else:
            status = "differs"
        report[status].append((f, dest))
        if args.apply and (status == "new" or (status == "differs" and args.overwrite)):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(sql.rstrip() + "\n", encoding="utf-8")

    labels = {"new": "新規（リポジトリに無い）", "same": "一致（既に同じ内容がある）",
              "differs": "内容が異なる（どちらが正しいか確認してください）",
              "unknown_scope": "DB/スキーマが省略されていて場所を決められない（--default-db/--default-schema を指定）",
              "skipped": "CREATE 文が無いため対象外（分析クエリ等）"}
    for k, label in labels.items():
        if report[k]:
            print(f"\n■ {label}: {len(report[k])} 件")
            for src, dest in report[k]:
                print(f"  {src}" + (f"  →  {dest.relative_to(REPO_ROOT)}" if dest else ""))
    if not args.apply:
        print("\n（確認のみ。配置するには --apply を付けてください）")
    else:
        print("\n配置しました。git diff で内容を確認し、yml（説明・オーナー）を書いて PR を作成してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
