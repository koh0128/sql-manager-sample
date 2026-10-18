# sql-management

Snowflake の SQL（テーブル・ビュー・タスク・プロシージャ等）を GitHub で管理し、
**リポジトリの SQL から作ったデータフロー図とドキュメント** を GitHub Pages で見られるようにする仕組みです。

いまは最初のステップとして、**格納された SQL からデータフロー（リネージ）を整理する** ことに絞っています。
- SQL を `sql/` に格納する作業は手動で行います（下の「フォルダ構成」の規約に沿って格納）。回収・配置用のツールは `_archive/collect/` に退避しています。
- Snowflake には接続しません（DDL の書き出し、最終更新・行数・参照回数などの取得は一旦除外し、`_archive/snowflake/` に退避しています）。

> いま `sql/` に入っている RAW_DB / DWH_DB / MART_DB はサンプルです。実際のオブジェクトに置き換えてください（下の「導入手順」）。

## 仕組み

```
リポジトリの SQL・yml ──(repo_graph.py)──▶ graph.json   … テーブル・カラムの依存、変換式、説明
                                  │
                           (build_site.py)
                                  ▼
                     lineage.json ＋ 画面 ──▶ GitHub Pages
```

画面でできること:
- テーブル単位のデータフロー図（DB/スキーマ・種別・タグ・オーナーで絞り込み、上流・下流のハイライト、Nホップ表示、検索）
- カラム単位のリネージ図（カラム間の線に変換式）
- 各オブジェクトの説明・オーナー・タグ・カラム説明・SQL・GitHub のファイルへのリンク
- 後続の数（直接／下流全体）と、SQL・yml から分かる更新方法・更新頻度。ノードの色は DB 別
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

## ローカルで試す

```bash
make setup     # .venv を作って依存をインストール
make site      # リポジトリの SQL からサイトを作る
make serve     # http://localhost:8000 で表示
```
その他: `make check`（規約チェック）、`make lint`（sqlfluff）、`make graph`（graph.json のみ）。

## 導入手順

1. **GitHub の設定**
   - Settings → Pages → Source を「GitHub Actions」、Visibility を **Private**（組織内のみ）
   - `.github/CODEOWNERS` のチーム名を実際のものに
   - main のブランチ保護で `pr` ワークフローの成功と CODEOWNERS のレビューを必須に
2. **SQL を手動で格納**：「フォルダ構成」の規約（1オブジェクト1ファイル、`.sql` と `.yml` を対で、名前は大文字）に沿って `sql/` に置き、PR を作る。
   サンプルの `sql/` は、実際の SQL が入る時点で削除する。
3. **格納された PR を確認**：`pr` ワークフローの規約チェック・SQL 解析の結果を見る。手元では `make check` と `make site` で確認できます。
   図で線がつながらない・解析できない SQL は `make check` の警告に出ます。
   CTAS で作るテーブルは、`GET_DDL` の結果（カラム定義だけ）ではなく元の CTAS 文が入っていないと上流がつながりません。

## GitHub Actions

| ワークフロー | いつ | 何をする |
|---|---|---|
| `pr` | PR | 規約チェック、sqlfluff、SQL 解析確認、**変更の影響範囲（下流のテーブル・削除カラムの利用先）を PR にコメント** |
| `site` | main への push | リポジトリの SQL からサイト生成 → Pages 公開 |

## ロードマップ

一元管理（v1）→ 環境の変数化とデプロイ自動化（v2）→ データ品質管理（v3）の順に進めます。
