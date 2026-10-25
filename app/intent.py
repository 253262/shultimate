# -*- coding: utf-8 -*-
"""诉求分路：先判断「这个人到底要什么」，再决定怎么答。

**为什么要有它**（2026-10-06 owner 定的产品方向）
owner 原话：
  「一个产品要让用户觉得好用，第一件事是**要能分析出用户提问的根本目的和核心诉求**。
    比如，用户是单纯无聊来询问，还是只想查找某一条具体规则？
    或者是来找你诉苦，需要你一边提供情绪价值，一边把规则给讲透？
    分析出核心诉求后，说出能够满足这个诉求的话术。」

四类诉求：

  chat    闲聊 / 元问题（打招呼、道谢、问你能不能用）      → 不检索，一两句（见 smalltalk.py）
  vent    诉苦 / 觉得被规则占了便宜，要的是「搞明白 + 被接住」 → **照常检索**，多一句情绪承接
  lookup  只要某一条的**原文**                                → 给逐字原文，不套五段结构
  rule    普通规则问题（默认）                                → 一切照旧

⚠️⚠️ **本模块只改"表达方式"，绝不动"依据"**：
  · vent 仍然是完整 RAG（检索 → 只依据条款作答），情绪承接只有一句，
    而且**不许因为这一句把回答拉长、不许因为照顾情绪而放宽"规则没规定"**；
  · lookup 也仍然只从检索到的条款里取字，条款里没有的**一个字都不补**。
  · 铁律「不确定就说不确定」在四条路上都生效 —— 分路不是给编造开门。

⚠️ **方向必须「宁漏勿错」**（与 smalltalk.py 同规矩）：
  判不准就回落到 rule —— 那是最保守、也是原来就正确的那条路。
  错判的代价：vent 多一句共情（轻微）、lookup 少讲一点分析（轻微）；
  而少判的代价是 0。所以词表只收**特异性高**的说法，宽词一律不要。

⚠️ 本模块**不参与任何打分**，只决定走哪条路（同 `routes.DOMAIN_WORDS`）。
"""
from __future__ import annotations

import re

from . import routes
from . import smalltalk

INTENT_CHAT = "chat"
INTENT_VENT = "vent"
INTENT_LOOKUP = "lookup"
INTENT_RULE = "rule"

# ---------------------------------------------------------------------------
# vent：诉苦 / 被占了便宜
# ---------------------------------------------------------------------------
# 两类词，命中**任意一类**即可 —— 它们都在描述"事后心里过不去"，
# 而不是在问"规则怎么规定"。
VENT_MOOD: list[str] = [
    # 情绪本身
    "气死", "好气", "很气", "生气", "憋屈", "窝火", "郁闷", "不爽",
    "火大", "恼火", "委屈", "恶心", "无语", "忍不了", "受不了",
    # 觉得不公 / 被占了便宜（owner 原话里的那批说法）
    "被坑", "坑了", "吃亏", "占了便宜", "被占便宜", "利用规则", "钻空子",
    "不公平", "不公正", "偏袒", "耍赖",
    # 对方咬死不改
    "死不承认", "死不认", "就是不认", "坚持他没错", "坚持自己没错",
    "歪理", "强词夺理", "嗓门大", "谁大声谁有理", "大声就有理",
    # 想讨个说法
    "说理", "评理", "讨说法", "讲不通", "跟他讲",
]
# ⚠️ 刻意**不收**的词（都踩过或想过，逐条记下理由）：
#   · 裸"不认" / "不服"  —— 「判罚程序」族与「诚实以对」族已各有归属，
#     而且"他不认这个判罚"是在问**怎么办**（流程题），不是诉苦。
#   · "不公平"只收四字全称 —— "公平"是 DOMAIN_WORDS 里的域内词，
#     放进这里会把一切泛泛而谈的问题标成诉苦。
#   · "太过分了""离谱"  —— 队友之间互相吐槽队友也会这么说，
#     与"被对手用规则占了便宜"不是一件事。

# ---------------------------------------------------------------------------
# lookup：只要某一条的原文
# ---------------------------------------------------------------------------
# 形如 17.6 / 18.1.1.5 / 第 17.6 条。
_CLAUSE_REF = re.compile(r"(?<![\d.])(\d{1,2}\.\d{1,2}(?:\.\d{1,2})*)(?![\d.])")
# 紧跟这些量词的数字不是条款号（"3.4 米""1.5 秒"）。
_UNIT_CHARS = "米秒厘毫公克斤人次分时度个年月日周倍%％折"

LOOKUP_HINTS: list[str] = ["原文", "逐字", "一字不差", "原话是怎么", "怎么写的"]


def _clause_ref(text: str) -> bool:
    """句子里有没有"像条款号"的引用？

    ⚠️ 两道闸：① 章号必须在 1~20（WFDF 主规则就 20 章）；
    ② 后面紧跟量词的不算（"3.4 米"是尺寸不是条款）。
    实测 447 条真实问法里含小数数字的 **0 条**，所以这里几乎零误伤。
    """
    for m in _CLAUSE_REF.finditer(text):
        try:
            chapter = int(m.group(1).split(".")[0])
        except ValueError:      # pragma: no cover - 正则已保证是数字
            continue
        if not 1 <= chapter <= 20:
            continue
        # ⚠️ 必须先跳过空白再取下一个字：队员打的是「3.4 米」「1.5 秒」，
        #    中间那个空格会让"紧跟量词"这道闸整个失效（探针实测到过）。
        nxt = text[m.end():m.end() + 4].lstrip()[:1]
        if nxt and nxt in _UNIT_CHARS:
            continue
        return True
    return False


def is_vent(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = routes._norm(t)
    return any(h in low for h in VENT_MOOD)


def is_lookup(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = routes._norm(t)
    if _clause_ref(low):
        return True
    return any(h in low for h in LOOKUP_HINTS)


def classify(text: str) -> str:
    """把一句话归到四类诉求之一。

    ⚠️ 顺序是这个函数的全部：**chat 必须最先判**。
      「谢谢」也含情绪词吗？不含，但「气死我了」这种就同时像闲聊又像规则题 ——
      先过 chat 才能保证"根本不是规则问题"的那批不落到后面去。
    ⚠️ lookup 排在 vent 前面：`17.6 原文是什么` 是纯粹的取文件需求，
      给原文就是满足诉求，加一句共情反而是噪音。
    """
    t = (text or "").strip()
    if not t:
        return INTENT_RULE
    if smalltalk.is_smalltalk(t):
        return INTENT_CHAT
    if is_lookup(t):
        return INTENT_LOOKUP
    if is_vent(t):
        return INTENT_VENT
    return INTENT_RULE
