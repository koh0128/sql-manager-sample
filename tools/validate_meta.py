"""sql/ 配下の規約チェック（PR の CI で実行）。

- .sql と .yml が対になっているか
- yml が tools/schemas/object.schema.json に合っているか、name / type がファイルの場所と一致しているか
- SQL が解析できるか、CREATE 文の対象名がファイルの場所と一致しているか

使い方:
    python tools/validate_meta.py                 # すべて
    python tools/validate_meta.py <file> ...      # 指定ファイル（.sql / .yml）だけ
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FOLDER_TO_TYPE, REPO_ROOT, SQL_DIR, SQL_DIR_NAME, read_yaml  # noqa: E402
from place_sql import detect  # noqa: E402

SCHEMA = json.loads((Path(__file__).parent / "schemas/object.schema.json").read_text(encoding="utf-8"))


def check(sql_path: Path) -> list[str]:
    errs = []
    rel = sql_path.relative_to(REPO_ROOT)
    try:
        db, schema, folder = sql_path.relative_to(SQL_DIR).parts[:3]
    except ValueError:
        return [f"{rel}: sql/<DB>/<SCHEMA>/<種別>/<NAME>.sql の形になっていません"]
    type_ = FOLDER_TO_TYPE.get(folder)
    if not type_:
        return [f"{rel}: 種別フォルダ '{folder}' は使えません（{', '.join(FOLDER_TO_TYPE)}）"]
    name = sql_path.stem
    if name != name.upper():
        errs.append(f"{rel}: ファイル名は大文字にしてください")

    d = detect(sql_path.read_text(encoding="utf-8"), db.upper(), schema.upper())
    if d is None:
        errs.append(f"{rel}: CREATE 文が見つかりません")
    elif (d[0], d[1], d[2]) != (db.upper(), schema.upper(), name.upper()) or d[3] != type_:
        errs.append(f"{rel}: CREATE の対象 {d[0]}.{d[1]}.{d[2]}（{d[3]}）がファイルの場所と一致しません")

    yml = sql_path.with_suffix(".yml")
    if not yml.exists():
        errs.append(f"{rel}: 対になる {yml.name} がありません（説明とオーナーを書いてください）")
        return errs
    meta = read_yaml(yml)
    for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(meta):
        loc = ".".join(str(p) for p in e.absolute_path) or "(root)"
        errs.append(f"{yml.relative_to(REPO_ROOT)}: {loc}: {e.message}")
    if meta.get("name") and meta["name"] != name.upper():
        errs.append(f"{yml.relative_to(REPO_ROOT)}: name '{meta['name']}' がファイル名と一致しません")
    if meta.get("type") and meta["type"] != type_:
        errs.append(f"{yml.relative_to(REPO_ROOT)}: type '{meta['type']}' がフォルダ（{type_}）と一致しません")
    return errs


def main(argv: list[str]) -> int:
    if argv:
        paths = {(REPO_ROOT / a).resolve().with_suffix(".sql") for a in argv if a.endswith((".sql", ".yml"))
                 and f"{SQL_DIR_NAME}/" in a and not Path(a).name.startswith("_")}
        paths = {p for p in paths if p.exists()}
    else:
        paths = set(SQL_DIR.glob("*/*/*/*.sql"))
    orphans = [y for y in SQL_DIR.glob("*/*/*/*.yml") if not y.with_suffix(".sql").exists()]
    errs = [e for p in sorted(paths) for e in check(p)]
    errs += [f"{y.relative_to(REPO_ROOT)}: 対になる .sql がありません" for y in orphans]
    for e in errs:
        print("ERROR", e)
    print(f"{len(paths)} files checked, {len(errs)} errors")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
