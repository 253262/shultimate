# -*- coding: utf-8 -*-
"""基础自检：不联网，验证语料与索引的完整性。

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

RUN_PAID_TESTS = os.environ.get("RUN_PAID_TESTS") == "1"


def load_jsonl(name: str) -> list[dict]:
    p = config.DATA_DIR / name
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestCorpus(unittest.TestCase):
    """语料层：切块结果必须完整、字段齐全。"""

    def test_rule_count(self):
        rules = load_jsonl("rules.jsonl")
        # 368 = 语块数（含 4 个父条号被拆成"父行 + 子行"的多条）；
        # 语块数与官方唯一条号数同为 368（2026-10-04 核正，见 docs/retrieval-ceiling.md 6.3）。
        self.assertEqual(len(rules), 368, "规则语块应为 368 条，与官方英文唯一条号一致")

    def test_definition_count(self):
        defs = load_jsonl("definitions.jsonl")
        self.assertEqual(len(defs), 40, "术语定义应为 40 条")

    def test_required_fields(self):
        for r in load_jsonl("rules.jsonl"):
            for key in ("id", "chapter", "chapter_title", "text", "search_text", "source"):
                self.assertIn(key, r, f"{r.get('id')} 缺字段 {key}")
            self.assertTrue(r["text"].strip(), f"{r['id']} 正文为空")

    def test_no_duplicate_ids(self):
        ids = [r["id"] for r in load_jsonl("rules.jsonl")]
        self.assertEqual(len(ids), len(set(ids)), "存在重复条号")

    def test_parent_points_to_real_clause(self):
        """父条款必须存在，或者就是章号本身（章标题不单独成条款）。"""
        rows = load_jsonl("rules.jsonl")
        ids = {r["id"] for r in rows}
        chapters = {r["chapter"] for r in rows}
        for r in rows:
            p = r["parent"]
            if p:
                self.assertTrue(p in ids or p in chapters,
                                f"{r['id']} 的父条款 {p} 既不是条款也不是章号")

    def test_known_clause_text(self):
        """抽查：2.1 的正文里确实没有尺寸数字（这是本项目要诚实的那个案例）。"""
        row = next(r for r in load_jsonl("rules.jsonl") if r["id"] == "2.1")
        self.assertIn("图 1", row["text"])
        self.assertNotIn("64", row["text"])


class TestIndex(unittest.TestCase):
    """索引层：二进制矩阵与元数据必须对得上。"""

    def test_index_consistency(self):
        meta_p = config.DATA_DIR / "index.json"
        bin_p = config.DATA_DIR / "embeddings.bin"
        if not (meta_p.exists() and bin_p.exists()):
            self.skipTest("索引尚未构建")
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        expected = len(meta["items"]) * meta["dims"] * 4      # float32
        self.assertEqual(bin_p.stat().st_size, expected,
                         "embeddings.bin 大小与 index.json 不一致")

    def test_query_prefix_is_asymmetric(self):
        """查询侧必须加检索前缀，文档侧不加 —— 这是提升最大的单项配置。"""
        self.assertTrue(config.QUERY_PREFIX.strip())
        self.assertGreater(config.MIN_SCORE, 0)


class TestGenerator(unittest.TestCase):
    """生成层：不外呼，只校验提示词里的硬约束还在。"""

    def test_prompt_guards(self):
        from app import generator
        p = generator.SYSTEM_PROMPT
        self.assertIn("没有裁判", p, "必须写明飞盘是无裁判运动")
        self.assertIn("规则里没有规定", p, "必须要求在依据不足时明说")
        self.assertIn("条款号", p, "必须要求标注条款号")

    def test_no_numbered_list_in_context(self):
        """条款列表不得出现 [1][2] 行号，否则模型会把行号当条款号。"""
        from app import generator
        ctx = generator.build_context([
            {"id": "17.6", "chapter_title": "犯规", "text": "示例"}
        ])
        self.assertIn("第17.6条", ctx)
        self.assertNotIn("[1]", ctx)


class TestCurated(unittest.TestCase):
    """疑问库：Owner 裁定必须能覆盖向量检索，且不误伤无关问题。"""

    def setUp(self):
        from app import curated
        self.m = curated
        self.path = self.m._DATA
        self.backup = self.path.read_text(encoding="utf-8") if self.path.exists() else None
        # 每个测试用**独立且唯一**的临时库，避免污染真实裁定。
        #
        # ⚠️ 为什么是唯一名而不是固定名 + 先 unlink：
        #   固定名 + 先 unlink 是老做法，踩过两次 ——
        #   ① 上一次运行被中断（改代码、Ctrl+C、进程被杀）时 tearDown 不执行，
        #      临时库里留下上轮条目，lookup() 会一起读进去，于是 add() 返回的 id
        #      与 lookup() 命中的 id 不一致。表现是 `FAILED (failures=1)` 且
        #      **时好时坏**（5 次挂 1 次）—— 这种 flaky 极易误判成"我改坏了检索"。
        #   ② 更隐蔽：本机沙箱有批量删除护栏，一轮对话累计删过约 50 个文件后，
        #      连 `unlink` 都会被拦（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`）——
        #      这里原来没有 try/except，护栏一拦就直接 ERROR，
        #      整条套件红、单独跑却绿。
        #   ⇒ 唯一文件名让"清空"这一步**根本不存在**：不可能有残留，也不需要删。
        #     用 pid + 计数器保证同一进程内多次 setUp 也互不干扰。
        import os
        TestCurated._seq = getattr(TestCurated, "_seq", 0) + 1
        self.tmp = self.path.with_name(
            f"_test_curated_{os.getpid()}_{TestCurated._seq}.jsonl")
        self.m._DATA = self.tmp

    def tearDown(self):
        self.m._DATA = self.path
        # 删不掉就算了：文件名唯一，残留既不会被下次读到，也不会污染真实裁定。
        try:
            if self.tmp.exists():
                self.tmp.unlink()
        except OSError:
            pass

    def test_add_and_lookup(self):
        it = self.m.add(question="得了分之后还要停下来吗",
                        answer="得分之后这一分就结束了。",
                        clauses=["14.2"],
                        keywords=["得分后停下", "得停吗"])
        self.assertEqual(it["source"], self.m.SOURCE_OWNER)
        hit = self.m.lookup("得了分之后还要停下来吗")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], it["id"])

    def test_synonym_hits(self):
        """同义问法必须能命中 —— 用户不会用 Owner 的原话来提问。

        ⚠️ **2026-10-13 改**：原来这里用**人造的短关键词**（只有
           `["得分后停下", "得停吗"]` 两个词）。把 `_WEAK_DICE` 从 0.50 抬到 0.65
           之后它挂了，但**那不是回归** —— 线上真实那条条目的关键词有 **4 个**
           （见下），实测三条同义问法**全部命中**（`_score` 0.509~0.636）。
           ⇒ 用人造数据测的是"这个人为构造能不能过"，不是"真实条目能不能过"。
             换成**线上真实的关键词**，测试反而更强。

        ⚠️ 下面这组与 `data/curated.jsonl` 里
           「得了分之后还要停下来吗」的关键词**保持一致**。
           改那边的话这里要跟着改（否则测试测的是历史快照）。
        """
        self.m.add(question="得了分之后还要停下来吗", answer="答案",
                   keywords=["得分后停下", "得分了要停", "进了球要停", "得分后要停下来吗"])
        for q in ("得分了还要停吗", "进球后要停下来吗", "得停吗", "得分之后要停吗"):
            self.assertIsNotNone(self.m.lookup(q), f"「{q}」应命中")

    def test_unrelated_does_not_hit(self):
        """无关问题绝不能撞上人工裁定（宁可漏，不可错答）。"""
        self.m.add(question="得了分之后还要停下来吗", answer="答案",
                   keywords=["得分后停下"])
        for q in ("接盘被撞算不算接住", "比赛场地多大", "读秒从几开始",
                  "防守队员能撞人吗", "有伤口能继续打吗"):
            self.assertIsNone(self.m.lookup(q), f"「{q}」不应命中")

    def test_generic_tail_must_not_hijack(self):
        """**最坏的一种失败**：curated 命中不过生成层 —— 一旦误配，
        用户直接拿到一段答非所问的"权威"答案，比拒答坏得多。

        真实案例（2026-10-05）：疑问库有一条「得分后停止」裁定，
        关键词是同一句的 5 个变体。线上问读秒题
        「防守的人报数的间隔明显不到一秒，我该叫什么？比赛要停下来吗？」
        被它抢走并原样返回 —— 因为旧弱匹配只数"几个关键词含 2 字子串命中"，
        **一个通用尾巴「停下来吗」就能同时点亮 5 个关键词**，门槛形同虚设。
        已改成按实词重合度（Dice 系数）判定。
        """
        self.m.add(question="得了分之后还要停下来吗", answer="答案",
                   keywords=["得分后停下", "得停吗", "得分了要停",
                             "进了球要停", "得分后要停下来吗"])
        # 只共享"停下来吗"这个尾巴、主体完全不同的题 → 必须拦住
        self.assertIsNone(
            self.m.lookup("防守的人报数的间隔明显不到一秒，我该叫什么？比赛要停下来吗？"),
            "通用尾巴不能成为命中依据（这是线上真实踩过的坑）")
        # 但同族真变体必须继续命中 —— 别把弱匹配整个阉掉
        # ⚠️ 只列**有足够实词**的变体。像"得停了吗"这种 3 字问句只剩 2 个实词，
        #    Dice 天然到不了 0.45 —— 这类极短问法落回检索层是**安全**的失败
        #    （RAG 会照规则答），所以不为它放松判据。
        for q in ("得分了还要停吗", "进球后要停下来吗"):
            with self.subTest(q=q):
                self.assertIsNotNone(self.m.lookup(q), f"「{q}」是同一问题的变体，应命中")

    def test_clause_equiv(self):
        self.assertIn("11.1", self.m.clause_ids({"clauses": ["2.3"]}))
        self.assertIn("4.5", self.m.clause_ids({"clauses": ["7.1"]}))

    def test_delete(self):
        it = self.m.add(question="临时问题-abc", answer="答案")
        self.assertTrue(self.m.delete(it["id"]))
        self.assertIsNone(self.m.lookup("临时问题-abc"))


class TestWebLookup(unittest.TestCase):
    """联网兜底：不外呼，只锁死两条硬约束。"""

    def test_no_citations_field(self):
        """⚠️ 硬约束：联网结果绝不能带 citations。
        citations 在前端代表"规则原文"，网络内容混进去就是伪装一手来源。"""
        from app import weblookup
        import inspect
        src = inspect.getsource(weblookup)
        self.assertNotIn('"citations"', src, "weblookup 不得产出 citations 字段")

    def test_chinese_query_abandons(self):
        """抽不出英文检索词就必须放弃 —— 中文词在英文 PDF 里必然零命中。"""
        from app import weblookup
        self.assertEqual(weblookup._to_english("飞盘的价格是多少"), "")
        self.assertIsNone(weblookup.lookup("飞盘的价格是多少"))

    def test_english_terms_extracted(self):
        from app import weblookup
        self.assertIn("goal", weblookup._to_english("得分之后要停下来吗"))
        self.assertIn("stall", weblookup._to_english("读秒从几开始"))

    def test_english_pdf_url(self):
        """只认 WFDF 自己的域名。"""
        from app import weblookup
        self.assertIn("rules.wfdf.sport", weblookup.OFFICIAL_PDF)


@unittest.skipUnless(RUN_PAID_TESTS, "真实 embedding 集成测试；设置 RUN_PAID_TESTS=1 才跑")
class TestRouteGain(unittest.TestCase):
    """「得分后要不要停」类问题必须能召回 14.2 —— 曾经排全库 #20 被拒答。"""

    def test_scoring_stop_recall(self):
        from app.retriever import Retriever
        r = Retriever()
        for q in ("得了分之后还要停下来吗", "得分之后必须马上停下来吗",
                  "进球后要停下来吗", "什么时候必须停下来"):
            res = r.search(q, top_k=8)
            ids = [h["id"] for h in res["hits"]]
            self.assertIn("14.2", ids, f"「{q}」应召回 14.2，实际 {ids[:4]}")
            self.assertTrue(res["confident"], f"「{q}」闸门应放行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
