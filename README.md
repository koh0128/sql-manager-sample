# sql-management

Snowflake の SQL（テーブル・ビュー・タスク・プロシージャ等）を GitHub で管理し、
**リポジトリの SQL から作ったデータフロー図** に **Snowflake の実際の状態** を重ねて、
異常（タスク失敗・手動変更・手動デプロイ・GitHub 未登録など）を同じ画面で確認できるようにする仕組みです。

> いま `sql/` に入っている RAW_DB / DWH_DB / MART_DB はサンプルです。実際のオブジェクトに置き換えてください（下の「導入手順」）。

## 仕組み

```
リポジトリの SQL・yml ──(repo_graph.py)──▶ graph.json   … あるべき姿（テーブル・カラムの依存、変換式）
Snowflake          ──(snapshot.py)───▶ state.json   … 実際の姿（定義・実行結果・DDL 実行者・参照回数）
                         │
                   (compare.py) 突き合わせ
                         ▼
          lineage.json（画面用）＋ anomalies.json（異常一覧）
                         │
        build_site.py → GitHub Pages ／ sync_issues.py → GitHub Issue
```

| 検出する異常 | 判定 |
|---|---|
| タスク失敗・更新遅延 | `TASK_HISTORY` の最終実行が失敗。書き込み先と、その下流を「更新遅延」にする |
| 手動変更 | Snowflake の定義とリポジトリの SQL が違う（整形・大文字小文字・コメントの違いは無視） |
| 手動デプロイ | `monitor.yml` の許可リストに無いユーザーが DDL を実行した（誰が・いつ・何を） |
| GitHub 未登録 | Snowflake にだけあるオブジェクト |
| 未デプロイ | リポジトリにだけあるオブジェクト |
| コード外の流れ | 実行履歴にはあるが、リポジトリの SQL に無い読み書き |
| 30日以上未更新 | 書き込み（ビューは参照）が `stale_days` 以上無く、参照も無い |

## フォルダ構成

```
sql/<DATABASE>/<SCHEMA>/<種別>/<NAME>.sql   … CREATE 文（1オブジェクト1ファイル）
sql/<DATABASE>/<SCHEMA>/<種別>/<NAME>.yml   … 説明・オーナー・タグ・カラム説明
sql/<DATABASE>/_database.yml                … DB の説明
```
種別フォルダ: `tables` `views` `dynamic_tables` `streams` `tasks` `procedures` `functions` `stages` `file_formats`

- ファイル名・フォルダ名は Snowflake の識別子どおり**大文字**。
- SQL 内のオブジェクト名は `DB.SCHEMA.NAME` で書く（省略するとファイルの場所の DB・スキーマとみなす）。
- Airflow などタスク以外で更新するテーブルは、yml の `meta.refresh`（例: `Airflow: dim_daily`）と `meta.schedule`（例: `毎日02:20`）を書くと図に出ます。

yml の例（必須は `name` `type` `description` `owner`。定義は `tools/schemas/object.schema.json`）:
```yaml
name: DIM_CUSTOMER
type: table
description: 顧客ディメンション。PIIを除外し年代に丸めたもの。
owner: data-platform
tags: [ec, dim]
meta:
  refresh: 'Airflow: dim_daily'
  schedule: 毎日02:20
columns:
  - name: AGE_BAND
    description: 年代（10歳刻み）
```
型・行数・更新日時など Snowflake から取れる情報は yml に書きません（自動で取得して表示します）。

## ローカルで試す

```bash
make setup     # .venv を作って依存をインストール
make sample    # サンプルの Snowflake 状態でサイトを作る（Snowflake 接続不要）
make serve     # http://localhost:8000 で表示
```
`make site` はリポジトリの SQL だけで作ります（状態は「未取得」表示）。
その他: `make check`（規約チェック）、`make lint`（sqlfluff）、`make graph`（graph.json のみ）。

## 導入手順

1. **Snowflake に読み取り用のサービスユーザーを作る**（キーペア認証）
   ```sql
   CREATE ROLE SQL_MGMT_READER;
   GRANT USAGE ON WAREHOUSE <WH_XS> TO ROLE SQL_MGMT_READER;
   GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE SQL_MGMT_READER;   -- ACCOUNT_USAGE
   -- 対象 DB ごとに
   GRANT USAGE ON DATABASE <DB> TO ROLE SQL_MGMT_READER;
   GRANT USAGE ON ALL SCHEMAS IN DATABASE <DB> TO ROLE SQL_MGMT_READER;
   GRANT REFERENCES ON ALL TABLES IN DATABASE <DB> TO ROLE SQL_MGMT_READER;
   GRANT REFERENCES ON ALL VIEWS IN DATABASE <DB> TO ROLE SQL_MGMT_READER;
   GRANT MONITOR ON ALL TASKS IN DATABASE <DB> TO ROLE SQL_MGMT_READER;
   -- FUTURE GRANTS も同様に
   CREATE USER SVC_SQL_MGMT TYPE = SERVICE DEFAULT_ROLE = SQL_MGMT_READER RSA_PUBLIC_KEY = '<公開鍵>';
   GRANT ROLE SQL_MGMT_READER TO USER SVC_SQL_MGMT;
   ```
   ACCESS_HISTORY（手動デプロイの実行者・コード外の流れ・参照回数）は Enterprise Edition 以上が必要です。
   Standard Edition の場合はリポジトリ変数 `SNOWFLAKE_NO_ACCESS_HISTORY=true` を設定してください。
2. **`monitor.yml` を編集**：対象 `databases`、`allowed_ddl`（Airflow などのサービスユーザー）、`ignore_objects`。
3. **既存オブジェクトを書き出す**（サンプルの `sql/` は削除してから）
   ```bash
   pip install -r requirements-snowflake.txt
   export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=SVC_SQL_MGMT SNOWFLAKE_ROLE=SQL_MGMT_READER \
          SNOWFLAKE_WAREHOUSE=... SNOWFLAKE_PRIVATE_KEY_PATH=~/.ssh/svc_sql_mgmt.p8
   python tools/export_ddl.py
   ```
4. **各自の PC にある SQL を回収**：ブランチを切って
   ```bash
   python tools/place_sql.py ~/Documents/my_sqls            # 確認
   python tools/place_sql.py ~/Documents/my_sqls --apply    # 新規のものを配置
   ```
   「内容が異なる」と出たものは、どちらが正しいか確認してから `--overwrite` するか手で直す。yml の説明・オーナーを書いて PR。
5. **GitHub の設定**
   - Settings → Pages → Source を「GitHub Actions」、Visibility を **Private**（組織内のみ）
   - Settings → Secrets and variables → Actions
     - Variables: `SNOWFLAKE_ACCOUNT` `SNOWFLAKE_USER` `SNOWFLAKE_ROLE` `SNOWFLAKE_WAREHOUSE`（任意 `SNOWFLAKE_NO_ACCESS_HISTORY`）
     - Secrets: `SNOWFLAKE_PRIVATE_KEY`（PEM 本文）、必要なら `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`
   - `.github/CODEOWNERS` のチーム名を実際のものに
   - main のブランチ保護で `pr` ワークフローの成功と CODEOWNERS のレビューを必須に

## GitHub Actions

| ワークフロー | いつ | 何をする |
|---|---|---|
| `pr` | PR | 規約チェック、sqlfluff、SQL 解析確認、**変更の影響範囲（下流のテーブル・削除カラムの利用先）を PR にコメント** |
| `site` | main への push ＋ 毎時 | Snowflake 状態の取得 → サイト生成 → Pages 公開 → 異常を Issue に同期（新規は作成、解消したらクローズ） |

Task 失敗をすぐ知りたい場合は、Snowflake 側でタスクに `ERROR_INTEGRATION` を設定して Slack 等へ通知してください（サイトと Issue は最大1時間遅れ）。

## 今後

- **CD**：main マージで DEV へ自動デプロイ、PROD は承認付き。デプロイは `monitor.yml` の `deploy_users` のユーザーで行い、導入後は `mode: strict`（それ以外の DDL はすべて異常）に切り替える。
- **dbt**：変換系 SQL をスキーマ単位で dbt に移行。`repo_graph.py` で dbt の `manifest.json` も読み、同じ図に統合する。
