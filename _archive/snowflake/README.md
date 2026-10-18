# Snowflake 接続まわり（一旦除外）

最初のステップ（SQL ファイルの回収とデータフローの整理）では Snowflake に接続しないため、
接続して情報を取る部分をここに退避しています。動かす前提のコードではありません。

| ファイル | 元の場所 | 役割 |
|---|---|---|
| `tools/snapshot.py` | `tools/` | 最終更新・行数・カラム型・30日参照回数を取得して `build/state.json` に出力 |
| `tools/export_ddl.py` | `tools/` | 既存オブジェクトの DDL を `sql/` に書き出す（初回ベースライン用） |
| `tools/enrich.py` | `tools/` | `graph.json` に `state.json` の情報を付けて `lineage.json` を作る |
| `config.yml` | ルート | 取得対象の DB・除外パターン |
| `requirements-snowflake.txt` | ルート | 接続用の追加依存 |
| `examples/` | ルート | サンプルの `state.json` とその生成スクリプト |

戻すときは元の場所に移し、`tools/build_site.py`・`site/app.js`・`.github/workflows/site.yml`・`Makefile`・`README.md` を
この退避より前のコミットの内容に合わせて戻してください。
