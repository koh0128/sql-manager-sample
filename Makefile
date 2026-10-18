PY ?= .venv/bin/python
STATE ?=

.PHONY: setup graph check lint site sample serve snapshot

setup:            ## 開発環境を作る
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

graph:            ## リポジトリの SQL からグラフ（build/graph.json）を作る
	$(PY) tools/repo_graph.py

check:            ## 規約チェック（CI と同じ）
	$(PY) tools/validate_meta.py
	$(PY) tools/repo_graph.py --check

lint:             ## SQL の静的チェック
	.venv/bin/sqlfluff lint sql --disable-progress-bar --ignore parsing

site:             ## サイトを作る（STATE=build/state.json を渡すと Snowflake の情報を付ける）
	$(PY) tools/build_site.py $(if $(STATE),--state $(STATE),)

sample:           ## サンプルの Snowflake 情報でサイトを作る
	$(PY) tools/build_site.py --state examples/state.sample.json

serve:            ## build/site をローカルで表示（http://localhost:8000）
	$(PY) -m http.server -d build/site 8000

snapshot:         ## Snowflake から更新時刻・行数・参照回数を取得（環境変数で接続）
	$(PY) tools/snapshot.py
