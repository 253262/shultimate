# -*- coding: utf-8 -*-
"""附录注解：收录「规则**正文把答案外包给了附图 / 附录**」的硬事实。

**为什么需要它（2026-10-06 owner 实测）**
    owner 问「飞盘场地有多大」，系统答的是：
        规则里没有给出具体尺寸数字 —— 条款只说场地是长方形，尺寸和分区见图 1 …
    **这句话是对的**（正文 2.1 确实只有"尺寸和分区见图 1"一句），但用户拿不到尺寸
    —— owner 原话：「你觉得用户听到这个回答之后会满意吗？」。

    根因**不是模型不听话，是内容缺口**：368 条语料只收了正文，
    而官方把数字放在**附图**和**附录**里，一条都没进来。
    于是"只能依据条款回答"这条铁律，唯一正确的行为就是拒答 —— 系统是"守规矩"
    地答错的。修法只能是**补内容**，绝不能靠"放宽铁律 / 允许模型凭常识补数字"。

**⚠️ 判定纪律：先分清"行为缺陷"还是"内容缺口"**（别一上来就改提示词）
    ① 行为缺陷 —— 条款里有，模型没答对、没答准、答啰嗦了 ⇒ 改提示词 / 改检索。
    ② 内容缺口 —— 条款里确实没有，事实在图 / 附录 / 附录表里 ⇒ **往这里加一条**。
    分不清就先跑 `tools/exp_answer_shape.py` 真看一眼模型拿到了什么材料。

**来源纪律（红线，别绕）**
    - 每条必须有 `source`，写清出处（附录 A1.2 / 图 1），**并且写明"非正文条款"**。
    - **绝对不要**给注解编一个正文条款号。citations 在前端是"规则原文"的可信呈现，
      把附录内容伪装成正文条款 = 拿可信度换覆盖率，这是本项目最贵的资产。
    - 数字必须来自**官方一手文档**（WFDF 官网 PDF），核过再写。
      网络二手内容不归这里 —— 那是 app/weblookup.py 的职责（且带"未复核"标注）。
    - `verified` 字段写清"什么时候跟哪份原文核的"，便于下一轮复查。

**零第三方依赖**：JSONL + 内存索引，与项目其余部分一致。
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data" / "appendix_notes.jsonl"

# 必需字段。缺任何一个都视为坏条目，**整条丢弃**（宁可少答，不可答错来源）。
_REQUIRED = ("id", "for", "label", "text", "source")


def _load() -> list[dict]:
    if not _DATA.exists():
        return []
    out: list[dict] = []
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue                     # 坏行跳过，不让一条脏数据毁掉整层
        if not all(row.get(k) for k in _REQUIRED):
            continue
        ids = row["for"]
        if isinstance(ids, str):
            ids = [ids]
        ids = [str(i).strip() for i in ids if str(i).strip()]
        if not ids:
            continue
        row["for"] = ids
        out.append(row)
    return out


def all_notes() -> list[dict]:
    """全部注解（按文件顺序，人工维护，不需要排序）。"""
    return _load()


def notes_for(clause_id: str) -> list[dict]:
    """某条条款（或术语定义 id）名下挂的注解。没有就返回空列表。"""
    cid = str(clause_id or "").strip()
    if not cid:
        return []
    return [n for n in _load() if cid in n["for"]]


def attach(hits: list[dict]) -> list[dict]:
    """给命中的语块挂上它名下的【附录注解】。

    ⚠️ **只加内容，不改 `_score`、不改排序、不新增语块** ——
       打分与族是 `app/routes.py` 与检索的领域（见 AGENTS.md 红线 8 / 9）。
       这里做的是"正文指向图 / 附录时，把官方文档里的事实一并递给模型"，
       所以**必须挂在正文条目名下**（`for` 指向哪条正文，就跟哪条一起出现）。
    """
    for h in hits:
        notes = notes_for(h.get("id"))
        if notes:
            h["_notes"] = notes
    return hits


def stats() -> dict:
    rows = _load()
    return {
        "total": len(rows),
        "clauses": sorted({c for r in rows for c in r["for"]}),
        "path": str(_DATA),
    }
