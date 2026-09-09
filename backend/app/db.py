"""SQLite storage. Keep it dependency-free; WAL for concurrent reader (API) + writer (scheduler)."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

# 数据目录可经 HW_DATA_DIR 重定向（Docker 挂卷 /data）；默认仍是 backend/ 下
_DATA_DIR = Path(os.environ.get("HW_DATA_DIR") or Path(__file__).resolve().parent.parent)
DB_PATH = _DATA_DIR / "hermes-watch.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  hostname TEXT NOT NULL,
  port INTEGER DEFAULT 22,
  username TEXT DEFAULT 'root',
  secret TEXT DEFAULT '',
  group_name TEXT DEFAULT 'default',
  mock INTEGER DEFAULT 0,
  chaos TEXT DEFAULT '',
  created_at REAL
);
CREATE TABLE IF NOT EXISTS metrics(
  host_id INTEGER, ts REAL,
  cpu REAL, mem REAL, disk REAL, net_in REAL, net_out REAL, load1 REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics ON metrics(host_id, ts);
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY,
  host_id INTEGER, ts REAL, type TEXT, severity TEXT,
  title TEXT, detail TEXT, evidence TEXT, status TEXT DEFAULT 'open',
  card TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts REAL, host_id INTEGER, kind TEXT, message TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY,
  ts REAL, host_id INTEGER, finding_id INTEGER,
  title TEXT, command TEXT, rationale TEXT,
  status TEXT DEFAULT 'pending', decided_at REAL
);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, title TEXT,
  score REAL, data TEXT, content_html TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS proposal_runs(
  id INTEGER PRIMARY KEY,
  ts REAL, proposal_id INTEGER, host_id INTEGER,
  mode TEXT, command TEXT, risk TEXT,
  status TEXT, exit_code INTEGER, output TEXT, duration_ms INTEGER
);
CREATE TABLE IF NOT EXISTS notify_log(
  id INTEGER PRIMARY KEY,
  ts REAL, kind TEXT, text TEXT, channel TEXT,
  ok INTEGER, error TEXT DEFAULT ''
);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


MIGRATIONS = [
    # 出站采集 Agent（beszel 式）：token 绑定主机，agent 主动 push 指标
    "ALTER TABLE hosts ADD COLUMN agent_token TEXT DEFAULT ''",
    # Go agent 上报的 extras（进程/失败服务/证书），host_detail 直接展示
    "ALTER TABLE hosts ADD COLUMN last_extras TEXT DEFAULT ''",
    # 在线判定与采集错误：last_ok_ts 为最近一次成功采集（SSH 或 agent push）时间
    "ALTER TABLE hosts ADD COLUMN last_ok_ts REAL DEFAULT 0",
    "ALTER TABLE hosts ADD COLUMN last_error TEXT DEFAULT ''",
    # 告警生命周期：连续 ok_streak 轮未再触发 → 自动 resolved；resolved_at 记录恢复时间
    "ALTER TABLE findings ADD COLUMN ok_streak INTEGER DEFAULT 0",
    "ALTER TABLE findings ADD COLUMN resolved_at REAL",
    # crit 告警周期重发：上次外发通知时间
    "ALTER TABLE findings ADD COLUMN last_notified REAL",
    # 告警确认：人工已知悉后停止重发
    "ALTER TABLE findings ADD COLUMN acked_at REAL",
    # SSH TOFU：首次连接记录的主机公钥指纹（SHA256:…）；按主机静默截止时间
    "ALTER TABLE hosts ADD COLUMN host_key_fp TEXT DEFAULT ''",
    "ALTER TABLE hosts ADD COLUMN silenced_until REAL DEFAULT 0",
    # agent 上报的磁盘 IO 速率（KiB/s）与温度（°C），0=未上报
    "ALTER TABLE metrics ADD COLUMN io_read REAL DEFAULT 0",
    "ALTER TABLE metrics ADD COLUMN io_write REAL DEFAULT 0",
    "ALTER TABLE metrics ADD COLUMN temp_c REAL DEFAULT 0",
    # swap 使用率（%，agent/SSH 探针双来源），0=无 swap 或未上报
    "ALTER TABLE metrics ADD COLUMN swap REAL DEFAULT 0",
]

SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'observer',
  created_at REAL
);
"""


def init_db():
    from . import secrets as sec
    with _lock, connect() as con:
        con.executescript(SCHEMA)
        con.executescript(SCHEMA_EXTRA)
        for m in MIGRATIONS:
            try:
                con.execute(m)
            except sqlite3.OperationalError:
                pass  # 列已存在
        sec.migrate_plaintext(con)  # SSH 密码明文 → enc:v1 加密（幂等）


def query(sql: str, params=()) -> list[dict]:
    with _lock, connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def query_one(sql: str, params=()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()) -> int:
    with _lock, connect() as con:
        cur = con.execute(sql, params)
        con.commit()
        return cur.lastrowid


def executemany(sql: str, seq):
    with _lock, connect() as con:
        con.executemany(sql, seq)
        con.commit()


def now() -> float:
    return time.time()


def j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def uj(s, default=None):
    try:
        return json.loads(s)
    except (TypeError, json.JSONDecodeError):
        return default
