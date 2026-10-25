# -*- coding: utf-8 -*-
"""「术语白话解释」的守卫 —— 目的仍然是**让所有人都能懂飞盘规则**。

来历：2026-10-10 第二轮审查。上一轮修的是「**答不出来**」，
本轮修的是「**答了也读不懂**」。

实测基线（改之前，打线上真实接口）：
    问：我是新手第一次打比赛，什么算得分
    答：…接盘时你所有**触地点**都在**进攻得分区**内…**确立盘权**那一刻…

一个刚打第一次的新手看到 `触地点` / `确立盘权` / `进攻得分区`，
和看到英文原文没有本质区别。而 `data/definitions.jsonl` 里
**本来就躺着 40 条官方名词定义**，白放着没用。

⚠️⚠️ 本模块最高的一条纪律 —— **零编造**：
    术语解释**一个字都不许模型生成**，纯字符串匹配 + 从官方名词定义整句取原文。
    这条一旦破，就是"为了让人看懂而编造定义"，比看不懂严重得多。

⚠️ 第二条纪律 —— **宁可漏，不可贴反**：
    派生词表（`_DERIVED`）只许收与原词**严格同义**的形态。
    上线前自查就拦下一个真实事故：曾写 `"非轻微接触": "轻微接触"`，
    而官方定义里"非轻微接触"的出现方式恰恰是**反义**
    （原文「碰到…手臂/手…不算轻微接触」）—— 那是对着新手说反话。
    本文件用一条守卫把这个反面教材钉死，防止它被"优化"回去。

照抄本目录已踩过的坑（见 test_audit_2026_10_09.py 模块 docstring）：
剥注释再断言代码、钉使用点、断言窗口掐到逻辑块。
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import generator as generator_mod  # noqa: E402
from app import glossary  # noqa: E402
from app.generator import Generator  # noqa: E402

DEF_FILE = ROOT / "data" / "definitions.jsonl"
GENERATOR_PY = (ROOT / "app" / "generator.py").read_text(encoding="utf-8")
SERVER_PY = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


def strip_js_comments(src: str) -> str:
    """去掉 JS 行注释与块注释，再断言。

    ⚠️ 这不是洁癖，是红队逼出来的（2026-10-10）：
    守卫 `test_glossary_labels_its_source` 原来写的是
    `assertIn("官方名词定义", APP_JS)` —— 而这三个字**注释里就有两处**
    （"这块是官方名词定义"、"必须是 data/definitions.jsonl 里的官方名词定义"）。
    于是真把渲染里那行删掉，红队照样绿 —— 这就是本目录反复记录的假绿。
    同理，正则也只在剥注释后的代码上跑。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def strip_py_comments(src: str) -> str:
    src = re.sub(r'(?s)"""(?:.|\n)*?"""', "", src)
    return re.sub(r"(?m)#.*$", "", src)


APP_JS_CODE = strip_js_comments(APP_JS)
GENERATOR_CODE = strip_py_comments(GENERATOR_PY)
SERVER_CODE = strip_py_comments(SERVER_PY)


def defs() -> list[dict]:
    return [json.loads(l) for l in DEF_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]


class TestGlossaryZeroFabrication(unittest.TestCase):
    """零编造：解释的每个字都必须来自官方名词定义原文。"""

    def test_every_explanation_is_verbatim_from_definitions(self):
        """术语解释里出现的解释文本，必须逐字等于 definitions.jsonl 的 text。"""
        sample = ("你在对方得分区接稳盘就算得分，触地点都在区内，"
                  "确立盘权那一刻算分。防守队员之间发生非轻微接触。")
        for rec in glossary.terms_in(sample):
            official = {d["text"] for d in defs()}
            self.assertIn(rec["text"], official,
                          f"「{rec['term']}」的解释不是官方名词定义原文：{rec['text'][:60]}")

    def test_glossary_module_never_calls_llm(self):
        """本模块是纯字符串处理，绝不允许出现任何大模型调用。"""
        src = (ROOT / "app" / "glossary.py").read_text(encoding="utf-8")
        for bad in ("_chat", "urllib", "openai", "deepseek", "http"):
            self.assertNotIn(bad, src, f"glossary.py 不该出现 {bad} —— 术语解释必须零编造")

    def test_block_does_not_enter_answer_text(self):
        """术语解释不许拼进 answer 正文（会让人分不清哪句是模型说的）。

        ⚠️⚠️ 必须**运行时**断言，静态正则拦不住（红队 2 逼出来的）：
        回退写法是 `text = text.strip() + glossary_mod.block(text.strip())`，
        再原样 `"answer": text` —— 源码里 `"answer":` 右边根本没出现 glossary 字样，
        所有静态断言一律绿。**只有把 block 换成能自证的假块跑一遍才抓得到。**
        """
        body = GENERATOR_CODE.split("def answer(")[-1]
        self.assertIn('"glossary": g', body)
        self.assertNotRegex(body, r'"answer":\s*[^\n]*glossary',
                            "answer 正文里混进了 glossary")

        # ---- 运行时防线：把 block 换成会自证的假块 ----
        real_block = glossary.block
        try:
            glossary.block = lambda text: "<<<GLOSSARY_BLOCK_INJECTED>>>"
            g = Generator.__new__(Generator)
            g.resolved = [("http://x", "k", "m")]
            g.last_provider = None
            g._glossary_probe = None
            # 让第一次请求返回一段含术语的回答
            calls = {"n": 0}

            def fake_chat(url, payload, headers):
                calls["n"] += 1
                return {"choices": [{"message": {"content":
                              "你在对方得分区里接稳盘就算得分。"}}],
                        "usage": {}}

            old_chat = generator_mod._chat
            generator_mod._chat = fake_chat
            try:
                out = Generator.answer(
                    g, "什么算得分",
                    [{"id": "14.1", "text": "接住盘", "chapter_title": "得分",
                      "cos": 0.9}])
            finally:
                generator_mod._chat = old_chat
        finally:
            glossary.block = real_block

        self.assertIn("GLOSSARY_BLOCK_INJECTED", out["glossary"],
                      "假块没进 glossary 字段 —— 说明 glossary 字段的取值路径变了，守卫要重写")
        self.assertNotIn("GLOSSARY_BLOCK_INJECTED", out["answer"],
                        "⚠️ 术语解释被拼进了 answer 正文 —— 用户会分不清哪句是模型说的")


class TestGlossaryNoReversal(unittest.TestCase):
    """宁可漏，不可贴反 —— 上一条纪律的具体防线。"""

    def test_derived_terms_are_strictly_synonymous(self):
        """每个派生词必须在规则原文里与原词**同义**地出现。

        判据：原词的官方定义文本里若出现该派生词，且上下文出现否定标记
        （"不"、"非"、"除…外"），说明两者是反义，绝不能建立映射。
        """
        idx = glossary._load()
        for deriv, base in glossary._DERIVED.items():
            self.assertIn(base, idx, f"派生词 {deriv} 指向的原词 {base} 不在术语表里")
            base_text = idx[base]["text"]
            if deriv in base_text:
                # 定义文本里出现了派生词 → 必须人工确认同义，否则删表
                self.assertNotRegex(
                    base_text, r"不" + deriv,
                    f"「{deriv}→{base}」疑似反义映射：官方定义里出现「不{deriv}」")

    def test_reversed_term_never_in_derived(self):
        """"非轻微接触"绝不能被映射到"轻微接触"（上线前自查拦下的真实事故）。"""
        self.assertNotIn("非轻微接触", glossary._DERIVED)
        # 且必须真的不产出解释：宁可漏解释，也不许说反话
        out = glossary.block("防守队员之间发生了非轻微接触。")
        self.assertNotIn("非轻微接触", out)

    def test_negated_context_blocks_explanation(self):
        """答案里若明确说某词"不算/不是X"，就不能把 X 的定义贴上去。"""
        ans = "碰到对手的手臂不算非轻微接触，这种接触叫轻微接触。"
        terms = {t["term"] for t in glossary.terms_in(ans)}
        self.assertNotIn("非轻微接触", terms)


class TestGlossaryPrecision(unittest.TestCase):
    """只解释"正文没解释的"，且不许重复凑数。"""

    def test_no_explanation_when_already_explained(self):
        definition = glossary._load()["盘权"]["text"]
        ans = "关于盘权：" + definition
        self.assertNotIn("盘权", [item["term"] for item in glossary.terms_in(ans)])

    def test_no_duplicate_when_shorter_term_inside_longer(self):
        """`得分区` 被 `进攻得分区` 的解释覆盖时，不许再单列一遍。"""
        ans = "你必须在对方进攻得分区里接稳盘才算得分。"
        terms = [t["term"] for t in glossary.terms_in(ans)]
        self.assertIn("进攻得分区", terms)
        self.assertNotIn("得分区", terms)

    def test_chat_answer_gets_no_glossary(self):
        """闲聊不挂术语解释：一句话寒暄就该只是一句话寒暄。"""
        self.assertEqual(glossary.block("你好，我是飞盘规则助手，随便问。"), "")

    def test_cap_number_of_terms(self):
        """名额有上限，否则新手面前突然摊开一整篇词典。"""
        ans = ("得分区 盘权 接盘 触地 界外 边界线 得分线 中心区 "
               "比赛中止 竞技状态 防守队员 进攻队员 轻微接触 界内线")
        self.assertLessEqual(len(glossary.terms_in(ans)), glossary.MAX_TERMS)

    def test_max_terms_is_four(self):
        """定 4 个是量出来的：新手问题里最多同时出现 4 个生词。"""
        self.assertEqual(glossary.MAX_TERMS, 4)

    def test_heading_lives_in_one_place_only(self):
        """标题只能出现在**前端**一处，后端不许再输出一次。

        ⚠️ 真实渲染检查抓到的（2026-10-10）：两边都写了
            「📖 上面这几个词的意思 · 官方名词定义」＋「上面这几个词的意思：」
        —— 同一句话说两遍。所以 `block()` 现在**只输出词条**。
        守卫钉住这个职责边界，否则下次"优化一下"就会又写回去。
        """
        terms = glossary.terms_in("确立盘权、触地点都要在区内。")
        self.assertTrue(terms)
        out = glossary.block("确立盘权、触地点都要在区内。")
        self.assertNotIn("上面这几个词", out, "标题归前端渲染，后端重复输出了一遍")
        # 前端那一处必须还在
        gl_idx = APP_JS_CODE.index("function glossaryNode")
        self.assertIn("上面这几个词", APP_JS_CODE[gl_idx:gl_idx + 600])

    def test_block_only_lists_entries(self):
        """block() 的每一行都是 `- **词**：释义`，没有别的结构。"""
        out = glossary.block("你必须在进攻得分区接盘并确立盘权。")
        self.assertTrue(out.strip())
        for line in out.splitlines():
            self.assertRegex(line.strip(), r"^- \*\*.+\*\*：\S",
                             f"block 行格式不对：{line}")


class TestGlossaryWiring(unittest.TestCase):
    """接线：字段真的从生成层传到前端了。"""

    def test_generator_returns_glossary_field(self):
        self.assertIn('"glossary": g', GENERATOR_CODE)
        # 降级路径也要给：只给原文时术语密度最高，新手最需要
        self.assertGreaterEqual(GENERATOR_CODE.count('"glossary":'), 3)

    def test_server_passes_glossary_field(self):
        self.assertIn('"glossary": glossary', SERVER_CODE)
        # 拒答路径必须显式置空（且**在 not_found_text 之后**、llm 分支之前）
        #
        # ⚠️ 窗口 400 → 900（2026-10-13）：加了埋点后，`not_found_text(...)` 与
        #    `glossary = ""` 之间插进了约 300 字符的 `querylog_mod.log(...)`，
        #    原窗口挤不下 ⇒ 假红。
        #    ⚠️ **改的是窗口大小，不是判据** —— "拒答路径要显式置空、且位置在
        #       not_found_text 之后"这条一个字没动，只是给它足够视野。
        idx = SERVER_CODE.index("not_found_text(")
        window = SERVER_CODE[idx:idx + 900]
        self.assertRegex(window, r'glossary = ""')

    def test_not_found_has_no_glossary(self):
        """拒答不给术语解释：那段话本来就该短。"""
        self.assertIn('glossary = ""', SERVER_CODE)

    def test_frontend_renders_glossary(self):
        self.assertIn("glossaryNode", APP_JS_CODE)
        self.assertIn("payload.glossary", APP_JS_CODE)
        # 默认展开，不是藏进"展开细节" —— 藏着就等于没做
        gl_idx = APP_JS_CODE.index("function glossaryNode")
        self.assertIn("gloss-h", APP_JS_CODE[gl_idx:gl_idx + 600])
        # 必须是**直接 append 到 box**（默认可见），
        # 一旦被塞进 moreNode / 默认 display:none 就等于没做 —— 红队钉过这条。
        bot = APP_JS_CODE[APP_JS_CODE.index("function botNode"):]
        self.assertRegex(bot, r"glossaryNode\(m\.payload[^\n]*box\.appendChild\(gl\)")

    def test_glossary_block_is_visually_distinct(self):
        self.assertIn(".gloss", APP_CSS)
        # 遵循极简约定：不加阴影、不加完整边框
        gloss_css = APP_CSS.split(".gloss{")[1].split(".gloss-h")[0]
        self.assertNotIn("box-shadow", gloss_css)

    def test_glossary_labels_its_source(self):
        """必须标"官方名词定义"，否则用户以为这也是 AI 说的。

        ⚠️ **剥注释后**断言（红队 4 逼出来的）：不剥的话，
        函数上方那段注释里的"官方名词定义"会让这条守卫永远绿 ——
        把渲染里那行真删掉也照样过。
        """
        gl_idx = APP_JS_CODE.index("function glossaryNode")
        self.assertIn("官方名词定义", APP_JS_CODE[gl_idx:gl_idx + 600],
                      "渲染里没标来源 —— 用户会以为这也是 AI 说的")


class TestGlossaryRealAnswers(unittest.TestCase):
    """对真实答案跑一遍 —— 守的是效果，不是函数。"""

    def test_beginner_answer_gets_help(self):
        """实测基线：新手问法必须能拿到术语解释。"""
        real = ("得分就一句话：你在对方得分区里接稳盘，就算得分 (14.1)。"
                "接盘时你所有触地点都在进攻得分区内；接住之后你得把盘稳稳控制住"
                "（确立盘权），整个落地过程都没脱手 (14.1.2)。")
        out = glossary.block(real)
        self.assertIn("确立盘权", out)
        self.assertTrue(out.strip())

    def test_collision_answer_gets_help(self):
        real = ("抢占位置让对手躲不开、发生非轻微接触，这是阻挡犯规。")
        self.assertTrue(glossary.block(real).strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
