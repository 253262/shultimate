#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📊 **提问埋点** —— 记录用户问了什么、走了哪条路、答得怎么样（2026-10-13 owner 同意）。

## 为什么需要它
owner 定的成功判据是「**用户说懂了**」。但要判断"懂没懂"，先得知道
**他们问了什么、在哪一句卡住了、哪一类问题答得最烂**。

在此之前，唯一的信号是用户**主动点反馈**（`data/feedback.jsonl`）——
那不点的人（绝大多数）问了什么，我们**完全不知道**。
⇒ 30 轮的全部改进都是"自己跟自己比"，因为没有真实问题分布。

## ⚠️⚠️ 隐私：只记能改进产品的东西，绝不记能识别人的东西

**记**：
- 问题文本（⚠️ 见下面的"脱敏"）
- 走了哪条路（`rule` / `related` / `off_topic` / `harm` / `chat` / `curated`）
- 有没有被拒、命中几条条款、耗时、用的哪个模型
- 是否引用了条款、答案长度

**绝不记**：
- ❌ IP、User-Agent、Cookie、设备指纹
- ❌ 任何用户 ID / 昵称 / 会话 ID（**连哈希都不存** —— 哈希也是一种标识）
- ❌ 历史对话（只记当前这一问）
- ❌ 反馈里用户写的自由文本（那条走 `feedback.jsonl`，是用户**主动**写的，性质不同）

## ⚠️ 脱敏（`_scrub`）
用户可能顺手把真名、手机号、微信号打进问题里（"我们队长张三说…"）。
**不脱敏就是把人名往磁盘上写。** 做法：
- 手机号 / 长数字串 → 打码
- 邮箱 → 打码
- ⚠️ **明确不做的**：不做"姓名识别" —— 中文姓名没法靠正则可靠识别，
  硬做会**误伤规则术语**（"王哲"是规则编委，"张新光"也是）。
  ⇒ 诚实的边界：**我们挡得住联系方式，挡不住人名**。
     这条写在这里，是为了让接手的人**知道这个洞存在**，而不是以为已经解决了。

## 存储
`data/queries.jsonl`，**逐行 JSON append**。
⚠️ 用 append 而不是重写整个文件：埋点是**高频写**，重写在并发下会丢数据。
⚠️ 单行长度上限（`_MAX_Q`）防止有人往问题里塞一篇小说把磁盘写满。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from . import config

LOG_FILE = "queries.jsonl"
_MAX_Q = 500              # 问题文本最长存多少字（防灌）
_lock = threading.Lock()

# ⚠️ 脱敏：**只挡联系方式**。中文人名挡不住（见模块头"诚实的边界"）。
_PHONE = re.compile(r"1[3-9]\d{9}")
_LONGNUM = re.compile(r"\d{7,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# 微信号/QQ 这类：用户常写"加我微信 abc123"
_WECHAT = re.compile(r"(微信|weixin|wx|QQ|qq)[号:：\s]*([A-Za-z0-9_-]{5,})", re.I)


def _scrub(text: str) -> str:
    """脱敏：把联系方式打码。**幂等**（重复调用结果不变）。"""
    t = text or ""
    t = _EMAIL.sub("[邮箱]", t)
    t = _PHONE.sub("[手机号]", t)
    t = _WECHAT.sub(lambda m: f"{m.group(1)}[已隐去]", t)
    # ⚠️ 放最后：上面的替换会引入 `[手机号]` 这种带数字的标记，
    #    但那些标记里没有 7 位以上连续数字，所以不会被误伤。
    t = _LONGNUM.sub("[数字]", t)
    return t


def _writing_enabled() -> bool:
    """⚠️⚠️ **测试进程里一律不写盘**（2026-10-13 加，防了一类真会发生的污染）。

    ## 为什么必须有这道闸
    有的测试会**真跑 `handle_ask`**（行为级测试，这是对的 —— 见
    `tests/test_handle_ask_branches_2026_10_13.py` 的用意）。
    而 `handle_ask` 的每个分支都会调 `log(...)` ⇒
    **测试问题被写进真实的 `data/queries.jsonl`**。

    实测踩到：跑一次全量单测，用户数据文件里多出 **38 条**测试串
    （「帮我订张机票」「？」「a」「飞盘飞盘飞盘…」）。

    后果有两层，第二层更严重：
    1. **污染生产数据** —— 报表里混进测试串，真实提问的分布被稀释；
    2. **"用户数据"这个概念失效了** —— `data/queries.jsonl` 本来只该有
       **真人**问过的话。混了测试串之后，它既不是用户数据、也不是测试数据。

    ⚠️ 做法：**在源头挡**，而不是让每个测试自己去隔离。
       我第一版是给 `test_handle_ask_branches` 加临时目录 ——
       但 `test_smalltalk.py` 和两个 audit 测试**也在写**，
       逐个修是打地鼠；而且**以后新写的测试会再犯**。
    ⚠️ 判据：`unittest` 在跑 ⇒ `sys.modules` 里有 `unittest` **且**
       有正在执行的 TestCase。用 `sys.modules` 里有没有
       `unittest.__main__` / 正在 `discover` 不好判，所以退一步用更稳的信号：
       **`sys.argv[0]` 里含 `unittest`**（`python -m unittest` 就是这个形态），
       **或** 环境变量 `DSH_DISABLE_QUERYLOG=1`（给"我要故意跑真服务"用）。
    """
    if os.environ.get("DSH_DISABLE_QUERYLOG") == "1":
        return False
    argv0 = " ".join(sys.argv[:2]).lower()
    if "unittest" in argv0 or "pytest" in argv0:
        return False
    return True


def log(question: str, *, route: str = "", mode: str = "", confident=None,
        citations: int = 0, elapsed_ms=None, retrieval_ms=None,
        generation_ms=None, total_ms=None, provider: str = "",
        answer_len: int = 0, need_clarify=None, why: str = "",
        intent: str = "", extra: dict | None = None,
        probe: bool = False) -> None:
    """记一条。**绝不抛异常** —— 埋点坏了不能影响用户拿到答案。

    ⚠️ 这是**唯一一条**硬约束：埋点是旁路，它挂了主流程必须照常工作。
    ⚠️ 测试进程里**直接返回不写**（见 `_writing_enabled`）。

    ⚠️⚠️ `probe=True` = **这是自动巡检探针，不是用户提问，不要记**。
      为什么加这个参数（2026-10-15，一个我造成的污染）：
        服务器 cron 每 10 分钟跑 `tools/health_check.py`，它会**真问一句**
        `读秒从几开始` 来验证问答链路。而这句提问**被当成用户提问记了下来**
        ⇒ 实测后果：
           · 日志里 `读秒从几开始` 出现 **160 次**（全是探针）
           · 309 条"用户提问"里能确认是真人问的只有个位数
           · ⇒ **整份用户行为日志不可用**：算不出真实提问分布，
             也没法拿它判断用户到底在问什么
      ⇒ 探针要的是"链路通不通"，**不该被统计成一个用户**。

      ⚠️ 为什么用参数而不是线程局部（试过，风险太大）：
        流式路径下生成器的函数体**可能在线程局部被清掉之后才执行**，
        那时候读到的开关就是错的 —— 而这种错**只在流式下偶发**，最难查。
        ⇒ 参数传递虽然要改 9 个调用点，但**每一处都是显式的、看得见的**。
    """
    if probe:
        return
    if not _writing_enabled():
        return
    try:
        p = Path(config.DATA_DIR) / LOG_FILE
        rec = {
            "ts": int(time.time()),
            "q": _scrub(question)[:_MAX_Q],
            "route": str(route or "")[:24],
            "mode": str(mode or "")[:24],
            "intent": str(intent or "")[:16],
            "citations": int(citations or 0),
            "answer_len": int(answer_len or 0),
            "provider": str(provider or "")[:32],
        }
        if confident is not None:
            rec["confident"] = bool(confident)
        if elapsed_ms is not None:
            rec["elapsed_ms"] = int(elapsed_ms)
        # 2026-10-22：把「检索 / 生成 / 端到端」拆开记。
        # ⚠️ `elapsed_ms` 是历史字段（只代表检索那段），别拿它判断"用户觉得慢"。
        for key, val in (("retrieval_ms", retrieval_ms),
                         ("generation_ms", generation_ms),
                         ("total_ms", total_ms)):
            if val is not None:
                rec[key] = int(val)
        if need_clarify is not None:
            rec["need_clarify"] = bool(need_clarify)
        if why:
            rec["why"] = str(why)[:120]
        if extra:
            # ⚠️ 只接受标量，防止有人往里塞用户对象
            rec.update({k: v for k, v in extra.items()
                        if isinstance(v, (str, int, float, bool))})
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:  # noqa: BLE001
        # ⚠️ 静默失败是**故意的**：埋点不能影响用户拿答案，也不能把异常抛给调用方。
        #    排查时看服务日志里有没有这条记录缺失即可（有专门的统计脚本）。
        pass


def read_all() -> list[dict]:
    """读全部记录（给统计脚本/看板用）。坏行跳过，不抛。"""
    p = Path(config.DATA_DIR) / LOG_FILE
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summary() -> dict:
    """一句话统计（给 `/api/stats` 或运维看）。零成本、不读全量。"""
    rows = read_all()
    if not rows:
        return {"total": 0}
    by_route: dict[str, int] = {}
    for r in rows:
        k = str(r.get("route") or "?")
        by_route[k] = by_route.get(k, 0) + 1
    return {"total": len(rows), "by_route": by_route}
