# -*- coding: utf-8 -*-
"""容量闸门（2026-10-22；2026-10-23 复核后补接入层/预算）。

## 为什么需要它
`app/server.py` 用 `ThreadingHTTPServer` —— **每个请求一个线程**，而生成层/
向量层都是**同步阻塞**调用（一次 3~10 秒）。没有上限时：
  30 / 100 个并发 ⇒ 线程、连接、内存一起涨；pm2 的 `max_memory_restart`
  会直接把进程重启，**正在答题的人全丢**。

## 两层，别混
```
HTTP 接入层（BoundedThreadingHTTPServer，MAX_HTTP_THREADS）
  → 线程总数硬上限；满了直接 429，**不创建线程**
  → 拿到的线程进入 handle_ask
      → 请求级 Gate（MAX_CONCURRENT_REQUESTS）：限制问答并发
      → 拿不到名额就排队，最多 QUEUE_TIMEOUT_S 秒
      → 还拿不到 ⇒ Overloaded（HTTP 429 + Retry-After）
```
⚠️ 2026-10-23 GPT 复核实测：只在 `handle_ask` 外拿信号量，**不能**阻止
`ThreadingHTTPServer` 为等待者各开一个线程。线程硬上限必须在接入层做；
`app/server.py::BoundedThreadingHTTPServer` 负责这件事。

## 总预算
`request_budget()` 设的 deadline 已经传进 `_chat` / `_chat_stream` / `embedder._call`：
每次上游调用前 `check()`，timeout 取 `min(原 timeout, remaining())`。
拿到名额后不会无限跑；超时抛 `Overloaded`，由入口返回/发 error+done。

## 它不做什么
- 并发闸门不跨进程；每日付费预算由 SQLite 跨同机进程共享
- 不按 IP / 用户限流（本项目不收集身份信息，见 querylog 隐私纪律）
"""
from __future__ import annotations

import contextlib
import contextvars
import sqlite3
import threading
import time
from pathlib import Path

from . import config


class Overloaded(RuntimeError):
    """容量不足 / 排队超时。`retry_after` 会进 HTTP `Retry-After`。"""

    def __init__(self, message: str = "现在问的人有点多，请稍后再试",
                 retry_after: int = 5) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.expires = time.monotonic() + max(0.0, float(seconds))

    def remaining(self) -> float:
        return max(0.0, self.expires - time.monotonic())


_dl: contextvars.ContextVar[Deadline | None] = contextvars.ContextVar(
    "shultimate_deadline", default=None)


@contextlib.contextmanager
def request_budget(seconds: float | None = None):
    """给当前请求设一个总预算；子调用可用 `remaining()` 读。"""
    d = Deadline(config.REQUEST_BUDGET_S if seconds is None else seconds)
    token = _dl.set(d)
    try:
        yield d
    finally:
        _dl.reset(token)


def remaining() -> float:
    d = _dl.get()
    return d.remaining() if d is not None else float("inf")


def check() -> None:
    """上游调用前检查总预算；用尽即抛 Overloaded（可重试，不是 500）。"""
    if remaining() <= 0:
        raise Overloaded("这次请求已经超过时间预算，请稍后再试", 2)


def timeout(default: float) -> float:
    """把「默认 timeout」压到「剩余预算」以内；没有预算时原样返回。"""
    return max(0.1, min(float(default), remaining()))


class Gate:
    """进程内的有界信号量 + 排队超时。"""

    def __init__(self, name: str, limit: int, queue_timeout: float) -> None:
        self.name = name
        self.limit = max(1, int(limit))
        self.queue_timeout = max(0.1, float(queue_timeout))
        self._sem = threading.BoundedSemaphore(self.limit)

    @contextlib.contextmanager
    def slot(self):
        wait = min(self.queue_timeout, remaining())
        if wait <= 0:
            raise Overloaded("这次请求已经等太久了，请稍后再试", 2)
        if not self._sem.acquire(timeout=wait):
            raise Overloaded(
                f"{self.name}排队超过 {int(self.queue_timeout)} 秒，请稍后再试", 5)
        try:
            yield
        finally:
            self._sem.release()


# 请求级闸门：所有 /api/ask、/api/card、/api/ask/stream 共用。
REQUEST = Gate("请求", config.MAX_CONCURRENT_REQUESTS, config.QUEUE_TIMEOUT_S)


# ────────────────────────── 每日付费调用总预算（2026-10-24）──────────────────────────
#
# 为什么必须在**上游调用点**计数：
#   · 只数 HTTP 请求 ⇒ 一次回答里的改写 + embedding + 生成 + 兜底不计；
#   · 只给 /api/ask 加闸门 ⇒ /api/search、/api/branches 可绕过；
#   · 只做并发闸门 ⇒ 串行慢速请求可以刷一整天。
# 计数点：embedder._call（每次尝试）、generator._chat/_chat_stream（每次调用）。
# 计数不含缓存命中（缓存不花钱），也不含 web 抓取（无模型费用）。
# 生产实例用 SQLite 持久计数；测试实例可保留内存计数器。
class DailyBudget:
    """Daily paid-call limit; optionally persisted across restarts/workers."""

    def __init__(self, limit: int, database_path: Path | None = None) -> None:
        self.limit = max(0, int(limit))
        self.database_path = database_path
        self._lock = threading.Lock()
        self._day = time.strftime("%Y-%m-%d")
        self._count = 0
        self._by_kind: dict[str, int] = {}

    def _rollover_locked(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._count = 0
            self._by_kind = {}

    def charge(self, kind: str = "paid") -> None:
        """计一次上游调用；超过日上限抛 Overloaded（429，不是 500）。

        ⚠️ 先检查再 +1：超限的请求不消耗计数；并发下由锁保证不会多放行。
        ⚠️ `limit == 0` 表示不限（离线/测试）；仍然计数，便于观察。
        """
        if self.database_path is not None:
            self._charge_persistent(kind)
            return
        with self._lock:
            self._rollover_locked()
            if self.limit and self._count >= self.limit:
                now = time.localtime()
                midnight = time.mktime((now.tm_year, now.tm_mon, now.tm_mday + 1,
                                        0, 0, 0, 0, 0, -1))
                retry = max(60, int(midnight - time.time()))
                raise Overloaded(
                    "今天的免费问答额度已经用完了，明天再来；"
                    "已经打开的页面仍可看历史与笔记。", min(retry, 3600))
            self._count += 1
            k = str(kind or "paid")[:24]
            self._by_kind[k] = self._by_kind.get(k, 0) + 1

    def _connection(self):
        assert self.database_path is not None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database_path, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS paid_calls (day TEXT NOT NULL, kind TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY(day, kind))")
        return db

    def _charge_persistent(self, kind: str) -> None:
        today = time.strftime("%Y-%m-%d")
        try:
            db = self._connection()
            try:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    count = db.execute("SELECT COALESCE(SUM(n), 0) FROM paid_calls WHERE day=?", (today,)).fetchone()[0]
                    if self.limit and count >= self.limit:
                        raise Overloaded("今天的付费调用额度已用完，请明天再试", 3600)
                    db.execute("INSERT INTO paid_calls(day, kind, n) VALUES (?, ?, 1) "
                               "ON CONFLICT(day, kind) DO UPDATE SET n=n+1",
                               (today, str(kind or "paid")[:24]))
            finally:
                db.close()
        except (sqlite3.Error, OSError) as exc:
            raise Overloaded("付费调用预算记录不可用，已停止模型调用", 60) from exc

    def snapshot(self) -> dict:
        if self.database_path is not None:
            today = time.strftime("%Y-%m-%d")
            try:
                db = self._connection()
                try:
                    rows = db.execute("SELECT kind, n FROM paid_calls WHERE day=?", (today,)).fetchall()
                finally:
                    db.close()
            except (sqlite3.Error, OSError):
                rows = []
            by_kind = dict(rows)
            return {"day": today, "count": sum(by_kind.values()),
                    "limit": self.limit, "by_kind": by_kind}
        with self._lock:
            self._rollover_locked()
            return {"day": self._day, "count": self._count,
                    "limit": self.limit, "by_kind": dict(self._by_kind)}

    def reset(self, limit: int | None = None) -> None:
        """测试用：重置计数/日期（不在生产请求路径调用）。"""
        with self._lock:
            if limit is not None:
                self.limit = max(0, int(limit))
            self._day = time.strftime("%Y-%m-%d")
            self._count = 0
            self._by_kind = {}
        if self.database_path is not None:
            db = self._connection()
            try:
                with db:
                    db.execute("DELETE FROM paid_calls WHERE day=?", (time.strftime("%Y-%m-%d"),))
            finally:
                db.close()


DAILY = DailyBudget(config.DAILY_PAID_CALL_LIMIT, config.DATA_DIR / "usage.sqlite3")


def charge(kind: str = "paid") -> None:
    """上游调用前计费；超限抛 Overloaded。"""
    DAILY.charge(kind)


def usage() -> dict:
    return DAILY.snapshot()


@contextlib.contextmanager
def paid_slot():
    """付费入口的统一闸门：时间预算 + 请求并发名额。

    ⚠️ 不要把已经自己拿过 REQUEST.slot() 的 handle_ask_sync 再包一层：
        BoundedSemaphore 不可重入，会自己把自己排队到超时。
        handle_ask_sync 自己用 request_budget + REQUEST；本函数给
        search/branches 这类**没有自己闸门**的入口用。
    """
    with request_budget():
        with REQUEST.slot():
            yield
