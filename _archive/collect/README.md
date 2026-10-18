# SQL の回収・配置（外す）

SQL を `sql/` に格納する作業は外注先が行うため、回収・配置用のツールをここに退避しています。

| ファイル | 元の場所 | 役割 |
|---|---|---|
| `place_sql.py` | `tools/` | 手元の SQL ファイルの CREATE 文から格納先を判定し、`sql/<DB>/<SCHEMA>/<種別>/<NAME>.sql` に配置する |

DDL の書き出し（`export_ddl.py`）は `../snowflake/` にあります。
CREATE 文から格納先を判定する `detect()` は、規約チェック（`tools/validate_meta.py`）でも使うため `tools/common.py` に残しています。
