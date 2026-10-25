#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「圈外的点」：赛事设置知识（与规则语料**分开存放、分开引用**）。

## 为什么有这个模块（2026-10-13 owner 定的架构）
Owner 的比喻是**画一个圈**：

```
   ┌──────── 圆圈：规则语料（基本不变）────────┐
   │  data/rules.jsonl  = WFDF《团队飞盘规则》  │
   │  全国赛事都执行它，内容稳定                │
   └───────────────────────────────────────────┘

   · 圈外的点：主办方按比赛情况定，**场场可不同** ·
     这场 75 分钟 · 那场 65 分钟 · 另一场 60 分钟…
     目标分、性别比例、暂停次数、中场触发点、净时间…
```

⚠️ **语料不换**（owner 明确否决了"把语料换成官方 2025-2028 中文版"）。
   本模块只**增加**圈外的点，一个字都不动 `rules.jsonl` / `index.json` /
   `embeddings.bin`。

## 三条硬规矩（owner 特别强调的）
1. **不写死某一场的时长**。规则给的只是**基准**（4.2：先得 15 分获胜，
   通常约 100 分钟）；实际值由主办方定。已知实例：2026 男子公开赛
   75 分钟 / 决赛 85 分钟；2026 全国锦标赛 65 分钟 / 决赛 75 分钟。
   **同一年、同一个主办单位，两场就不一样。**
2. **适度留白**。被问到"这场比赛多久"而该场不在已知列表里 ⇒
   必须回「取决于该场竞赛规程，建议问组委会」，**不许编**。
3. **每条带出处**。来自规则原文 / 竞赛规程 / 社体中心细则 —— 要能说出来。
   用户必须分得清"规则这么规定"和"这场比赛这么定"。

## 为什么不用向量检索
条目只有十几条，用**词面相似度**就够；而且走向量就要重建索引
（`index.json` / `embeddings.bin`），那是**圈内**的东西，不该为圈外的点重算。
⇒ 本模块**零依赖、零 API、不碰索引**。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from . import config

COMPETITION_FILE = "competition.jsonl"

# 命中门槛。
#
# ⚠️ **判据是「覆盖率」，不是 Dice**（2026-10-13 第一版踩过）：
#   问句只有 3~10 字、条目正文 100~200 字 ⇒ 用 Dice 时分母被条目长度拉大，
#   实测**句句无命中**（「这场比赛打多久」对任何条目都 < 0.16）。
#   覆盖率 = 问句里有几个字在条目中出现 ÷ 问句总字数 ——
#   分母只跟问句走，短问句不再被罚。
#
# ⚠️ 还要**去掉泛字**：问句里的「的/是/有/这/么/吗」在几乎任何条目里都能找到，
#   留着会把覆盖率整体抬高、失去区分度（实测「帮我订张机票」也能过线）。
_MIN_COVER = 0.55
_MAX_HITS = 4

# 高频泛字：参与匹配只会制造噪声。都是**单字**，不会吃掉实词。
# ⚠️ 只放真正的功能字，不放"比赛/规则"这类实词 ——
#    去掉实词会让「比赛打几分」匹配不到讲目标分的条目。
_STOP = set("的了是有这那么吗呢吧啊在会要能可和对与及也就都还只被把让给从到个之其")

_lock = threading.Lock()
_cache: list[dict] | None = None
_cache_mtime: float | None = None

_PUNCT = re.compile(r"[\s，。、！？；：\"'“”‘’（）()《》【】\[\]…—\-·,.!?;:/\\|]+")


def _norm(t: str) -> str:
    return _PUNCT.sub("", (t or "").lower())


def _bigrams(t: str) -> set[str]:
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _dice(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def load() -> list[dict]:
    """读赛事条目。文件不在 ⇒ 返回空 list（**不报错** —— 它是可选增强）。

    ⚠️ 带 mtime 缓存：改了文件不用重启进程也能生效（与 curated 的做法一致）。
    """
    global _cache, _cache_mtime
    p = Path(config.DATA_DIR) / COMPETITION_FILE
    if not p.is_file():
        return []
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    with _lock:
        if _cache is not None and _cache_mtime == mtime:
            return _cache
        items: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("id") and o.get("text"):
                items.append(o)
        _cache, _cache_mtime = items, mtime
        return items


def _haystack(o: dict) -> set[str]:
    """参与匹配的字面：主题 + 正文 + 适用范围。

    ⚠️ **刻意不用 `note`** —— note 是给维护者看的（含"不许编"这类元指令），
       混进匹配面会让它被无关问题命中。
    """
    return _bigrams(_norm(" ".join([
        str(o.get("topic") or ""),
        str(o.get("text") or ""),
        str(o.get("applies_to") or ""),
    ])))


def _query_units(text: str) -> list[str]:
    """把问句切成匹配单元：先按字，再给相邻两字成对。

    ⚠️ 用**字**做主单元（短问句里字比 bigram 多，更不容易全落空），
       bigram 只作为加成。
    """
    q = _norm(text)
    chars = [c for c in q if c not in _STOP]
    return chars


def _kw_hit(query: str, o: dict) -> tuple[int, float, str | None]:
    """问句是否命中该条目的某个关键词。返回 (词长, 得分, 命中的词)。

    ⚠️ **短关键词要单独对待**（2026-10-13 踩过）：第一版要求关键词长度 >= 3，
       于是「多重」「几克」这类 **2 字关键词全被排除**，实测「盘有多重」无命中。
       ⇒ 规则改成：**>= 3 字**要求"是问句的子串"；
         **正好 2 字**只在**问句就等于它**时才算（避免"比赛"这类词命中一片）。
    """
    q = _norm(query)
    if not q:
        return 0, 0.0, None
    best_len, best_kw = 0, None
    for k in (o.get("keywords") or []):
        kn = _norm(str(k))
        if len(kn) >= 3 and kn in q:
            if len(kn) > best_len:
                best_len, best_kw = len(kn), k
        elif len(kn) == 2 and kn == q:
            if best_len == 0:
                best_len, best_kw = 2, k
    if not best_kw:
        return 0, 0.0, None
    # 2 字精确命中给 0.80；3 字 0.85 起，每多一字 +0.05，封顶 0.98
    score = 0.80 if best_len == 2 else min(0.98, 0.85 + 0.05 * (best_len - 3))
    return best_len, score, best_kw


def score_one(query: str, o: dict) -> float:
    """问句对单条的分数。

    = max(关键词命中分, 覆盖率分)
      · 关键词命中：**问句里出现这个词** ⇒ 强信号
      · 覆盖率：问句实词有多少在条目里出现（兜住没写关键词的问法）
    """
    _, kw_score, _ = _kw_hit(query, o)

    chars = _query_units(query)
    if not chars:
        return round(kw_score, 4)
    hay = _haystack(o)
    hay_chars = {t[0] for t in hay} | {t[1] for t in hay}
    hit = sum(1 for c in chars if c in hay_chars)
    base = hit / len(chars)
    qb = _bigrams("".join(chars))
    bonus = (len(qb & hay) / len(qb)) if qb else 0.0
    cov_score = 0.7 * base + 0.3 * bonus
    return round(max(kw_score, cov_score), 4)


def search(query: str, limit: int = _MAX_HITS) -> list[dict]:
    """按覆盖率找相关赛事条目。返回带 `_score` 的副本，不改缓存。"""
    if not _norm(query):
        return []
    scored: list[tuple[float, dict]] = []
    for o in load():
        s = score_one(query, o)
        if s >= _MIN_COVER:
            scored.append((s, o))
    scored.sort(key=lambda x: -x[0])
    out = []
    for s, o in scored[:limit]:
        it = dict(o)
        it["_score"] = round(s, 4)
        out.append(it)
    return out


def stats() -> dict:
    items = load()
    kinds: dict[str, int] = {}
    for o in items:
        kinds[str(o.get("kind") or "?")] = kinds.get(str(o.get("kind") or "?"), 0) + 1
    return {"total": len(items), "by_kind": kinds,
            "file": str(Path(config.DATA_DIR) / COMPETITION_FILE)}
