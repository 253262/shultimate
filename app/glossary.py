# -*- coding: utf-8 -*-
"""答案里的术语白话解释：**零编造**，全部取自 `data/definitions.jsonl` 官方名词定义。

**为什么要有它**（2026-10-10）
产品目的写在 `AGENTS.md`：让大家都能够懂飞盘规则。
实测（对线上真实提问）发现真正的断点不在"答不出"，而在**答了也读不懂**：

    问：我是新手第一次打比赛，什么算得分
    答：得分就一句话：你在对方得分区里接稳盘，就算得分(14.1)。
        具体要同时满足两条：接盘时你所有触地点都在进攻得分区内…
        得分的时间点算你**确立盘权那一刻**…

一个刚打第一次的新手，看到 `得分区`、`触地点`、`确立盘权` 这几个词，
和看到英文原文没有本质区别 —— 他还得自己去查每个词。
而 `data/definitions.jsonl` 里**本来就有**这 40 条官方名词定义，白放着不用。

⇒ 所以：答案里出现了术语、正文却没解释它时，就在答案末尾补一小段白话解释。

**为什么不是加示例问题**（这条路已经被否决过一次）
`static/app.js` 顶部记着一条已定纪律：首屏示例问题被全部删除，
理由是"它们在替用户做决定 + 显得居高临下"。
本模块走的是相反方向：**用户已经问了、已经得到答案了**，
我们不替他决定该问什么，只把他读不懂的词翻译掉。这不违反那条纪律。

⚠️⚠️ 三条铁律：
  1. **一个字都不许模型编。** 本模块**不调用大模型**，纯字符串匹配 +
     从 `definitions.jsonl` 里整句取原文。模型生成的答案里没写的解释，
     这里也绝不会补。
  2. **默认不抢戏。** 解释块默认折叠（走 `<!--more-->` 同一套折叠机制），
     不进直答段。老队员看到的是原本那条答案，不多一字。
  3. **只解释"正文没解释的"。** 如果答案正文里已经解释了这个词
     （或已经出现过它的官方定义），一律不重复贴 —— 重复比不贴更劝退。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_DEF_FILE = Path(__file__).resolve().parent.parent / "data" / "definitions.jsonl"

# 术语在答案里最多解释几个。
# 定 4 个是量出来的：新手问题里最多同时出现 4 个生词，
# 再多就是一整篇词典，违背"解释服务于读懂"而不是"展示我们有多少词条"。
MAX_TERMS = 4

# 高频却零解释门槛的词：贴定义纯属噪音，纯占名额。
# ⚠️ 判据是**中文里不用学就懂**（不是"规则里很简单"）——
#    `队员`/`进攻队员` 谁第一次看规则都知道是什么，
#    但 `确立盘权`/`非轻微接触` 不查就永远不知道。
# ⚠️ 这类词里**没有一个是黑掉新手看不懂的**：
#    真门槛高的（触地、盘权、防守、轻微接触）全部留在表里。
_NO_GAIN: set[str] = {
    "队员", "进攻队员", "防守队员", "比赛场地", "得分区", "得分线",
    "中心区", "传盘", "投掷", "比赛中止", "比赛/回合",
}

# 答案里出现这个词，就算"正文可能已经解释过了"。
# ⚠️ 这几个是**元问题**（"下面解释一下确立盘权"）而不是真解释，
#    所以只当作"跳过"依据，不当作"已解释"依据 —— 真解释判据见 `_is_explained`。
_META_HINTS = ("意思是", "指的是", "也就是说", "即", "所谓")

_INDEX: dict[str, dict] | None = None

# 派生形式 → 官方术语。
# ⚠️ 为什么要这张表（2026-10-10 实测）：
#   官方名词定义收的是"盘权（Possession of the disc）"，但规则正文和所有
#   真实答案里出现的都是**动词形态「确立盘权」** —— 那是新手最需要解释的词，
#   按字面查表却查不到，只能漏过去。派生形式与原词在规则里同义，
#   指向同一条官方定义，所以这里直接指向它，**不新增任何解释**。
# ⚠️ 铁律：这里**只允许放与原词严格同义的形态**。
#   一旦某个派生词的意思和原词有差别，就会变成误导 —— 那必须删掉，而不是留在表里。
#
#   ⚠️⚠️ 反面教材（2026-10-10 上线前自查拦下）：曾写过 "非轻微接触": "轻微接触"。
#   官方名词定义只有「轻微接触」，而**"非轻微接触"在那条定义里的出现方式恰恰是
#   反义**（原文："碰到…对手的手臂/手…**不算轻微接触**"）——
#   把"轻微接触"的定义贴给"非轻微接触"，等于对着新手说反话。
#   宁可漏解释，也不许贴一条意思相反的。这就是本表存在的全部理由。
_DERIVED: dict[str, str] = {
    "确立盘权": "盘权",
    "丢失盘权": "盘权",
    "进攻盘权": "盘权",
}


def _load() -> dict[str, dict]:
    """载入术语索引。term 与 alias 都作为键（英文术语同样要能命中）。"""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    idx: dict[str, dict] = {}
    try:
        raw = _DEF_FILE.read_text(encoding="utf-8")
    except OSError:
        _INDEX = idx
        return idx
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        term = (d.get("term") or "").strip()
        text = (d.get("text") or "").strip()
        # ⚠️ 官方名词定义里本身有几条是纯转引（"见"扔盘动作（throwing motion）""），
        #    这种**没有信息量**，贴给新手等于贴了一句废话，还占掉 4 个名额。
        #    一律不收 —— 让它空着名额给真正有解释的词。
        if not term or not text or text.startswith("见") or len(text) < 8:
            continue
        rec = {"term": term, "alias": (d.get("alias") or "").strip(), "text": text,
               "id": d.get("id") or ""}
        for k in (term, rec["alias"]):
            if k and k not in idx:
                idx[k] = rec
    # 派生形式挂上去（同名即覆盖为自身，无需特判）。
    for deriv, base in _DERIVED.items():
        if base in idx and deriv not in idx:
            rec = dict(idx[base])
            # 显示名用正文里出现的那个形态 —— 新手是在答案里看到「确立盘权」，
            # 解释块里却写「盘权」，他还得自己对一次。
            rec["term"] = deriv
            idx[deriv] = rec
    _INDEX = idx
    return idx


def _is_explained(answer: str, rec: dict) -> bool:
    """答案正文里是不是已经解释过这个术语了？

    判据：正文里出现了该定义文本的**前 12 个字**（去掉空白后比）。
    取 12 字是为了容纳"队员在以下情况确立盘权：接住传盘…"这类前缀差异，
    又不至于短到"意思是"三个字就误判成已解释。
    """
    probe = re.sub(r"\s+", "", rec["text"])[:12]
    if len(probe) < 8:
        return False
    flat = re.sub(r"\s+", "", answer)
    return probe in flat


def terms_in(answer: str) -> list[dict]:
    """扫出答案里出现、且正文没解释的官方术语。返回 [{term, text}]。

    **最长匹配优先**：先按术语长度降序扫，命中就从后面把它整段挖掉。
    理由：答案里常同时出现 `得分区` 和 `进攻得分区`，
    如果先命中短的 `得分区`，`进攻得分区` 就再也匹配不到了，
    而后者才是答案真正在用的那个词。
    """
    idx = _load()
    if not idx:
        return []
    text = answer
    found: list[dict] = []
    # 长词先扫：同一批键按字符长度降序，中英混排也不影响排序。
    for key in sorted(idx, key=len, reverse=True):
        if len(key) < 2:
            continue
        if key not in text:
            continue
        rec = idx[key]
        if rec["term"] in _NO_GAIN or key in _NO_GAIN:
            text = text.replace(key, "　" * len(key))
            continue
        if any(f["text"] == rec["text"] for f in found):
            continue                      # 同一条定义，别重复
        # ⚠️ 已选的某条解释**文本里已经包含**这个词，就不必再单列 ——
        #   实测「进攻得分区」的解释是"该队当前试图得分的那一侧得分区"，
        #   读到这里他已经知道"得分区"是什么了，再贴一条纯粹是凑数。
        if any(key in f["text"] for f in found):
            text = text.replace(key, "　" * len(key))
            continue
        # ⚠️⚠️ 「是否已解释」必须拿**原文**判，不能拿挖空后的 text 判。
        #   实测踩过（2026-10-10）：`队员` 命中后被挖成空格，
        #   紧接着 `盘权` 的判据 probe 是「队员在以下情况确立盘权：」——
        #   开头那两个字刚被挖没了，判据就假阴性，**明明正文已经解释过却重复贴了一遍**。
        #   text 只负责"这个词还没被扫过"，正文判据一律回原文 answer。
        if _is_explained(answer, rec):
            text = text.replace(key, "　" * len(key))
            continue
        # ⚠️ 命中后把该词整段挖掉：否则 `防守队员` 命中完，
        #    里面的 `队员` 还会再命中一次，白占一个名额。
        text = text.replace(key, "　" * len(key))
        found.append(rec)
        if len(found) >= MAX_TERMS:
            break
    return found


def block(answer: str) -> str:
    """生成术语解释块（Markdown，可直接接在答案末尾）。没命中返回空串。

    ⚠️ 这里**只输出词条本身**，不写标题 ——
    标题由前端渲染（`glossaryNode`）负责，且标了"官方名词定义"来源。
    早先两边各写一遍，实测渲染出来是
        「上面这几个词的意思 · 官方名词定义」＋「上面这几个词的意思：」
    —— 同一句话说两遍。**职责只能有一处**，所以标题归前端。
    """
    terms = terms_in(answer)
    if not terms:
        return ""
    lines = []
    for t in terms:
        lines.append(f"- **{t['term']}**：{t['text']}")
    return "\n".join(lines)


def has_glossary(answer: str) -> bool:
    return bool(block(answer))