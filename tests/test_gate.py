# -*- coding: utf-8 -*-
"""守卫：相关性闸门必须「域内放行、真离题拦截」。

**背景（2026-10-05，第一次量到"答对率"才发现的）**
闸门原来的判据是「有没有命中规则类目（族）」→ 命中 0 个族就拦截。
但族是为**需要判断的争议场景**建的（犯规/违例/争抢/读秒…），于是
**368 条规则里有 190 条（52%）不属于任何族** —— 场地(第2章)、装备(第3章)、
队伍(第5章)、比赛开始(第6章) 这些事实性章节整章无族。

实测后果（`tools/answer_eval.py` 第一次跑就撞上）：
    · 「边线和端线是什么」→ 正解 **2.2 排第 1**，闸门拦截 ❌
    · 「什么衣服不能穿」  → 正解 **3.4 排第 1**，闸门拦截 ❌
检索完全正确、答案就在手里，却对用户说"规则里没有"。
154 个评测主题里 **24 个（16%）的答案键全部无族**，都处在这个风险里。

**为什么必须钉住，而且两边都要钉**
只钉"域内放行"会让人误以为可以把词表砍到最小；
只钉"离题拦截"会让人不敢加词。两条**同时**钉住，
才能表达真实设计意图：**判据是"含不含飞盘域内词"，不是"是不是个好问题"。**
    · 误拦：用户问真规则问题 → 我们答"没有规定" → **实质答错**
    · 漏放：白调一次大模型 → 模型按铁律说"规则里没有" → **无害**
代价不对称 ⇒ 宁可偏松。所以本文件对离题题的断言是
「**不含任何飞盘域内词的**必须拦」，而不是"上面这 17 条全都必须拦"。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import routes                       # noqa: E402
from app.retriever import Retriever          # noqa: E402

# 2026-10-24 DS-SEC-QA-02 R5：默认真实离线；真实 embedding 集成测试显式开启。
RUN_PAID_TESTS = os.environ.get("RUN_PAID_TESTS") == "1"

# 域内：真实球员会问的规则问题。**每一条都必须放行** ——
# 这些都是"规则里有答案"的问题，拦住就是答错。
IN_DOMAIN = [
    # 曾实测被误拦的两条（本次修复的直接触发点）
    "边线和端线是什么",
    "什么衣服不能穿",
    # 事实性章节：无族，全靠域内词表兜
    "场地多大", "一场比赛打几分", "几个人上场", "换人怎么换",
    "队服必须统一吗", "鞋有什么要求", "半场休息多久", "得分区多大",
    # 争议章节：本来就靠族，不能被这次改动弄坏
    "上下半场怎么换", "开盘出界了怎么办", "读秒数到几", "暂停能叫几次",
    "受伤了怎么办", "死盘活盘区别", "盘掉了算谁的", "砖点在哪里",
    "比赛打到几分结束", "下雨了停不停", "轴心点怎么站",
    "开盘能碰盘吗", "被人撞了算犯规吗", "盘出界了在哪发",
    # 补第一批词表后跑 answer_eval 仍被拦的 11 条 —— 词表漏了「盘」这个核心字
    "防守方要站在线后吗", "盘在滚能去拦吗", "防守方能挡着不让我捡盘吗",
    "接住之后又掉出来了算接住吗", "能踩着队友跳起来吗",
    "把盘打到别人身上再接回来", "拿到盘又故意扔掉", "两个人同时防我",
    "什么是 travel", "伤停的代价是什么", "双方谈不拢怎么处理",
    # 2026-10-07 补「总纲型」问法（owner 用「敢下场」准绳审出来的第三类闸门缺陷）：
    # 这类问题的材料**是齐的**（1.8 / 18.2.4.1 / 12.9 都在 top8），
    # 但它们不指向任何**具体情形** ⇒ 命中不了任何族、也不在原词表里
    # ⇒ 必被闸门拦成"规则里没有"。实测 10 句真实问法里 5 句中招。
    "新手第一次打比赛最怕犯什么错",
    "打球新手要注意什么",
    "比赛前要准备什么",
    "有什么规则是大家常忽略的",
    "比赛时不能做什么",
    "上场前我该知道哪些规则",
    "最容易被吹的犯规是什么",
    "老队员带新人要注意什么",
    "飞盘新手怎么入门",
]

# 总纲型问法（2026-10-07）：材料齐但族不认领 ⇒ 靠域内词表放行。
OVERVIEW_QUESTIONS = [
    "新手第一次打比赛最怕犯什么错",
    "打球新手要注意什么",
    "比赛时不能做什么",
    "有什么规则是大家常忽略的",
]

# 真离题：**一个飞盘域内词都不含**。这些必须拦住。
OFF_DOMAIN_MUST_BLOCK = [
    "怎么学 Python", "推荐个餐厅", "我该怎么找女朋友", "股票今天涨了吗",
    "如何写一份简历", "帮我算一下 3+5", "明天几点开会", "推荐一部电影",
    "怎么减肥", "这款手机多少钱", "帮我写封邮件", "给我讲个笑话",
    "帮我订张机票", "怎么练才能扔得远",
]

# 含域内名词、但问题本身笼统 —— **允许放行**，交给生成层按依据充分性兜。
# 写在这里是为了把"允许"显式化：以后有人想收紧到"必须拦"，
# 得先想清楚误拦的代价。
OFF_DOMAIN_ALLOWED = [
    "今天天气怎么样", "北京到上海多远", "这双鞋在哪儿买",
    # ⚠️ 这几条含「飞盘」二字，按"偏松"原则设计上**就该放行**：
    #    它们不是错答，是"这不在我职责范围"的诚实回复。
    #    代价（白调一次模型）远小于为了拦它们而把词表收紧、
    #    又把「盘在滚能去拦吗」这类真题拦掉的代价。
    "飞盘运动是哪一年发明的", "飞盘多少钱一个", "推荐个飞盘牌子",
]


@unittest.skipUnless(RUN_PAID_TESTS, "真实 embedding 集成测试；设置 RUN_PAID_TESTS=1 才跑")
class TestGateDomainLexicon(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.r = Retriever()

    def test_domain_words_not_empty_and_no_dupes(self):
        """词表本身要干净：非空、无重复（重复说明有人在两处各加了一遍）。"""
        words = routes.DOMAIN_WORDS
        self.assertGreater(len(words), 100, "词表被砍得太狠了，闸门等于失效")
        dupes = [w for w in set(words) if words.count(w) > 1]
        self.assertEqual([], dupes, f"域内词表有重复项：{dupes}")

    def test_domain_hit_basic(self):
        # 词表按长度倒序索引，同长度的相对顺序不确定 —— 只断言"命中了其中之一"
        self.assertIn(routes.domain_hit("边线和端线是什么"), ("边线", "端线"))
        self.assertIsNone(routes.domain_hit("怎么学 Python"))
        self.assertIsNone(routes.domain_hit(""))

    def test_in_domain_questions_must_pass_gate(self):
        """⭐ 域内问题必须放行 —— 这条是本文件的核心。

        失败 = 我们有答案却对用户说"规则里没有"，是最严重的一类错。
        """
        for q in IN_DOMAIN:
            with self.subTest(q=q):
                res = self.r.search(q, top_k=8)
                self.assertTrue(
                    res["confident"],
                    f"「{q}」含飞盘域内词却被闸门拦了 —— 这等于答非所问。"
                    f"命中族={res.get('routes')} 域内词={res.get('domain_word')}")

    def test_overview_style_questions_pass_gate(self):
        """⭐ 第三类闸门缺陷的专项守卫（2026-10-07）。

        「新手最怕犯什么错」这类**总纲型**问法：
        · 材料是齐的 —— 1.8（老队员该帮新手）、18.2.4.1（走步违例）、
          12.9（不得张臂挡人）检索**全部在 top8**
        · 但它们不指向任何**具体情形** ⇒ 命中不了任何族

        ⇒ 原先 confident=False ⇒ mode=not_found ⇒ 对新手说
          「规则里没有和这个问题直接相关的规定」+ 甩五条走步违例过来。
        **这是最严重的一类错：有答案，却让用户更不敢下场。**

        为什么不用族而用域内词（否决理由写在 routes.py 的注释里）：
        族会给族内所有条款加分，而"新手要注意什么"该召回的是横跨
        第 1/3/12/17/18 章的一大片 ⇒ 任何族都装不下，反而挤掉正解。
        域内词表不参与打分（红线 #9），放行是零副作用的。
        """
        for q in OVERVIEW_QUESTIONS:
            with self.subTest(q=q):
                res = self.r.search(q, top_k=8)
                self.assertTrue(
                    res["confident"],
                    f"「{q}」是总纲型问题却被闸门拦了。"
                    f"命中族={res.get('routes')} 域内词={res.get('domain_word')}")

    def test_overview_words_dont_leak_into_off_domain(self):
        """反向：补进来的总纲词**不许**把真离题问题带进闸门。

        这批词全是「动作/状态词」、不含任何飞盘实体名词 ——
        所以「帮我写段代码」「我想订机票」这类必须继续被拦。
        """
        for q in ("帮我写段python代码", "我想订机票", "明天几号考试",
                  "帮我起个网名", "这段代码什么意思"):
            with self.subTest(q=q):
                res = self.r.search(q, top_k=8)
                self.assertFalse(
                    res["confident"],
                    f"「{q}」是离题问题却被放行了（域内词={res.get('domain_word')}）")

    def test_off_domain_rejection_must_not_show_irrelevant_clauses(self):
        """⭐ 完全离题时**不许**再摆一堆不相干条款（2026-10-07）。

        闸门拦截其实是两种完全不同的情况：
          · 域内但没有对应条款 ⇒ 列"你是不是想问这些"**是在帮他**
          · 完全离题 ⇒ 列条款**是错的** —— 他压根不在问飞盘，
            我们却搬一堆出界/飞盘精神条款出来，还问他
            「你是不是想问这些？」⇒ 明示用户这些条款与他的问题无关，
            比老实说"这个我答不了"更糟。
        """
        import app.generator as gen
        for q in OFF_DOMAIN_MUST_BLOCK:
            with self.subTest(q=q):
                res = self.r.search(q, top_k=8)
                self.assertFalse(res["confident"], f"前提不成立：{q} 竟被放行了")
                txt = gen.not_found_text(res["nearby"], bool(res.get("domain_word")))
                self.assertNotIn("你是不是想问这些", txt,
                                 f"「{q}」是离题问题，却还在列候选条款")
                self.assertNotIn("如果都不是", txt,
                                 f"「{q}」是离题问题，却还在引导他改问法")

    def test_in_domain_rejection_still_offers_candidates(self):
        """反向：域内但没条款时，**保留**"你是不是想问这些"的引导
        —— 那是这个模板原本就有的有用行为，别一起删掉。"""
        import app.generator as gen
        # 直接构造，不依赖题集当前状态（否则词表一改就假失败）。
        # 域内信号给 True ⇒ 必须保留原有的候选引导。
        txt = gen.not_found_text([{"id": "1.3", "chapter_title": "飞盘精神",
                                    "text": "在双方发生争议时，你就是在当裁判"}],
                                 True)
        self.assertIn("你是不是想问这些", txt,
                      "域内问题的拒答必须保留候选引导 —— 那是这个模板"
                      "原本就有的有用行为，别一起删掉")
        self.assertIn("规则里没有", txt, "域内拒答不该丢掉这个说法")

    def test_off_domain_without_domain_word_must_block(self):
        """⭐ 真离题必须拦 —— 这条防的是"有人把词表改成永远放行"。"""
        for q in OFF_DOMAIN_MUST_BLOCK:
            with self.subTest(q=q):
                res = self.r.search(q, top_k=8)
                self.assertFalse(
                    res["confident"],
                    f"「{q}」不含任何飞盘域内词，应被闸门拦住；"
                    f"实际放行（域内词={res.get('domain_word')}）")

    def test_off_domain_with_domain_word_is_allowed_through(self):
        """含域内名词的笼统问题**允许**放行 —— 显式记录这个有意的选择。

        不是漏网：生成层拿到的是低相关候选，铁律下会说"规则里没有相关规定"。
        拦它的代价（替用户判断"这不是个好问题"）大于放行。
        ⚠️ 如果你想把这三条收紧成"必须拦"，**请先改这段测试** ——
           它是故意写死的刹车，逼你先想清楚误拦的代价（见模块注释）。
        """
        for q in OFF_DOMAIN_ALLOWED:
            with self.subTest(q=q):
                self.assertIsNotNone(
                    routes.domain_hit(q),
                    f"「{q}」里确实含飞盘域内名词，当前设计选择放行")
                self.assertTrue(self.r.search(q, top_k=8)["confident"])

    def test_domain_words_must_not_enter_bonus_tables(self):
        """⭐⭐ 域内词表**绝不能进 forced_split** —— 它只该管闸门。

        一旦接进去，就等于凭空多出 20 个巨型兜底族：每章的总则条款都会被
        抬进候选前列，必然破坏已经调好的排序（`ROUTE_BONUS` 是承重参数，
        族内任一条能盖过族外全部，实测 cos=0.622 压过 cos=0.934）。

        这四条是**无族但有域内词**的问题 —— 正好是本次修复的直接目标
        （修复前它们命中 0 个族 → 被闸门拦，虽然正解排第 1）。
        """
        avail = set(self.r._id2idx)
        for q in ("边线和端线是什么", "什么衣服不能穿",
                  "鞋有什么要求", "一场比赛打几分"):
            with self.subTest(q=q):
                self.assertIsNotNone(routes.domain_hit(q), f"「{q}」应有域内词")
                pri, aux, rt = routes.forced_split(q, avail)
                self.assertEqual([], rt, f"「{q}」不该命中任何族：{rt}")
                self.assertEqual({}, pri, f"「{q}」域内词泄漏进了主档：{pri}")
                self.assertEqual({}, aux, f"「{q}」域内词泄漏进了辅档：{aux}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
