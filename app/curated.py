# -*- coding: utf-8 -*-
"""疑问库：Owner 亲自回答过的问题，成为**最高优先级**的知识来源。

**为什么需要它（2026-10-04）**
    向量检索在飞盘这种"单一领域、术语高度一致"的语料上区分度不足：
    413 条余弦中位 0.348、90% 0.490、前 5% 全挤在 0.07 宽的带子里。
    于是有两类问题系统永远答不对：
      ① 规则里**确实有**这条，但排第 12~36 进不了 TOP_K=8（维护者的
         "得了分之后还要停下来吗"就是这种，14.2 排 #20）；
      ② 规则里**真没有**（本地赛制、器材、约定），但 Owner 知道答案。
    这两类都不该由模型硬编，也不该永远拒答 —— 应该"问 Owner"。
    Owner 答完写进这里，之后同类问题直接命中，**不再依赖检索**。

**优先级：疑问库 > 规则语料 > 联网兜底**
    人工裁定的可信度高于自动检索，这是本项目"不确定就说不确定"
    铁律的延伸：拿不准就问，问到了就记住。

**零第三方依赖**：JSONL 追加写 + 简单字面/关键词匹配。
    条目数量是人工级（几十~几百条），不需要向量检索。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data" / "curated.jsonl"

# ⚠️⚠️ 写锁（2026-10-09 加）。**这把锁不是保险，是必需品。**
# server 用的是 ThreadingHTTPServer（每请求一线程），而：
#   · add()    是独立的 open("a") append —— 各线程各持一个文件偏移
#   · delete() 是**无锁的读-改-写**：_load() 读全量 → 重写全量 → tmp.replace()
# 两个真实丢数据的时序：
#   ① add 落在 delete 的「读」和「写」之间 ⇒ 新裁定被 replace 整个抹掉，
#      界面上「保存裁定」返回成功、paint() 重画，**刷新后条目不见了**；
#   ② 两次 add 并发 ⇒ 两行内容交错 ⇒ 产出半条 JSON，
#      而 _load() 对坏行是 continue 静默跳过 ⇒ **这条裁定从此永不命中**，
#      Owner 却以为它在库、以为它生效。
# 疑问库是本项目**唯一**能绕过整个检索+生成层、把文本当「人工核定」权威答案
# 吐给用户的通道 ⇒ 这里的静默丢失是"看起来有依据、实际没有"，比报错更坏。
# _load() 也必须在锁内：否则它可能读到 replace 进行中的半截内容。
_LOCK = threading.RLock()   # ⚠️ 必须是 RLock 不是 Lock：delete() 持锁后还会调 _load()

# 来源类型（决定回答里怎么标注，也决定可信度排序）
SOURCE_OWNER = "owner"      # Owner 亲自回答 —— 最高可信度
SOURCE_WEB = "web"          # 联网兜底抓到、Owner 未复核 —— 必须标注"未经复核"

# 与语义等价组一致：这些条号互相打通，命中任一即算命中
_EQUIV: list[set[str]] = [
    {"2.3", "11.1"},
    {"4.5", "7.1"},
]


def _norm(s: str) -> str:
    return re.sub(r"[\s，。？！、,.?!：:；;]", "", (s or "").strip().lower())


def _load() -> list[dict]:
    """⚠️ 读也必须在锁内（2026-10-09 加）—— 否则可能读到 delete() 的
    replace 进行到一半的中间态。虽然 replace 是原子的、理论上只会读到
    旧或新完整版，但 read_text 与 replace 竞态时**没有任何保证**，
    而坏行会被下面 continue 静默跳过 ⇒ 静默少一条裁定。
    ⚠️ 必须用 RLock：delete() 已持有锁时还会调 _load()，普通 Lock 会自锁死。"""
    with _LOCK:
        return _load_locked()


def _load_locked() -> list[dict]:
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
            continue          # 坏行跳过，不让一条脏数据毁掉整个库
        if row.get("question") and row.get("answer"):
            out.append(row)
    return out


def all_items() -> list[dict]:
    """全部条目，按加入时间倒序（新的在前）。"""
    return sorted(_load(), key=lambda x: x.get("created_at", 0), reverse=True)


def add(question: str, answer: str, clauses: list[str] | None = None,
        keywords: list[str] | None = None, source: str = SOURCE_OWNER,
        by: str = "") -> dict:
    """写入一条裁定。返回落盘后的条目。"""
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    if not answer:
        raise ValueError("答案不能为空")
    if source not in (SOURCE_OWNER, SOURCE_WEB):
        source = SOURCE_OWNER

    item = {
        "id": f"c{int(time.time() * 1000):x}",
        "question": question,
        "answer": answer,
        "clauses": [str(c).strip() for c in (clauses or []) if str(c).strip()],
        "keywords": [str(k).strip() for k in (keywords or []) if str(k).strip()],
        "source": source,
        "by": (by or "Owner").strip(),
        "created_at": time.time(),
    }
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    # ⚠️ append 也要在锁内：见 _LOCK 处的说明②（并发写交错 ⇒ 半条 JSON）
    with _LOCK:
        with _DATA.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def delete(item_id: str) -> bool:
    """按 id 删除。裁定是"改"不是"堆"——过期的裁定必须能撤掉。

    ⚠️ 整个读-改-写必须在**同一把锁内**，否则见 _LOCK 处的说明①
    （并发 add 会被 replace 静默吃掉，而调用方收到的是"成功"）。
    """
    with _LOCK:
        rows = _load_locked()
        keep = [r for r in rows if r.get("id") != item_id]
        if len(keep) == len(rows):
            return False
        tmp = _DATA.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep),
                       encoding="utf-8")
        tmp.replace(_DATA)
    return True


# ---- 匹配 ------------------------------------------------------------------

# 中文停用词：这些词出现在任何问题里，不能作为匹配依据
_STOP = set("的了吗呢吧啊我你他她它们是在有和就都而及与也很不没这那什么怎么"
            "如何吗呀哦嗯个们一二三四五六七八九十")
# ⛔⛔【已试过并回退，勿重开】2026-10-13 试过**扩充这个集合**，用来压掉
#   「从/时/候/算/始/开/几」这类泛字抬高的 Dice —— **失败，代价极大**：
#     · 精确率 73.9% → 79.2%（**涨了**）
#     · 但**盲测诚实命中 4% → 0%**、**误区库诚实覆盖 84% → 24%**（**崩了**）
#   ⇒ 根因：那些字**正是关键词的承重字符**。
#     例：关键词「读秒从几开始数」去掉 从/开/始/几 之后什么都不剩，
#         整条关键词失效 ⇒ 真问法再也命不中。
#   ⚠️ **教训与第 16 轮同源**：**别用"看起来更干净"的全局过滤去换局部问题。**
#     真正的问题在"短问句拿 4 个字去撞长关键词"，解法是**按问句长度分档**
#     （见下面的 _SHORT_Q / _SHORT_DICE）与**收窄具体关键词**，
#     不是把泛字从匹配面里全局删掉。

# 弱匹配阈值（Dice 系数）。实测分得很开，取空档中间：
#   真变体（必须命中）：「得分了还要停吗」0.71、「进球后要停下来吗」0.63
#   误配（必须拦住） ：「防守的人报数的间隔明显不到一秒…比赛要停下来吗」0.27
#
# ⚠️ 2026-10-12 由 0.45 提到 0.50：分母改成"单个关键词"之后，
#   0.45 与 0.50 的覆盖率几乎一样（103 vs 104 / 178），但 0.50 把
#   噪声误配从 1/16 压回 0/16。见 `_score` 第 3 级的完整实测表。
#
# ⛔⛔【下面这段"勿重开"已于 2026-10-13 **被推翻**，保留只为记录过程 ——
#      它当时的结论是错的，**不要照它做**。真正生效的说明在再下面那一大段。】
#   ~~【已试过并回退，勿重开】2026-10-12 试过把 0.50 提到 0.60，依据是盲测：~~
#   ~~误配(0.500~0.571) 与 真命中(0.600~0.769) 分得很开，看着 0.60 正好落在空档里。~~
#   ~~实测否决：盲测被抢 7→4（只省 3 个坏的），而误区库覆盖 84%→58%（丢 46 个好的）~~
#   ~~⇒ 代价远大于收益。~~
#   ⚠️ **为什么这个否决是错的**：它只量了"命中多少"，**没量"命中的对不对"**。
#      用覆盖率去判一个**准确性**改动 —— 用错了尺子，所以得出了反向结论。
#      2026-10-13 用两套独立评测集量精确率才发现：当时"被误杀的 46 条"里
#      **绝大多数本来就是假命中**。详见下一段。
#
#   （原结论里"正确修法是收窄具体关键词"这条**仍然成立**，只是不够 ——
#     假命中面太大，收窄个别词解决不了，必须同时抬阈值。）
# ⛔【第 16 轮的否决已作废 —— 2026-10-13 重新取证后推翻】
#
# 【当时为什么否决】把 0.50 抬到 0.60 后，误区库的"变体覆盖率"从 84% 掉到 58%
#   （丢 46 条）⇒ 判定"代价远大于收益"，写成 ⛔ 勿重开。
#
# 【为什么推翻】那个否决**用错了指标**：它只量了"命中多少"，
#   **完全没量"命中的对不对"**。部署后（2026-10-13）用两套**独立**评测集
#   （447 FAQ + 覆盖题集，共 1303 条问法）量**精确率**，真相是：
#
#     _WEAK_DICE   命中对   命错   精确率
#        0.50        121     251    32.5%   ← 当时线上就是这个
#        0.60         74      68    52.1%
#        0.65         58      30    65.9%   ← 现在取这个
#        0.70         38      16    70.4%
#
#   ⇒ **当时"被误杀的 46 条"里，绝大多数本来就是假命中。** 逐条核过：
#     抬到 0.65 丢掉 284 条命中 = **63 条真命中 + 221 条假命中**。
#
# 【为什么可以接受丢 63 条】判据是本项目一贯的"两边代价不对称"：
#   · **漏了无害** —— 掉回正常检索+生成层，而且规则真没写时它会拒答；
#   · **错了最坏** —— curated 绕过生成层，用户拿到的是**标着「人工核定」的答非所问**。
#   实测受害样例（当时线上真的这么答）：
#     「得分区从哪里开始算」（该讲场地 2.4）→ 答"从 1 数到 10……读秒"
#     「可以戴手表打球吗」（该讲装备 3.4）  → 答"第一次打比赛，不用背规则…"
#     「比赛打几分」（该讲赛制 4.2）        → 答"第一次打比赛，不用背规则…"
#   三条撞车全发生在**短问句 + 共享泛字**上（用户问句实词只有 5~9 个 ⇒ Dice 虚高）：
#     dice=0.625「得分区从哪里开始算」↔ kw「读秒从哪里开始」（共享 从/开/始 三个泛字）
#     dice=0.545「比赛打几分」        ↔ kw「刚开始打比赛」
#     dice=0.500「随便什么盘都能用吗」↔ kw「没写禁止就能随便做吗」
#
# ⚠️ **教训（比这个数值更重要）**：**"代价"必须用与目标一致的指标量。**
#   用"覆盖率"去判"准确性"的改动，等于用错的尺子量 —— 会得出反向结论。
#   ⇒ 以后凡是动匹配逻辑，**必须同时报"命中数"和"精确率"**。
#   （这条与 §7.8「报数字永远说清是哪把尺子」同源。）
#
# ⚠️ 想再往上抬（0.70 精确率 70.4%）要另一套证据：
#   先用 `tools/curated_precision.py` 看**漏掉了哪些真问题**，
#   确认那些问题在检索+生成层答得不差，才谈得上。
_WEAK_DICE = 0.65

# 弱匹配分数可以爬升的跨度。真实 Dice 值映射到 `[0.50, 0.50 + _WEAK_SPAN]`：
#   · 下界 0.50 = lookup 阈值 ⇒ 确保"够像的"仍然命中；
#   · 上界 0.699 < 0.80（第 2 级子串命中的起点）⇒ 保证**字面命中永远赢过模糊匹配**。
# ⚠️ 2026-10-12 为什么需要它：弱匹配原来一律返回常数 0.50，
#    导致任何两句过阈值的问法都**必然同分**，而 `lookup()` 用严格大于比较
#    ⇒ 同分时先出现者获胜 = **按文件顺序随机选**。
#    实测因此产生大量同分并列（见 `_score` 里那段注释的实例）。
_WEAK_SPAN = 0.199

# ⛔⛔【已试过并回退，勿重开】2026-10-13 试过**按问句长度给第 3 级分档**
#   （短问句实词数 <= 8 时，Dice 门槛从 0.65 抬到 0.80）。
#   动机是「接盘时候被撞算犯规吗」这类短问句拿 4 个字去撞长关键词、Dice 虚高。
#
#   **A/B 实测证明它是纯损失**（同一份 curated.jsonl，逐组合跑）：
#     原关键词 + 无分档   命中 88  对 65  错 23  精确率 73.9%
#     原关键词 + 有分档   命中 43  对 29  错 14  精确率 67.4%
#     收窄后   + 无分档   命中 83  对 65  错 18  精确率 78.3%  ← 最好
#     收窄后   + 有分档   命中 43  对 30  错 13  精确率 69.8%
#   ⇒ 分档把命中数**砍掉一半**、**答对数从 65 掉到 30**，连精确率都降了。
#
#   ⚠️ **为什么"精确率降"是关键判据**：直觉上"门槛更严 ⇒ 精确率该升"，
#      但实测相反 —— 因为**错的那 13 条同时也带走了 35 条对的**，
#      分母缩得比分子快，比例反而变差。**这说明"少答"不等于"答得更准"。**
#   ⚠️ **教训**：调匹配时**先看"答对了多少条（对）"，再看比例**。
#      只看精确率会为了 5 个百分点的比例损失 35 条正确答案。
#
#   ⇒ 真正有效的做法只有一个：**收窄具体关键词**（见
#     `tools/narrow_curated_keywords_2026_10_13.py`）。那一项单独贡献
#     错 23 → 18（精确率 73.9% → 78.3%），且**答对数不变（都是 65）**。
#_SHORT_Q = 8
#_SHORT_DICE = 0.80



def _content_chars(s: str) -> set[str]:
    """归一化后取**实词字符**集合（去掉标点与停用字）。

    只用于衡量"两个问法到底像不像"。刻意按**单字**而不是按词：
    本项目零依赖、没有分词器；中文单字配 Dice 系数已经足够把
    "真变体"和"只共享一个通用尾巴"的误配分开（见 _WEAK_DICE 的实测值）。
    """
    return {c for c in _norm(s) if c not in _STOP}


def _keywords_of(item: dict) -> list[str]:
    kws = list(item.get("keywords") or [])
    kws.append(item.get("question") or "")
    return [_norm(k) for k in kws if _norm(k)]


def _score(item: dict, qn: str, q_raw: str) -> float:
    """给一条裁定打分。0 = 不匹配。

    三级判据（从强到弱）：
      1. 问题文本几乎相同（含标点归一化后）→ 0.95
      2. 关键词出现在原问题里         → 0.80 + 命中比例
      3. 关键词与问题有 ≥2 字公共子串  → 0.50（弱，只在 1/2 都落空时用）

    ⚠️ `exclude`（2026-10-20）：这条裁定**明确不适用**于含某些词的问句。
       例：通用「读秒从 0 还是 1 开始」不适用于「错叫之后读秒从几开始」
       （后者是 15.9.3）。命中 exclude 直接 0 分，掉回正常检索流程。
       为什么不靠关键词收窄：弱匹配的 Dice 仍会把它拉回来（已实测）。
    """
    # 先看 exclude：它比任何正匹配都优先（"这条不适用"是硬边界）。
    for x in item.get("exclude") or []:
        x = _norm(x)
        if x and x in qn:
            return 0.0

    if _norm(item.get("question")) == qn:
        return 0.95

    kws = _keywords_of(item)
    if not kws:
        return 0.0

    best = 0.0
    for k in kws:
        if len(k) >= 2 and k in qn:
            best = max(best, 0.80 + min(0.15, len(k) / 40))

    if best > 0:
        return best

    # 弱匹配：**逐关键词**的实词重合度（Dice 系数），取最大值。
    #
    # ⚠️ 2026-10-05 换掉旧实现「数有几个关键词含 2 字子串命中，≥2 个就放行」。
    #   旧规则有个致命漏洞：一条裁定的关键词**本就是同一个问题的多个变体**，
    #   所以**一个通用尾巴就能同时点亮好几个关键词**，"≥2 个"的门槛形同虚设。
    #   实测受害案例：「防守的人报数的间隔明显不到一秒，我该叫什么？比赛要停下来吗？」
    #    （读秒过快题）被「得分后停止」裁定以 0.50 抢走 —— 而 curated 命中
    #    **不过生成层**，用户会**直接拿到一段答非所问的权威答案**，这是本项目
    #    最坏的一种失败（比拒答坏得多）。旧规则下它还稳稳通过。
    #
    # ⚠️⚠️ 2026-10-12 **第二次修正：分母从"全部关键词的并集"改成"单个关键词"**。
    #   原实现是 `ent = 所有 keywords + question 的实词并集`，
    #   于是 **每加一个关键词都在撑大分母** ⇒ 把别的变体从阈值上挤下去。
    #   实测证据（误区库 25 条 / 178 条真实变体，`tools/curated_stress.py --variants`）：
    #     · 并集版：覆盖率 **64/178 = 36%**；m015 加到 10 个词、m016 加到 11 个词，
    #       覆盖率仍是 3/7 —— **和只有 4 个词的邻居毫无差别**（加词白加）。
    #     · 逐词取最大：覆盖率 **103/178 = 58%**（+22pp），噪声误配 0/16 → 1/16。
    #   ⇒ 「多写关键词变体」这件事，只有改成逐词才有意义。
    #   阈值 0.45 → 0.50：58% 与 103/178 基本不变，但把噪声误配压回 0/16
    #   （实测扫描：0.45→1/16、0.50→1/16、0.60→0/16 但覆盖掉到 44%）。
    #   ⚠️ 仍然保持"宁可漏、不可错"的方向：漏了只是掉回正常检索流程（无害），
    #      错了是给出答非所问的权威答案（最坏）。
    qc = _content_chars(qn)
    if qc:
        # ⛔ 曾经在这里按问句长度给门槛分档（短问句 0.80 / 长问句 0.65）——
        #   2026-10-13 A/B 实测证明是**纯损失**，已撤（详见 _WEAK_SPAN 下方那段）。
        #   ⇒ 现在一律用 _WEAK_DICE。
        best_dice = 0.0
        for k in kws:
            kc = _content_chars(k)
            if not kc:
                continue
            dice = 2 * len(kc & qc) / (len(kc) + len(qc))
            if dice > best_dice:
                best_dice = dice
        if best_dice >= _WEAK_DICE:
            # ⚠️⚠️ 2026-10-12 **返回真实 Dice 值，不再返回常数 0.50**。
            #   原来返回常数导致：**任何两句过阈值的问法都必然同分**，
            #   而 `lookup()` 是 `if s > best_s`（严格大于）⇒ 同分时**先出现者获胜**
            #   ⇒ 谁赢取决于**文件顺序** = 静默随机。
            #   实测（误区库 25 条 / 178 条真实变体）因此产生 **大量同分并列**，
            #   例：「喊走步之后要站定吗」→ m004 与 m016 同分；
            #       「够盘的时候撞人了算谁的」→ m011/m018/m022 三条同分。
            #   ⇒ 返回真实值让"更像的那条"自然胜出。
            #   ⚠️ 分数压到 `[0.50, 0.699]`：仍**高于 lookup 阈值 0.50**（保证命中），
            #      且**低于第 1/2 级的 0.80**（保证"字面/子串命中"永远赢过"只有点像"）。
            frac = (best_dice - _WEAK_DICE) / max(1e-6, 1.0 - _WEAK_DICE)
            return round(0.50 + _WEAK_SPAN * min(1.0, max(0.0, frac)), 4)
    return 0.0


def lookup(query: str, threshold: float = 0.50) -> dict | None:
    """查疑问库。命中返回条目 + 得分，否则 None。

    默认阈值 0.50。**这个值是实测定的，不要随手调高**：
    疑问库的形态特点是「同一个问题有大量口语变体」，Owner 只可能写一种说法。
    实测「得分了还要停吗」「进球后要停下来吗」与库里问法字面不同，
    但靠关键词（得分+停）能认出来 —— 这类同族问法必须能命中。
    同时验证过 6 条完全无关的题（接盘被撞/场地多大/能撞人吗/有伤口/读秒）
    在 0.50 下一条都不命中，说明弱匹配没造成误伤。
    低于 0.50 才开始放行更松的字符重叠，误伤风险陡增。
    """
    q = (query or "").strip()
    if not q:
        return None
    qn = _norm(q)
    # ⚠️⚠️ 2026-10-12 **fail-closed 同分保护**：
    #   原来是 `if s > best_s`（严格大于）⇒ **同分时先出现者获胜**，
    #   等于"按文件顺序随机选一条"。而疑问库命中**绕过检索+生成层**，
    #   用户拿到的是**答非所问的权威答案** —— 本项目最坏的一种失败。
    #   ⇒ 改成：收集**所有**并列最高分的条目；
    #      · 只有一条 → 正常返回；
    #      · 多于一条 → **返回 None**（掉回正常检索流程）。
    #   判据依据"两边代价不对称"（项目历来口径）：
    #     · 不命中 = 走正常检索+生成层，**无害**；
    #     · 命中错的 = 给出答非所问的**权威**答案，**最坏**。
    #   ⚠️ 注意：`_score` 的弱匹配现在返回**真实 Dice 映射值**（见 _WEAK_SPAN），
    #      所以"真同分"已大幅减少；这里只是最后一道兜底。
    best_s = 0.0
    winners: list[dict] = []
    for item in _load():
        s = _score(item, qn, q)
        if s > best_s:
            best_s, winners = s, [item]
        elif s == best_s and s > 0:
            winners.append(item)
    if best_s < threshold or not winners:
        return None
    if len(winners) > 1:
        return None
    out = dict(winners[0])
    out["_match"] = round(best_s, 3)
    return out


def clause_ids(item: dict) -> list[str]:
    """裁定里点名的条款号；若与语义等价组打通，一并返回。"""
    ids = list(item.get("clauses") or [])
    extra: list[str] = []
    for c in list(ids):
        for grp in _EQUIV:
            if c in grp:
                extra += [g for g in grp if g not in ids]
    return ids + extra


def stats() -> dict:
    rows = _load()
    by_source: dict[str, int] = {}
    for r in rows:
        s = r.get("source", SOURCE_OWNER)
        by_source[s] = by_source.get(s, 0) + 1
    return {
        "total": len(rows),
        "by_source": by_source,
    }
