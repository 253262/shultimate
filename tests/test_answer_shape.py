# -*- coding: utf-8 -*-
"""回答形态守卫（2026-10-06 起）：回答不再分节，改成「直答 + 可选展开」。

【这一版守什么】
owner 实测反馈（原话）：「我问你一句话，你乱七八糟说了一堆」——
他问「比赛时男女比例」，收到的是一屏
「结论 / 依据 / 该怎么做 / 规则没规定的部分 / 还需确认」。他要的是
**他怎么问，我就怎么答**。于是这一轮做了三件事：

  1. `app/generator.py` 删掉了那张**五段输出清单**。清单本身就是"把回答填满"的
     邀请 —— 一边写着"长度跟着问题走、别硬凑小节"，一边列着五个待填的小节，
     模型必然去填。
  2. 改成「直答 + `<!--more-->` + 细节」：细节段默认折叠，用户点一下才展开。
  3. `static/app.js` 删掉了把那些词渲染成小标题的分支（`SECTIONS`）。

【为什么要有这个文件】
**"删掉一个结构"这件事不会自动保持删除。** 下一轮很容易有人觉得
"提示词里是不是该写清输出什么"，于是把清单又加回来 —— 那就前功尽弃，而且
**没有任何东西会报错**。所以这里守的是**否定式**。

⚠️ 只守否定式（"不许出现什么"），不守"必须出现哪些词"：
   清单式断言会把正常实现变更误报成违规（本项目 10-06 误报过
   `electron().app.getAppPath()`），也会在提示词里提一句要禁止它时就满足，
   等于没守。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import generator  # noqa: E402

# 被删掉的那五个小节名。**它们是历史，不许再作为模板行出现。**
DEAD_SECTIONS = ("结论", "依据", "该怎么做", "规则没规定的部分", "还需确认")

# 直答 / 细节的分隔标记 —— Python 提示词与前端 JS 必须用同一个字面量。
MORE_MARK = "<!--more-->"


def _checklist_lines(text: str) -> list[str]:
    """"把这段文本里以模板形式出现的旧小节名挑出来。

    单独抽成函数是为了能**证明它抓得到**（见 TestRedTeam）——
    "报 0 错"之前必须先证明它能报出"错"，否则全绿只证明没抛异常。
    """
    return [f"**{s}**" for s in DEAD_SECTIONS if f"**{s}**" in text]


def _strip_js_comments(src: str) -> str:
    """剥掉 JS 注释。

    ⚠️⚠️ 顺序：**先 `//` 后 `/* */`**。反了的话，块注释里的 `/*` 会把后面几百行
    全吞掉 ⇒ **静默假绿**（本项目已犯过 4 次，见 RULES.md「通用工程踩坑」）。
    """
    src = re.sub(r"//[^\n]*", "", src)
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


class TestNoSectionChecklist(unittest.TestCase):
    """五个旧小节名不许以模板行的形式回来。"""

    def test_system_prompt_has_no_section_template(self):
        bad = _checklist_lines(generator.SYSTEM_PROMPT)
        self.assertEqual(
            bad, [],
            "SYSTEM_PROMPT 里又出现了旧的小节模板行 " + repr(bad) +
            " —— 这张清单是 owner 点名要删掉的（它会把回答撑成一屏），不要再加回来")

    def test_not_found_template_has_no_section_label(self):
        """兜底模板也不许写「**结论**：…」——它本身就是答案，不需要贴标签。"""
        txt = generator.not_found_text([])
        self.assertEqual(_checklist_lines(txt), [])
        self.assertNotIn("**结论**", txt)


class TestRedTeam(unittest.TestCase):
    """先证明检测器**抓得到**，再相信它报的"干净"。"""

    def test_detector_catches_a_reintroduced_checklist(self):
        broken = generator.SYSTEM_PROMPT + "\n**结论**：一句话直接回答\n**依据**：分点说明\n"
        bad = _checklist_lines(broken)
        self.assertIn("**结论**", bad)
        self.assertIn("**依据**", bad)

    def test_detector_is_not_always_empty(self):
        """防止有人把 `_checklist_lines` 改成恒返回 []（那会让上面两条静默变绿）。"""
        self.assertNotEqual(_checklist_lines("**依据**：x"), [])


class TestDetailMarker(unittest.TestCase):
    """`<!--more-->` 是直答与细节的分界，两端必须用同一个字面量。"""

    def test_marker_present_in_prompt(self):
        self.assertIn(MORE_MARK, generator.SYSTEM_PROMPT,
                      "提示词里没有分隔标记 —— 模型没法把细节分开，折叠就无从谈起")

    def test_marker_present_in_frontend(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn(MORE_MARK, js,
                      "static/app.js 里没有这个标记 —— 提示词产出了细节，前端切不开")

    def test_chat_path_never_produces_a_detail_section(self):
        """闲聊不该有"展开细节"：它压根不检索，展开也没内容。"""
        self.assertIn("不要", generator.CHAT_SYSTEM)
        # 对话路提示词必须**点名**禁止写这个标记（否则模型照抄规则路的格式）
        self.assertIn(MORE_MARK, generator.CHAT_SYSTEM)


class TestFrontendSectionRendererIsGone(unittest.TestCase):
    """前端不许再把那五个词渲染成小标题。"""

    def test_sections_renderer_removed(self):
        js = _strip_js_comments((ROOT / "static" / "app.js").read_text(encoding="utf-8"))
        self.assertNotIn("SECTIONS", js,
                         "app.js 里还留着 SECTIONS —— 界面会替模型长出小节标题")

    def test_redteam_comment_stripping_is_real(self):
        """证明上面的剥注释不是空操作：**带注释的**源码确实含这个词、剥完没有。"""
        raw = '/* 这里提到 SECTIONS 数组 */\nconst x = 1;\n'
        self.assertIn("SECTIONS", raw)
        self.assertNotIn("SECTIONS", _strip_js_comments(raw))

    def test_strip_order_block_comment_is_not_swallowed(self):
        """⚠️ 剥注释顺序的回归：块注释里的 `//` 不能让后面整段消失。

        反例：先剥 `/* */` 再剥 `//` 时，只要注释里出现 `//`，剩下的半条
        注释会把后面的代码一起吃掉 —— 表现就是"什么都检查不到"却全绿。
        """
        src = "/* 说明 // 副作用 */\nconst KEEP_ME = 1;\n"
        self.assertIn("KEEP_ME", _strip_js_comments(src))


class TestAssistants(unittest.TestCase):
    """预设精简成 2 个：被删掉的**不许回来**。"""

    def setUp(self):
        from app import assistants
        self.m = assistants
        self.ids = [a["id"] for a in self.m.ASSISTANTS]

    def test_removed_presets_stay_removed(self):
        for gone in ("newbie", "onsite"):
            with self.subTest(preset=gone):
                self.assertNotIn(gone, self.ids,
                                 f"预设「{gone}」是 2026-10-06 owner 点名删掉的，别加回来")

    def test_ids_are_unique(self):
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_default_preset_adds_no_style(self):
        """默认预设**不许**带追加风格。

        这是真正的风险点：「新手模式」被删，就是因为它那句
        "不要省略『该怎么做』，新手最需要知道场上具体动作"在**主动把回答撑长**。
        要是哪天有人给默认预设也加一段"要讲清楚…"，默认形态就悄悄退回去了。
        """
        d = self.m.get(self.m.DEFAULT_ID)
        self.assertEqual(d["style"], "",
                         "默认预设带上了追加风格 —— 它会覆盖/顶长默认回答，"
                         "而默认回答必须是最短的那一版")

    def test_every_preset_keeps_citation_on(self):
        for a in self.m.ASSISTANTS:
            with self.subTest(preset=a["id"]):
                self.assertTrue(a["cite"])


class TestMoreButtonIsVisible(unittest.TestCase):
    """"展开细节"是入口 —— 入口不能被藏起来（本项目踩过：入口太深 = 等于没有）。"""

    def setUp(self):
        self.css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

    def test_button_rule_exists(self):
        self.assertIn(".more-btn", self.css)

    def test_button_is_not_hidden_until_hover(self):
        """`.more-btn` 自己的规则块里不许出现 opacity —— 那会重犯"藏入口"的错。"""
        m = re.search(r"\.more-btn\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, "找不到 .more-btn 的样式规则")
        self.assertNotIn("opacity", m.group(1),
                         ".more-btn 被做成需要 hover 才出现了 —— 用户找不到的功能 = 不存在")

    def test_button_not_built_inside_the_hover_only_tools_container(self):
        """不许把入口塞进 `.tools`（那里是 `opacity:0`，hover 才出现）。"""
        js = _strip_js_comments((ROOT / "static" / "app.js").read_text(encoding="utf-8"))
        self.assertIn("function moreNode", js, "找不到 moreNode —— 入口被删了？")
        seg = js.split("function moreNode")[1].split("function botNode")[0]
        self.assertIn('"more-btn"', seg, "moreNode 里没有创建按钮")
        self.assertNotIn("tools", seg,
                         "展开入口被建到了 .tools 容器里 —— 那是 hover 才出现的，"
                         "等于把入口藏起来")


if __name__ == "__main__":
    unittest.main(verbosity=2)
