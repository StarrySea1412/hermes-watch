"""SQLite 定时备份与恢复（对标 Beszel 自动备份）。

- 备份用 sqlite3 在线 backup API（WAL 安全），落 HW_DATA_DIR/backups/
- 调度：scheduler 保留策略周期调用 maybe_backup_daily()（24h 一次，settings.last_backup_ts 记账）
- 恢复：运行中直接换库文件不安全 → 暂存 .restore-pending 标记，下次启动时
  consume_restore_if_pending()（main.lifespan 最先调用）替换库文件再初始化
"""
import os
import re
import sqlite3
import time
from pathlib import Path

from . import db

_NAME_RE = re.compile(r"^hermes-watch-\d{8}-\d{6}(\.\w+)?\.db$")  # 只认自家备份名，防路径穿越
_MARKER = ".restore-pending"


def backup_dir() -> Path:
    return db._DATA_DIR / "backups"


def create_backup() -> dict:
    """在线备份当前库（sqlite3 backup API，连接期间 WAL 一致性由 SQLite 保证）。"""
    backup_dir().mkdir(parents=True, exist_ok=True)
    name = time.strftime("hermes-watch-%Y%m%d-%H%M%S.db")
    dest = backup_dir() / name
    src = sqlite3.connect(db.DB_PATH, timeout=10)
    try:
        dst = sqlite3.connect(dest, timeout=10)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    db.execute("INSERT INTO settings(key,value) VALUES('last_backup_ts',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(db.now()),))
    return {"name": name, "size": dest.stat().st_size, "ts": db.now()}


def list_backups() -> list[dict]:
    out = []
    d = backup_dir()
    if not d.exists():
        return out
    for f in sorted(d.iterdir(), key=lambda x: x.name, reverse=True):
        if f.is_file() and _NAME_RE.match(f.name):
            out.append({"name": f.name, "size": f.stat().st_size, "ts": f.stat().st_mtime})
    return out


def prune(keep: int | None = None) -> int:
    """只保留最近 N 份（settings.backup_keep，默认 10），返回删除数。"""
    if keep is None:
        r = db.query_one("SELECT value FROM settings WHERE key='backup_keep'")
        try:
            keep = max(1, int(float(r["value"])))
        except (TypeError, ValueError):
            keep = 10
    removed = 0
    for row in list_backups()[keep:]:
        try:
            (backup_dir() / row["name"]).unlink()
            removed += 1
        except OSError:
            pass
    return removed


def maybe_backup_daily() -> None:
    """距上次备份 ≥ 24h 则备份 + 修剪（由保留策略周期驱动，异常不外抛）。"""
    r = db.query_one("SELECT value FROM settings WHERE key='last_backup_ts'")
    try:
        last = float(r["value"])
    except (TypeError, ValueError):
        last = 0
    if db.now() - last < 86400:
        return
    create_backup()
    prune()


def resolve_name(name: str) -> Path | None:
    """校验备份名（防路径穿越）并返回绝对路径；不存在返回 None。"""
    if not _NAME_RE.match(name):
        return None
    p = backup_dir() / name
    return p if p.exists() else None


def stage_restore(name: str) -> bool:
    """暂存恢复标记（重启时由 consume_restore_if_pending 消费）。"""
    p = resolve_name(name)
    if not p:
        return False
    (db._DATA_DIR / _MARKER).write_text(name, encoding="utf-8")
    return True


def consume_restore_if_pending(data_dir: Path | None = None, db_path=None) -> str | None:
    """启动最前端调用：存在恢复标记则用备份替换库文件（连同 WAL/SHM 清理）。

    返回恢复的备份名；data_dir/db_path 参数供测试注入临时目录，不触真实库。"""
    d = data_dir or db._DATA_DIR
    marker = d / _MARKER
    if not marker.exists():
        return None
    name = marker.read_text(encoding="utf-8").strip()
    marker.unlink()
    target = db_path or db.DB_PATH
    src = d / "backups" / name
    if not _NAME_RE.match(name) or not src.exists():
        return None
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(Path(str(target) + suffix))
        except OSError:
            pass
    os.replace(src, target)
    return name
