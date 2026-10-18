"""tools/ 共通の定数・ユーティリティ。"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
from pathlib import Path

import sqlglot
import yaml

# TASK / STREAM など sqlglot が未対応の構文は Command として扱うので、その警告ログは出さない
logging.getLogger("sqlglot").setLevel(logging.ERROR)

REPO_ROOT = Path(__file__).resolve().parent.parent
# SQL を置くフォルダ名（変える場合はここと CODEOWNERS・ワークフローの paths を変更）
SQL_DIR_NAME = "sql"
SQL_DIR = REPO_ROOT / SQL_DIR_NAME
BUILD_DIR = REPO_ROOT / "build"
DIALECT = "snowflake"

# フォルダ名 ⇔ オブジェクト種別
FOLDER_TO_TYPE = {
    "tables": "table",
    "views": "view",
    "dynamic_tables": "dynamic_table",
    "streams": "stream",
    "tasks": "task",
    "procedures": "procedure",
    "functions": "function",
    "stages": "stage",
    "file_formats": "file_format",
}
TYPE_TO_FOLDER = {v: k for k, v in FOLDER_TO_TYPE.items()}


def read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fqn(*parts: str) -> str:
    return ".".join(p.strip('"').upper() for p in parts)


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


CREATE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:SECURE|TRANSIENT|TEMPORARY|TEMP|LOCAL|GLOBAL|RECURSIVE)\s+)*"
    r"(DYNAMIC\s+TABLE|MATERIALIZED\s+VIEW|TABLE|VIEW|STREAM|TASK|PROCEDURE|FUNCTION|FILE\s+FORMAT|STAGE)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([\w$\"]+(?:\.[\w$\"]+){0,2})", re.I)
KIND = {"TABLE": "table", "VIEW": "view", "MATERIALIZED VIEW": "view", "DYNAMIC TABLE": "dynamic_table",
        "STREAM": "stream", "TASK": "task", "PROCEDURE": "procedure", "FUNCTION": "function",
        "FILE FORMAT": "file_format", "STAGE": "stage"}


def detect(sql: str, default_db: str | None, default_schema: str | None):
    """最初の CREATE 文から (DB, SCHEMA, NAME, 種別) を返す。分からなければ None。"""
    m = CREATE_RE.search(strip_comments(sql))
    if not m:
        return None
    kind = KIND[re.sub(r"\s+", " ", m.group(1).upper())]
    parts = [p.strip('"').upper() for p in m.group(2).split(".")]
    if len(parts) == 1:
        parts = [default_db, default_schema, parts[0]]
    elif len(parts) == 2:
        parts = [default_db, *parts]
    if not all(parts):
        return None
    return (*parts, kind)


TASK_BODY_RE = re.compile(r"\bAS\s+(?=(MERGE|INSERT|CALL|CREATE|UPDATE|DELETE|BEGIN|EXECUTE|COPY|SELECT)\b)", re.I)


def task_body(sql: str) -> str:
    """CREATE TASK ... AS <body> の body 部分。"""
    text = strip_comments(sql)
    m = TASK_BODY_RE.search(text)
    return text[m.end():].strip() if m else text


def procedure_body(sql: str) -> str:
    """CREATE PROCEDURE ... AS $$ <body> $$ の body 部分。"""
    m = re.search(r"\$\$(.*)\$\$", sql, re.S)
    return m.group(1).strip() if m else sql


def match_any(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name.upper(), p.upper()) for p in patterns or [])


def humanize_schedule(s: str) -> str:
    """'5 MINUTE' → '5分毎'、'USING CRON 30 6 * * * Asia/Tokyo' → '毎日06:30'、'1 hour' → '1時間毎'。"""
    if not s:
        return ""
    m = re.fullmatch(r"\s*(\d+)\s*(MINUTE|MINUTES|M|HOUR|HOURS|H|DAY|DAYS|D)\s*", s, re.I)
    if m:
        unit = m.group(2).upper()[0]
        return f"{m.group(1)}{ {'M': '分', 'H': '時間', 'D': '日'}[unit] }毎"
    m = re.fullmatch(r"\s*USING\s+CRON\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\S+)?\s*", s, re.I)
    if m:
        minute, hour, dom, mon, dow = m.groups()
        if minute.isdigit() and hour.isdigit() and dom == mon == "*":
            t = f"{int(hour):02d}:{int(minute):02d}"
            return f"毎日{t}" if dow == "*" else f"毎週({dow}) {t}"
    return s
