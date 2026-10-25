# -*- coding: utf-8 -*-
"""侧栏底部「账号行（头像）」必须钉在底部的守卫。

⚠️ 存在的理由（2026-10-06 owner 报的）
点「收起历史」（现在是侧栏那行 `#wsItem`；原先是标题行里的 `#wsFold` 图标，
2026-10-06 收敛成一个）时 body 挂上 `.folded`，而
    body.folded .nav-scroll, body.folded .hist-search { display:none }
会把侧栏里**唯一的 `flex:1`**（`.nav-scroll`）整个隐形 ⇒ 剩余空间没人认领
⇒ 底部账号行跟着「工作区」标题一路往上跑。
真实渲染实测（1280×704，Chrome/CDP 取 getBoundingClientRect）：
    展开   账号行底边 = 704（贴底）
    收起   账号行底边 = 249  ← 凭空上移 455px（客户端外观下 415px）
顶上那条 border-top 也悬在半空，看着就是"头像浮起来了"。

修法：`.nav-me{ margin-top:auto }` —— 与 `flex:1` **互补**而非重复：
展开时 nav-scroll 已吃满剩余空间、剩余为 0 ⇒ auto 归 0，零副作用。
⚠️ 刻意不选"折叠时别隐藏 nav-scroll"那条路 —— 那等于列表没被收起。

⚠️⚠️ 本文件所有断言都**先剥 CSS 注释再找**。
理由：`margin-top:auto` 这句话就写在 `.nav-me` 的注释里（解释为什么需要它），
不剥注释 ⇒ 守卫在注释里找到关键字 ⇒ **假绿**。
文件末尾两条"故意写坏"的用例专门证明这一步不是多余的。

渲染层的真值（1px 级、截图看不出来的那种）由 tools/check_sidebar_layout.js 负责，
本文件只钉住"那句话还在"。两者互补：源码级保证不被手滑删掉，渲染级保证真的生效。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "static" / "app.css"
DESKTOP_STATIC = ROOT / "desktop" / "static"


def strip_css_comments(s: str) -> str:
    """去掉 CSS 注释。

    ⚠️ 必须先剥再断言：注释里提到某个写法，会让源码级守卫误判成"写了"。
       CSS 只有 `/* */` 一种注释，所以不存在 JS 那种"两个都剥、顺序反了会互相吞"
       的问题；这里用非贪婪 + DOTALL，避免注释里嵌套的 `*` 把后面整段吞掉。
    """
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def nav_me_block(css: str) -> str:
    """取出 `.nav-me{...}` 这条规则的内容（已假定传入的是剥过注释的 CSS）。

    `[^}]*` 足够：这条规则里没有嵌套的括号。
    ⚠️ 不会误匹配 `.nav-me:hover{...}` 或 `.nav-me .ava{...}` ——
       `.nav-me` 后面必须紧跟空白再到 `{`，而那两个后面跟的是 `:` 或 ` .`。
    """
    m = re.search(r"\.nav-me\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def has_pin(block: str) -> bool:
    """规则内容里有没有那句「钉住底部」的 margin-top:auto。"""
    return bool(re.search(r"margin-top\s*:\s*auto", block))


def folded_hide_rule(css: str) -> tuple[str, str]:
    """挑出折叠态里**真正负责隐藏**的那条 `body.folded …{display:none}`。

    ⚠️ 别用 `re.search` 抓第一条 `body.folded`：折叠态下还有别的规则
    （2026-10-06 起有 `.ws-item .arw` 的箭头转向），抓到那条会得出
    "折叠把列表藏了/没藏"的**假判**。判据要落在"哪条规则在隐藏东西"上。
    """
    for sel, blk in re.findall(r"body\.folded\s+([^{]*)\{([^}]*)\}", css):
        if re.search(r"display\s*:\s*none", blk):
            return sel, blk
    return "", ""


class TestNavMePinned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = CSS_PATH.read_text(encoding="utf-8")
        cls.css = strip_css_comments(cls.raw)

    # ---------- 核心：那句话还在 ----------

    def test_auto_margin_is_present(self):
        blk = nav_me_block(self.css)
        self.assertTrue(
            has_pin(blk),
            "static/app.css 的 .nav-me 里没有 margin-top:auto —— "
            "收起历史时底部账号行（头像）会跟着往上跑",
        )

    def test_no_zero_margin_override(self):
        """有人可能"顺手"用 margin-top:0 覆盖掉它，同样是回归。"""
        blk = nav_me_block(self.css)
        self.assertIsNone(
            re.search(r"margin-top\s*:\s*0", blk),
            ".nav-me 里出现了 margin-top:0，会抵消钉住底部的 auto",
        )

    def test_nav_me_does_not_shrink(self):
        """窗口很矮时底部行也不许被压扁（同源的"没被保护"问题）。"""
        blk = nav_me_block(self.css)
        self.assertRegex(blk, r"flex\s*:\s*none")

    # ---------- 前提：为什么需要 auto ----------

    def test_nav_is_a_flex_column(self):
        """前提 1：侧栏是纵向 flex —— 否则 auto margin 无意义。"""
        m = re.search(r"\.nav\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, "找不到 .nav 规则")
        blk = m.group(1)
        self.assertRegex(blk, r"display\s*:\s*flex")
        self.assertRegex(blk, r"flex-direction\s*:\s*column")

    def test_folded_hides_the_only_flex_grow_child(self):
        """前提 2：`.folded` 确实把 `flex:1` 的那个滚动区隐形了。

        这两条前提一起成立，才是"必须在 .nav-me 自己身上钉住"的完整理由。
        哪天折叠方式改了（比如改成给 nav-scroll 加 height:0），
        这条会红 —— 那时请连同注释里的解释一起更新。
        """
        sel, blk = folded_hide_rule(self.css)
        self.assertNotEqual(sel, "", "找不到折叠态里负责隐藏的 body.folded 规则")
        self.assertIn(".nav-scroll", sel, "折叠态不再隐藏 .nav-scroll 了，请复核注释里的解释")
        self.assertRegex(blk, r"display\s*:\s*none")
        # 账号行本身绝不能被折叠态藏掉 —— 那样头像会"消失"，是另一种坏
        self.assertNotIn(".nav-me", sel, "折叠态把账号行一起藏了")

    def test_nav_scroll_is_the_flex_grow_element(self):
        m = re.search(r"\.nav-scroll\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, "找不到 .nav-scroll 规则")
        self.assertRegex(m.group(1), r"flex\s*:\s*1")

    # ---------- 红队：证明守卫真的会红 ----------

    def test_comment_only_auto_would_be_a_false_pass(self):
        """不剥注释就会假绿 —— 这是"必须先剥注释"这一步的证明。

        构造一份"规则里没有、只有注释里提到过 margin-top:auto"的 CSS：
          不剥注释 ⇒ 误判为已钉住（假绿）
          剥了注释 ⇒ 正确判为没钉住
        本文件真实使用的 `static/app.css` 恰好就是这种形状（注释里解释了
        为什么要 auto），所以不剥注释的话，上面第一条断言会永远通过、
        哪怕有人把真正的那行删了。
        """
        fake = ".nav-me{\n  /* 早期版本这里写过 margin-top:auto，后来被删了 */\n  display:flex;\n}"
        self.assertTrue(has_pin(fake), "前提不成立：假样本里注释没有让断言误判")
        self.assertFalse(has_pin(nav_me_block(strip_css_comments(fake))))

    def test_removing_the_line_would_be_caught(self):
        """把真正那行删掉，守卫必须报红（复用上面被测的同一个函数）。"""
        broken = re.sub(
            r"(\.nav-me\s*\{)([^}]*)\}",
            lambda m: m.group(1) + m.group(2).replace("margin-top:auto;", "") + "}",
            self.css,
            count=1,
        )
        self.assertNotEqual(broken, self.css, "前提不成立：没删掉任何东西")
        self.assertFalse(has_pin(nav_me_block(broken)), "守卫漏掉了被删掉 auto 的版本")


class TestDesktopStaticInSync(unittest.TestCase):
    """⚠️ 界面是**打包进客户端**的（desktop/main.js 用 protocol.handle 接管 http，
    只有 /api/* 走网络）⇒ 改了 static/ 却忘了 `npm run sync`（= node sync-static.js），
    打出来的包就还是旧界面，而且**没有任何报错**。

    这正是本次"头像浮起来"传到客户端的路径。目录是构建产物（不在 git 里），
    所以只在它存在时校验 —— 换台机器 clone 下来会 skip。
    """

    def test_desktop_copy_matches_repo(self):
        if not DESKTOP_STATIC.is_dir():
            self.skipTest("desktop/static 不存在（未跑过 sync），跳过")
        for name in ("app.css", "app.js", "index.html"):
            src = (ROOT / "static" / name).read_bytes()
            dst = (DESKTOP_STATIC / name).read_bytes()
            self.assertEqual(
                src,
                dst,
                f"desktop/static/{name} 与仓库 static/{name} 不一致 —— "
                "打包前请先 `cd desktop && npm run sync`，否则客户端还是旧界面",
            )


if __name__ == "__main__":
    unittest.main()
