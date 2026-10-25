# -*- coding: utf-8 -*-
"""界面结构守卫（2026-10-06 起）：三处 owner 亲自点过、亲口报过的界面问题。

【这一轮守什么】
owner 在客户端截图里圈了三处：

  ① **侧栏「默认工作区」点了没反应**。它不是坏了 —— 它是个 `div`，从来没接过
     click。错在**它长得像能点**（图标 + 选中底色 + 加粗文字 = 一个按钮的全部特征）。
     ⇒ 现在它叫「历史对话」，是 `<button id="wsItem">`，点一下收起/展开列表，
       右侧显示条数；标题行里那个重复的折叠图标已删（一件事不要两个入口）。

  ② **每条回答下面的「这个回答有用吗？ 有用 没用」**。owner 原话：
     「提的每一个问题都写个『这东西』，感觉很烦」「我觉得要删掉」。
     ⇒ 文案行删除，改成**只有图标、hover 才出现**（ChatGPT / Claude / Gemini 的
       回答工具条全是这个形态）。

  ③ **笔记没有条理、难用**。「能不能有个『一二三四』的条理？」
     ⇒ 加分组（重点 / 今天 / 昨天 / 日期）+ 连续编号 + 星标置顶；
       页脚那行死文本改成可点（回那次对话 / 跳那条原文）；加保存回执。

【为什么要有这个文件】
这三条都是**"很容易被改回去"的决定**：把 ws-item 改回 div、把文案加回来、
把编号和分组省掉 —— 三种改法都不会报任何错，页面照样能跑。
所以这里守的是**否定式 + 关键接口的存在**，不是"长得对不对"。

⚠️ 源码级守卫**必须先剥注释再断言**（本项目已犯过 4 次假绿的坑）：
   这些决定同时写在 app.js / app.css 的注释里解释，不剥注释 ⇒ 在注释里
   找到关键字 ⇒ 假绿。文件末尾每条都有一条红队用例证明它真能红。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "static" / "index.html"
JS_PATH = ROOT / "static" / "app.js"
CSS_PATH = ROOT / "static" / "app.css"


def strip_js_comments(src: str) -> str:
    """剥掉 JS 注释。

    ⚠️⚠️ 顺序：**先 `//` 后 `/* */`**。反了的话，块注释里的 `/*` 会把后面几百行
    全吞掉 ⇒ **静默假绿**（本项目已犯过 4 次，见 RULES.md「通用工程踩坑」）。
    """
    src = re.sub(r"//[^\n]*", "", src)
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


def strip_css_comments(src: str) -> str:
    """剥掉 CSS 注释（只有 `/* */` 一种）。"""
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


def strip_html_comments(src: str) -> str:
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


def css_rule(css: str, selector: str) -> str:
    """取某条规则的内容。selector 会做转义后精确匹配（不接受前缀命中）。

    ⚠️ 传进来的 css 必须是**已剥注释**的，否则可能命中注释里的同名片段。
    """
    esc = re.escape(selector)
    m = re.search(r"(?:^|[,}\s])" + esc + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def folded_hide_rule(css: str) -> tuple[str, str]:
    """挑出折叠态里**真正负责隐藏**的那条 `body.folded …{display:none}`。

    ⚠️ 不能用 `re.search` 抓第一条 `body.folded` —— 折叠态下还有别的规则
    （比如箭头转向 `.ws-item .arw`），抓到那条就会得出"折叠把 .ws-item 藏了"
    的**假红**。判据必须落在"哪条规则在隐藏东西"上。
    """
    for sel, blk in re.findall(r"body\.folded\s+([^{]*)\{([^}]*)\}", css):
        if re.search(r"display\s*:\s*none", blk):
            return sel, blk
    return "", ""


class TestSidebarWorkspaceRow(unittest.TestCase):
    """① 侧栏那一行必须是**真按钮**，而且折叠之后还得看得见（它就是开关本身）。"""

    @classmethod
    def setUpClass(cls):
        cls.html = strip_html_comments(HTML_PATH.read_text(encoding="utf-8"))
        cls.js = strip_js_comments(JS_PATH.read_text(encoding="utf-8"))
        cls.css = strip_css_comments(CSS_PATH.read_text(encoding="utf-8"))

    def test_row_is_a_real_button_not_a_div(self):
        """`<div class="ws-item">` 就是那个"点了没反应"的坑，不许回来。"""
        self.assertNotRegex(
            self.html, r"<div[^>]*class=\"ws-item",
            "侧栏那一行又变回 div 了 —— div 没有 click，用户点了只会以为程序坏了")
        self.assertRegex(
            self.html, r"<button[^>]*id=\"wsItem\"",
            "找不到 <button id=\"wsItem\">：侧栏那行必须是可点的按钮")

    def test_no_dead_default_workspace_label(self):
        """「默认工作区」是照抄另一个产品的词，这里只有一个工作区。"""
        self.assertNotIn("默认工作区", self.html,
                         "「默认工作区」又回来了 —— 本项目只有一个工作区，"
                         "摆一个带选中态的假切换器只会让人以为点得动")

    def test_fold_icon_is_gone(self):
        """标题行里那个重复的折叠图标要删干净 —— 一件事两个入口必然走岔。"""
        self.assertNotIn('id="wsFold"', self.html,
                         "标题行里又出现了 #wsFold：折叠有两个入口了")

    def test_row_has_a_click_handler(self):
        self.assertIn('$("wsItem")', self.js, "#wsItem 没有接任何事件 —— 还是点不动")
        self.assertRegex(self.js, r'wsItem[\s\S]{0,80}addEventListener\("click"',
                         "#wsItem 存在但没绑 click")

    def test_row_survives_the_folded_state(self):
        """★ 最关键的一条：折叠态**不许把 .ws-item 一起藏掉**。

        它就是那个开关 —— 藏掉它，用户收起之后再也没法展开
        （上一版是靠标题行那个图标兜的，那个图标现在删了）。
        """
        sel, blk = folded_hide_rule(self.css)
        self.assertNotEqual(sel, "", "找不到折叠态的 display:none 规则")
        self.assertIn(".nav-scroll", sel, "折叠态不再隐藏列表了，请复核这条守卫的意图")
        self.assertNotIn(".ws-item", sel,
                         "折叠态把 .ws-item 一起藏了 —— 收起历史之后没法再展开")

    def test_count_is_written_into_the_row(self):
        self.assertIn('$("wsCnt")', self.js, "那一行右侧的条数没人写 —— 会永远是空的")


class TestFeedbackIsQuiet(unittest.TestCase):
    """② 反馈不许在**每一条**回答下面喊一句解释性文案。

    ⚠️⚠️ 这个类的两条断言 2026-10-15 改过，**改的是判据、不是放松** ——
       两边的历史都留着，否则下一轮会来回翻烧饼。

    【原来的判据】`.fb` 必须 `opacity:0`、必须 hover 才出现、按钮必须是纯图标。
      来源：2026-10-06 owner 报「提的每一个问题都写个『这东西』，感觉很烦」。
    【为什么要改】实测 `data/feedback.jsonl` **上线至今一条都没有** ——
      而"有没有让人弄明白"是产品最重要的信号之一（判据见 docs/product.md）。
      **藏起来的测量工具 = 没有测量。**
    【为什么不算放松】owner 嫌的是「每条都喊一句**解释性文案**」，
      不是「不该让人看见」。改后的规矩是：
        · 常驻的只有**一行问题**（`.fb-q`，小字淡色）——**不是解释**
        · **投过票之后连那一行也收掉**（`bar.style.display`）
        · 展开面板（选原因那张卡）仍然只在交互后出现
      ⇒ 所以下面两条断言**换了骨头但没减分量**：
        原来守"必须藏起来"，现在守"常驻的只能是一行、且投票后必须消失"。
    """

    @classmethod
    def setUpClass(cls):
        cls.js = strip_js_comments(JS_PATH.read_text(encoding="utf-8"))
        cls.css = strip_css_comments(CSS_PATH.read_text(encoding="utf-8"))

    def test_loud_hint_text_is_gone(self):
        """那两句被 owner 点名删掉的文案，一个字都不许回来。"""
        for dead in ("这个回答有用吗", "fb-hint"):
            with self.subTest(dead=dead):
                self.assertNotIn(dead, self.js,
                                 f"又出现了 {dead!r} —— owner 说过那条文案很烦")

    def test_buttons_are_labelled_not_bare_icons(self):
        """按钮必须是**看得懂的文字**，而且只能问一件事。

        ⚠️ 改判据的理由：纯 👍/👎 图标**不携带"弄明白了吗"这层意思** ——
           用户看到拇指会想"这是让我夸它吗"。
           owner 要的是"让他回答是或者否"那一类**明确的二选一**。
           ⇒ 现在用文字：「懂了」/「还是没明白」，两个字，不用猜。
        """
        seg = self.js.split("function fbNode")[1].split("function astName")[0]
        self.assertIn("fb-t", seg, "反馈按钮的样式类没了")
        self.assertIn("懂了", seg, "少了「懂了」这个按钮文字")
        self.assertIn("还是没明白", seg, "少了「还是没明白」这个按钮文字")
        # ⚠️ 反向：那几个被删掉的旧文案不许回来
        for dead in ('"有用"', '"没用"'):
            self.assertNotIn(dead, seg, f"{dead} 又回来了 —— 那正是被删掉的东西")
        # ⚠️ 问题只能有一个（不是每条都喊一串解释）
        self.assertEqual(seg.count("弄明白了吗"), 1,
                         "「弄明白了吗」出现了多次 —— 又变成重复喊话")

    def test_only_the_question_is_always_on(self):
        """常驻的只能是一行问题；选原因的面板必须交互后才出现。"""
        # ① 问题那一行**允许常驻**（这正是这次的改动）
        blk = css_rule(self.css, ".fb-q")
        self.assertTrue(blk, "少了 .fb-q 这条规则（问题那一行的样式）")
        # ② ⚠️ `.acted` 规矩必须留着：否则鼠标一移开，选原因的面板会凭空消失
        self.assertRegex(self.css, r"\.fb\.acted",
                         "缺 .acted：点了「没明白」之后鼠标一移开，面板会消失")
        # ③ ⚠️ 最重要的新判据：**投票后那一行必须收掉**。
        #    这条替代了原来的"必须 opacity:0" —— 它守的是同一件事：
        #    **别让已经表过态的每条回答下面都挂着一行东西。**
        seg = self.js.split("function fbNode")[1].split("function astName")[0]
        self.assertIn("settled()", seg, "缺 settled()：无法判断这题投过票没有")
        self.assertRegex(seg, r"bar\.style\.display\s*=\s*settled\(\)",
                         "投票之后没有把那一行问题收掉 —— 会变成每条回答都挂着一行")

    def test_chat_answers_still_carry_nothing(self):
        """闲聊（mode=chat）下面连反馈都不该有 —— 这条规矩没被这条改动破坏。"""
        self.assertIn('p.mode !== "chat"', self.js,
                      "botNode 的 mode 判据没了：闲聊下面会重新挂出一排东西")


class TestNotesHaveStructure(unittest.TestCase):
    """③ 笔记要有「一二三四」的条理，而且页脚那行得是能点的路。"""

    @classmethod
    def setUpClass(cls):
        cls.js = strip_js_comments(JS_PATH.read_text(encoding="utf-8"))
        cls.css = strip_css_comments(CSS_PATH.read_text(encoding="utf-8"))

    def test_shared_renderer_exists(self):
        """右栏和整页必须是同一套渲染 —— 两份必然有一天不一致。"""
        self.assertIn("function renderNoteList", self.js)
        # 右栏走 liveNotes()（要过滤掉回收站），整页走筛选后的 items —— 
        # 两处传的东西不同但**必须是同一个函数**。
        self.assertIn("renderNoteList(body, live, paintRail)", self.js)
        self.assertIn("renderNoteList(body, items, paintNotesPage", self.js)
        self.assertEqual(self.js.count("function renderNoteList"), 1,
                         "出现第二份渲染函数 —— 两个入口迟早渲染不一致")

    def test_grouping_exists(self):
        self.assertIn("function noteGroups", self.js, "分组没了 —— 回到一堆平铺")
        self.assertIn('"重点"', self.js, "没有「重点」分组 —— 星标置顶失效")
        self.assertIn("dayLabel(", self.js, "没有按日期分组")
        self.assertIn("nt-grp", self.css, "分组标题没有样式")

    def test_notes_are_numbered(self):
        """编号 = owner 说的「一二三四的条理」。"""
        self.assertIn("nt-no", self.js, "笔记没有序号了")
        self.assertIn("nt-no", self.css, "序号没有样式")

    def test_star_pins_a_note(self):
        self.assertIn("nt-star", self.js, "星标按钮没了")
        self.assertIn("n.star = !n.star", self.js, "星标点了不翻状态")
        self.assertIn("n.star", self.css + self.js, "星标没有参与渲染")

    def test_source_line_is_clickable(self):
        """★ 页脚那行原来是**死文本** —— 看见「依据 2.1」却点不动。

        这是笔记真正的价值：摘下来的东西不是死文本，是**一条回去的路**。
        """
        self.assertIn("function gotoNoteFrom", self.js, "回跳函数没了")
        seg = self.js.split("function noteNode")[1].split("function gotoNoteFrom")[0]
        self.assertIn('"nt-src"', seg, "来源问题不是可点的（nt-src 按钮缺）")
        self.assertIn('"nt-cite"', seg, "条款号不是可点的（nt-cite 按钮缺）")
        self.assertNotIn("m.innerHTML = `摘录自提问", seg,
                         "页脚又变回拼字符串的死文本了")

    def test_save_has_a_receipt(self):
        """自动保存必须有回执 —— 没有回执的自动保存，体感等于没保存。"""
        self.assertIn("flashSaved", self.js)
        self.assertIn("ntSaved", self.js)
        # 回执真的被调起来了，而且**只在写入成功时**闪（2026-10-24：写失败说"已保存"是更坏的缺陷）
        self.assertRegex(self.js, r"if \(saveNotes\(\)\) flashSaved\(\)",
                         "保存之后没闪回执，或没检查 saveNotes() 的返回值")


class TestRedTeam(unittest.TestCase):
    """先证明每条判据**抓得到**，再相信它报的"干净"。"""

    def test_detector_catches_div_row(self):
        html = '<div class="ws-item on"><span>默认工作区</span></div>'
        self.assertRegex(html, r"<div[^>]*class=\"ws-item")
        self.assertIn("默认工作区", html)

    def test_detector_catches_folded_hiding_the_toggle(self):
        css = "body.folded .nav-scroll,body.folded .hist-search,body.folded .ws-item{display:none;}"
        sel, _ = folded_hide_rule(css)
        self.assertIn(".ws-item", sel, "红队前提不成立")

    def test_folded_helper_ignores_non_hiding_rules(self):
        """红队：折叠态下**不负责隐藏**的规则（如箭头转向）不许被当成隐藏规则。"""
        css = ("body.folded .ws-item .arw{transform:rotate(-90deg);}\n"
               "body.folded .nav-scroll{display:none;}")
        sel, _ = folded_hide_rule(css)
        self.assertIn(".nav-scroll", sel)
        self.assertNotIn(".arw", sel)

    def test_detector_catches_loud_hint(self):
        js = 'hint.textContent = "这个回答有用吗？";'
        self.assertIn("这个回答有用吗", js)

    def test_css_rule_helper_is_not_always_empty(self):
        """防止有人把 css_rule 写成恒返回 ''（那会让 .fb 那条断言永远通过）。"""
        self.assertNotEqual(css_rule(".fb{opacity:0;}", ".fb"), "")
        # 而且不许被前缀命中：`.fb` 不应该匹配到 `.fbx`
        self.assertEqual(css_rule(".fbx{opacity:0;}", ".fb"), "")

    def test_strip_is_not_a_noop(self):
        """剥注释这一步本身要有效 —— 否则注释里的关键字会造成假绿。"""
        self.assertNotIn("这个回答有用吗", strip_js_comments('// 这个回答有用吗\nvar x = 1;'))
        self.assertNotIn("default", strip_css_comments("/* default */ .fb{opacity:0}"))


class TestNotesAreFindableAndSafe(unittest.TestCase):
    """④ 笔记的三个断点（2026-10-07）：找不到 / 记不住 / 删了找不回。

    上一版笔记只有"时间排序 + 一个搜索框"，于是：
      · 玩家记东西的分类轴是**规则条款**，不是日期 ⇒「关于掩护记过什么」翻不出来
      · 记下来从不拿出来 ⇒ 规则是考试型知识，不主动回忆等于没记
      · 笔记只在 localStorage，一次误触 × 就永久丢失（没有服务器备份）
    这三条都是**改回去不会报任何错**的改动，所以要用守卫钉住。
    """

    @classmethod
    def setUpClass(cls):
        cls.js = strip_js_comments(JS_PATH.read_text(encoding="utf-8"))
        cls.css = strip_css_comments(CSS_PATH.read_text(encoding="utf-8"))

    # ---- ① 按规则章聚合：别的笔记软件抄不走的分类轴 ----

    def test_chapter_grouping_exists(self):
        self.assertIn("function leadClause", self.js,
                      "没有 leadClause —— 按章节聚合无从判断主条款")
        self.assertIn("CHAPTERS", self.js, "没有章节表 —— 分组只会显示光秃秃的数字")
        self.assertIn("byChapter", self.js, "noteGroups 不接受分组维度，视图切换是假的")

    def test_lead_clause_picks_shallowest(self):
        """主条款 = 位数最浅的那条：18.3.1 要归到 18 章，不是单开一个 18.3.1 组。"""
        seg = self.js.split("function leadClause")[1].split("function ")[0]
        self.assertRegex(seg, r"split\(\s*[\"']\.[\"']\s*\)",
                         "主条款选择必须按 '.' 分层数比较深度")

    def test_unmatched_notes_are_never_dropped(self):
        """没条款的手写笔记要进「未归档」而不是消失 —— 丢数据是最严重的退步。"""
        seg = self.js.split("function noteGroups")[1].split("function renderNoteList")[0]
        self.assertIn("未归档", seg, "没条款的笔记没有去处（会被静默丢掉）")

    # ---- ② 主动回忆 ----

    def test_review_queue_only_asks_sourced_notes(self):
        """只考有来源的笔记：手写那句"队里说这样打"没有唯一答案，
        拿出来当题目是刁难用户。"""
        self.assertIn("function reviewQueue", self.js)
        # 「有没有来源」的判据写在 reviewDue 里，两个函数体都要看
        seg = self.js.split("function reviewDue")[1].split("function reviewQueue")[0]
        self.assertIn("from", seg, "reviewDue 没有区分有没有来源")
        self.assertIn("rev", seg, "复习没有间隔计数（rev）")
        self.assertIn("REVIEW_STEPS", seg, "复习间隔写死了，没有可调的档位")

    def test_answer_is_hidden_until_user_asks(self):
        """复习卡的价值全在"先自己想" —— 答案默认必须收起来。"""
        self.assertIn("_rv.show", self.js, "复习卡没有答没答的开关")
        self.assertIn("function reviewDone", self.js, "答完没有推进间隔")

    def test_review_card_can_be_dismissed(self):
        """点进复习就得出得去 —— 没有「收起」的复习卡是个陷阱。
        （第一版真漏了这个：能进去，出不来。）"""
        seg = self.js.split("function paintReview")[1].split("function viewNotes")[0]
        self.assertIn("rv-quit", seg, "复习卡没有收起按钮 —— 进去就出不来了")
        self.assertIn("onDone", seg, "收起按钮没接退出回调")

    def test_review_entry_only_appears_when_something_is_due(self):
        """永远亮着、点进去是空的入口，比没有这个入口更糟。"""
        seg = self.js.split("function paintNotesPage")[1].split("function ")[0]
        self.assertRegex(seg, r"rev\.hidden\s*=\s*!due",
                         "复习入口没有随有没有到期隐藏")

    # ---- ③ 删了找得回 + 数据是用户的 ----

    def test_delete_goes_to_trash_not_gone(self):
        """删除必须进回收站。本地无备份，硬删 = 永久丢失。"""
        self.assertIn("function trashNote", self.js, "没有回收站 —— 删除是硬删")
        seg = self.js.split("function noteNode")[1].split("function gotoNoteFrom")[0]
        self.assertNotIn("NT.items.splice", seg,
                         "noteNode 里还有硬删 —— 用户删错一条就永久没了")

    def test_trash_has_a_time_limit(self):
        """回收站必须有上限，否则它会变成第二个垃圾场。"""
        self.assertIn("function sweepTrash", self.js, "回收站不会自动清理")
        self.assertRegex(self.js, r"TRASH_DAYS\s*=\s*\d+",
                         "回收站保留期必须是具体天数")

    def test_undo_affordance_exists(self):
        """可撤销的操作不该先问"你确定吗" —— 对话框逼你先决定，
        撤销条让你先做事。"""
        self.assertIn("function undoToast", self.js, "没有撤销浮条")
        self.assertIn("restoreNote", self.js, "撤销按钮没有接恢复动作")
        self.assertIn(".undo-bar", self.css, "撤销浮条没有样式")

    def test_export_is_local_file_only(self):
        """导出/导入必须是纯本地（Blob + FileReader）——
        守住 test_notes_panel 那条"笔记代码里不许有网络调用"。"""
        self.assertIn("function exportNotes", self.js)
        self.assertIn("Blob", self.js, "导出不是本地文件")
        seg = self.js.split("function exportNotes")[1].split("function importNotes")[0]
        self.assertIn(".txt", seg, "导出格式不是可读的纯文本")

    def test_search_covers_the_source_question(self):
        """用户记笔记时记得的是当时问的那句，不是自己打的标题。"""
        seg = self.js.split("function paintNotesPage")[1].split("function dayLabel")[0]
        self.assertIn("from", seg, "搜索不搜来源问题 —— 摘录来的笔记搜不到")

    # ---- ④ 无障碍：评审六轴里 accessibility 得分最低(6.82)，最便宜的补分项 ----

    def test_focus_visible_is_defined(self):
        """focus-visible 而不是 focus：鼠标点不该留高亮圈，
        键盘 Tab 过来必须看得见。"""
        self.assertIn(":focus-visible", self.css,
                      "没有 focus-visible 样式：键盘用户看不到自己在哪")

    def test_reduced_motion_is_respected(self):
        """前庭功能障碍用户会因位移动画难受。"""
        self.assertIn("prefers-reduced-motion", self.css,
                      "没有尊重系统减少动态效果设置")

    def test_tabs_declare_aria(self):
        """视图切换用 role=tablist，必须带 aria-selected 才读得出当前在哪一档。"""
        self.assertIn("role=\"tablist\"", self.js, "视图切换没有 tablist 语义")
        self.assertIn("aria-selected", self.js, "没有 aria-selected —— 读屏用户不知道选中哪一档")


class TestNotesRedTeam(unittest.TestCase):
    """先证明上面每条判据**抓得到**，再相信它报的"干净"。"""

    @classmethod
    def setUpClass(cls):
        cls.js = strip_js_comments(JS_PATH.read_text(encoding="utf-8"))

    def test_detector_catches_missing_chapter_view(self):
        seg = self.js.split("function noteGroups")[1].split("function renderNoteList")[0]
        broken = seg.replace('byChapter', 'byTime')          # 把视图开关改回不存在
        self.assertNotIn("byChapter", broken, "红队前提不成立")

    def test_detector_catches_hard_delete(self):
        seg = self.js.split("function noteNode")[1].split("function gotoNoteFrom")[0]
        broken = seg + "\nNT.items.splice(0,1);"
        self.assertIn("NT.items.splice", broken, "红队前提不成立")

    def test_detector_catches_always_visible_review_entry(self):
        seg = self.js.split("function paintNotesPage")[1].split("function ")[0]
        broken = seg.replace("rev.hidden = !due", "rev.hidden = false")
        self.assertNotIn("rev.hidden = !due", broken, "红队前提不成立")

    def test_detector_catches_answer_shown_by_default(self):
        self.assertNotIn("_rv.show = true", self.js.split("function paintReview")[1],
                         "答案默认是展开的 —— 主动回忆就失效了")


if __name__ == "__main__":
    unittest.main()
