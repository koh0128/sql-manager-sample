# sql-management

Snowflake の SQL（テーブル・ビュー・タスク・プロシージャ等）を GitHub で管理し、
**リポジトリの SQL から作ったデータフロー図とドキュメント** を GitHub Pages で見られるようにする仕組みです。
Snowflake に接続できる場合は、最終更新・行数・30日の参照回数も図に表示します。

> いま `sql/` に入っている RAW_DB / DWH_DB / MART_DB はサンプルです。実際のオブジェクトに置き換えてください（下の「導入手順」）。

## 仕組み

```
リポジトリの SQL・yml ──(repo_graph.py)──▶ graph.json   … テーブル・カラムの依存、変換式、説明
Snowflake（任意）    ──(snapshot.py)───▶ state.json   … 最終更新・行数・カラム型・30日参照回数
                         │
                    (enrich.py) 参考情報を付ける
                         ▼
              lineage.json ──(build_site.py)──▶ GitHub Pages
```

画面でできること:
- テーブル単位のデータフロー図（DB/スキーマ・種別・タグ・オーナーで絞り込み、上流・下流のハイライト、Nホップ表示、検索）
- カラム単位のリネージ図（カラム間の線に変換式）
- 各オブジェクトの説明・オーナー・タグ・カラム説明・SQL・GitHub のファイルへのリンク
- 後続の数（直接／下流全体）・30日参照回数・最終更新・更新頻度。ノードの色を「更新の新しさ／参照回数／DB別」で切り替え
- 説明が未記入のオブジェクトとカラム説明の記入率

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
型・行数・更新日時など Snowflake から取れる情報は yml に書きません（取得して表示します）。

## ローカルで試す

```bash
make setup     # .venv を作って依存をインストール
make sample    # サンプルの Snowflake 情報でサイトを作る（Snowflake 接続不要）
make serve     # http://localhost:8000 で表示
```
`make site` はリポジトリの SQL だけで作ります（更新時刻・参照回数は「—」）。
その他: `make check`（規約チェック）、`make lint`（sqlfluff）、`make graph`（graph.json のみ）。

## 導入手順

1. **Snowflake に読み取り用のサービスユーザーを作る**（キーペア認証。Pages に更新時刻などを出さないなら不要）
   ```sql
   CREATE ROLE SQL_MGMT_READER;
   GRANT USAGE ON WAREHOUSE <WH_XS> TO ROLE SQL_MGMT_READER;
   GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE SQL_MGMT_READER;   -- 参照回数（ACCESS_HISTORY）用
   -- 対象 DB ごとに（FUTURE GRANTS も同様に）
   GRANT USAGE ON DATABASE <DB> TO ROLE SQL_MGMT_READER;
   GRANT USAGE ON ALL SCHEMAS IN DATABASE <DB> TO ROLE SQL_MGMT_READER;
   GRANT REFERENCES ON ALL TABLES IN DATABASE <DB> TO ROLE SQL_MGMT_READER;
   GRANT REFERENCES ON ALL VIEWS IN DATABASE <DB> TO ROLE SQL_MGMT_READER;
   CREATE USER SVC_SQL_MGMT TYPE = SERVICE DEFAULT_ROLE = SQL_MGMT_READER RSA_PUBLIC_KEY = '<公開鍵>';
   GRANT ROLE SQL_MGMT_READER TO USER SVC_SQL_MGMT;
   ```
   参照回数は ACCESS_HISTORY（Enterprise Edition 以上）から取ります。使えない場合はリポジトリ変数 `SNOWFLAKE_NO_ACCESS_HISTORY=true`。
   既存オブジェクトの書き出し（手順3）にはオブジェクトの定義を見る権限も必要です。
2. **`config.yml` を編集**：対象 `databases` と除外する `ignore_objects`。
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
   - Settings → Secrets and variables → Actions（Snowflake の情報を出す場合）
     - Variables: `SNOWFLAKE_ACCOUNT` `SNOWFLAKE_USER` `SNOWFLAKE_ROLE` `SNOWFLAKE_WAREHOUSE`（任意 `SNOWFLAKE_NO_ACCESS_HISTORY`）
     - Secrets: `SNOWFLAKE_PRIVATE_KEY`（PEM 本文）、必要なら `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`
   - `.github/CODEOWNERS` のチーム名を実際のものに
   - main のブランチ保護で `pr` ワークフローの成功と CODEOWNERS のレビューを必須に

## GitHub Actions

| ワークフロー | いつ | 何をする |
|---|---|---|
| `pr` | PR | 規約チェック、sqlfluff、SQL 解析確認、**変更の影響範囲（下流のテーブル・削除カラムの利用先）を PR にコメント** |
| `site` | main への push ＋ 毎時 | （設定があれば）Snowflake から更新時刻・行数・参照回数を取得 → サイト生成 → Pages 公開 |

## 今後

- **CD**：main マージで DEV へ自動デプロイ、PROD は承認付き。
- **dbt**：変換系 SQL をスキーマ単位で dbt に移行。`repo_graph.py` で dbt の `manifest.json` も読み、同じ図に統合する。
