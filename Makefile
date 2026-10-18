PY ?= .venv/bin/python

.PHONY: setup graph check lint site serve

setup:            ## 開発環境を作る
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

graph:            ## リポジトリの SQL からグラフ（build/graph.json）を作る
	$(PY) tools/repo_graph.py

check:            ## 規約チェック（CI と同じ）
	$(PY) tools/validate_meta.py
	$(PY) tools/repo_graph.py --check

lint:             ## SQL の静的チェック
	.venv/bin/sqlfluff lint sql --disable-progress-bar --ignore parsing

site:             ## サイトを作る（build/site）
	$(PY) tools/build_site.py

serve:            ## build/site をローカルで表示（http://localhost:8000）
	$(PY) -m http.server -d build/site 8000
