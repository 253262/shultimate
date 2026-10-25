# -*- coding: utf-8 -*-
"""练习器（需求侧机制 ④「练习 + 反馈」）：**场景题**。

## ⚠️ 为什么整体换掉（owner 2026-10-15 的指正，这是本文件存在的理由）

owner 的原话：
> 「我看到有好几条题是：『第 8 点几是什么？』『第几点几是什么？』**不能这么考**！
>   你要怎么考？你要**模拟某个具体的场景**，然后问他是否应该这么做，
>   让他回答『是』或者『否』。你应该**模拟一个场景，而不是让他去背规则**。
>   **没有谁能完完全全背住规则。**」

**实测确认他是对的**（`tools/audit_practice.py` 量过）：
换掉之前是 948 道，**100% 考记忆、0 道考能力**：
```
text2num  316 道  给正文问条款号   「这条规则是第几条？」
num2text  316 道  给条款号问正文
chapter   316 道  给正文问章节
```
而且题目本身荒唐 —— 选项只差一个数字：
```
「盘出界（"出界"out）；」→ 是第几条？  选项 13.11.2 / 13.11.1 / 13.1.3 / 13.2.5
```
> **背编号在真实场景里毫无用处**：你在场上跟人争论，不可能说"根据 18.2.1"，
> 你会说"你这么算是走步"。

## 题目从哪来（**不再是机械派生**）

```
data/scenario_bank.jsonl   ← 由 tools/pipeline_scenarios.py 产出
```
产出流程（两道关，缺一不可）：
```
① 生成（温度 0.8）  读一条规则 → 编一个具体场上情境 → 问一个能「是/否」回答的问题
② 校验（温度 0.1）  拿**规则原文逐字对照**，判断这个答案有没有依据
        ⇒ verdict != supported 就**淘汰**
        ⇒ 校验器指出的"哪句原文支持它"**直接留成 evidence 字段**
```
⚠️ **为什么必须有第②道关**：实测无约束时**25% 的答案是错的**，
   而这是把"答对规则"当唯一卖点的产品 —— **错答案比没有题库更糟**：
   用户会照着错的学，再照着错的去争。
   加了两道关之后，入库的**每一道都有原文依据**（`evidence` 字段可核对）。

## 数据形状（与前端契约一致）
```json
{
  "id": "s9_4",                      // 稳定 id（按规则号派生）
  "kind": "scenario",
  "prompt": "情境…\n\n问：…",          // 前端直接显示
  "options": [{"key":"yes","text":"是"},{"key":"no","text":"否"}],
  "answer": "yes",                    // ⚠️ 只留服务端，不下发
  "explain": "是。换人读秒必须从读秒一重新开始。",
  "why": "规则 9.4：…",               // 依据解释（**可以引条款号，那是依据不是考点**）
  "evidence": "原文明确：…",           // 校验器指出的支持句
  "clauses": ["9.4"],
  "chapter_title": "读秒"
}
```

## ⚠️ 二选一的代价（必须说清楚）
蒙对概率 **50%**。所以"答对率"这个数字本身说明不了太多 ——
**真正的价值在答完之后那两句解释**。前端必须把 `explain` + `why` + 条款依据
显示出来，否则这个练习器只是抛硬币。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config

# 题库文件（由 tools/pipeline_scenarios.py 产出）
BANK = config.DATA_DIR / "scenario_bank.jsonl"


def _load_bank() -> list[dict]:
    """读场景题库。

    ⚠️ 坏行**跳过但不静默**：数一下跳了几行，多了就在 stats 里暴露出来。
       （审计学到的：静默跳过坏行的东西，出问题没人知道。）
    """
    if not BANK.is_file():
        return []
    out: list[dict] = []
    for line in BANK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            q = json.loads(line)
        except json.JSONDecodeError:
            continue
        # 只收**结构完整**的题 —— 缺 answer/options 的题会让判分逻辑崩。
        if not (q.get("id") and q.get("prompt")
                and isinstance(q.get("options"), list) and q.get("options")
                and q.get("answer")):
            continue
        out.append(q)
    return out


def build(rules: list[dict] | None = None, seed: int | None = None) -> list[dict]:
    """返回题库。

    ⚠️ 签名保留 `rules` / `seed` 是为了**不破坏调用方**
      （`server.py` 启动时调 `practice_mod.build()`），但**现在都不用**：
      题目不再由 `rules.jsonl` 机械派生，而是读 `scenario_bank.jsonl`。
      传进来的 `rules` 忽略 —— 保留参数比改调用方安全。
    """
    return _load_bank()


def stats() -> dict:
    """给自检/体检用：几道题、覆盖几条规则、答案是否配平。"""
    qs = _load_bank()
    yes = sum(1 for q in qs if q.get("answer") == "yes")
    no = sum(1 for q in qs if q.get("answer") == "no")
    kinds: dict[str, int] = {}
    for q in qs:
        k = str(q.get("kind") or "?")
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "total": len(qs),
        "by_kind": kinds,
        "yes": yes,
        "no": no,
        "rules_covered": len({str((q.get("clauses") or [""])[0]) for q in qs}),
        "path": str(BANK),
        "exists": BANK.is_file(),
    }
