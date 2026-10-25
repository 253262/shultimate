# -*- coding: utf-8 -*-
"""Owner 令牌（人工裁定库写入凭证）的可用性守卫。

来历：维护者说「人工裁定库的令牌没给我，帮我配置吧」。去服务器查了才发现——
**服务器侧上一轮就已经配好了**（403 文案是「令牌不对」而非「服务器没有配置」，
前者只在 `config.CURATED_TOKEN` 非空时才会出现），真令牌加「写入 → 读回 →
删除」往返也完全正常。所以缺的不是配置，是两件用户侧的体验：

1. ⭐ **删除裁定在令牌不对时静默失败**（真缺陷，本轮修）
   `del.addEventListener("click", async () => { ... await post(...) })`
   整段没有 try/catch。令牌不对 ⇒ post() 抛错 ⇒ 未处理的 Promise rejection
   ⇒ confirm 弹窗过了，然后**什么都没发生**：条目还在、按钮无响应，
   用户以为软件卡了。而删除恰恰是最需要令牌的操作。
   ⚠️ 这与本轮已修的 6 个缺陷是**同一类**（静默失败），漏在按钮回调里。

2. **令牌填完无法确认对错**（本轮补）
   此前只能靠"新增一条 → 被拒"才知道填错了，于是变成
   「填错 → 试一次 → 被拒 → 再猜」的循环。新增 `action:"ping"`：
   **只校验令牌、绝不碰任何数据**。

⚠️ 本文件只测源码结构（零 API、可离线跑）；真实往返验证走线上 curl，
   记录在 AGENTS.md 本轮条目。

照抄本目录的三个已踩过的坑（见 test_audit_2026_10_09.py 模块 docstring）：
剥注释再断言代码、钉使用点不钉声明点、断言窗口掐到逻辑块结束。
以及第四个：**断言注释内容必须用原文**，不能用 *_SRC。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SERVER = (ROOT / "app" / "server.py").read_text(encoding="utf-8")


def strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", " ", src)
    return src


def strip_py_comments(src: str) -> str:
    return re.sub(r"(?m)#.*$", " ", src)


APP_JS_SRC = strip_js_comments(APP_JS)
SERVER_SRC = strip_py_comments(SERVER)


def slice_by_marker(src: str, marker: str, until: str) -> str:
    """按"起止锚点字符串"切一段源码。

    ⚠️ 为什么不用正则找函数边界：剥注释后收尾锚点的缩进/空行一变就失配，
    而 None 会被报成「找不到」——真假红难分（test_audit_2026_10_09.py 踩过）。
    用两个**中文/字面锚点**切，结果确定且可读。
    找不到起锚点 = 抛 AssertionError（此时才是真的"这段被改了"）。
    """
    i = src.find(marker)
    if i < 0:
        raise AssertionError(f"找不到起锚点：{marker!r}")
    j = src.find(until, i + len(marker))
    if j < 0:
        raise AssertionError(f"找不到收锚点：{until!r}")
    return src[i:j]


class TestDeleteNoSilentFailure(unittest.TestCase):
    """一、删除裁定必须有 try/catch（否则令牌错误 = 静默无反应）"""

    def _delete_handler(self) -> str:
        body = slice_by_marker(
            APP_JS_SRC,
            'del.addEventListener("click"',
            'acts.appendChild(test)',
        )
        self.assertIn('action: "delete"', body,
                      "删除按钮必须调 /api/curated 的 delete")
        return body

    def test_delete_handler_has_try_catch(self):
        """整段 await 必须被 try 包住。"""
        body = self._delete_handler()
        self.assertIn("try {", body,
                      "删除按钮的 await 没有 try/catch ⇒ 令牌不对时"
                      "confirm 弹完什么都没发生，用户以为软件卡了")
        self.assertIn("} catch", body,
                      "删除按钮缺 catch 分支")

    def test_delete_failure_is_shown_to_user(self):
        """失败必须**说给用户听**，且允许重试（不能把按钮永久废掉）。"""
        body = self._delete_handler()
        idx_await = body.find('await post("/api/curated"')
        idx_catch = body.find("} catch")
        self.assertGreater(idx_catch, idx_await,
                           "catch 必须在 await 之后（否则捕不到）")
        catch = body[idx_catch:]
        self.assertRegex(catch, r"toast\(|textContent\s*=",
                         "删除失败必须提示用户，不能静默")
        self.assertIn("重试", catch,
                      "失败后按钮要能重试，不能永久变成不可点的死按钮")
        self.assertIn("disabled", catch,
                      "请求在飞时要禁用按钮，防连点重复提交")

    def test_success_path_still_repaints(self):
        """⚠️ 反向守卫：加 try/catch 很容易顺手把重画删掉。

        原来就踩过一次（loadCurated 不重画 ⇒ 服务端已删、卡片还在）。
        有 try 之后必须确认成功路径仍然 paint()。"""
        body = self._delete_handler()
        ok_seg = body[:body.find("} catch")]
        self.assertIn("await loadCurated(true)", ok_seg)
        self.assertIn("paint()", ok_seg,
                      "删除成功后必须重画，否则卡片留在屏幕上，"
                      "用户会以为删除失败（还能再点一次「试问一次」）")


class TestTokenPing(unittest.TestCase):
    """二、令牌自检：ping 必须只校验、不写数据"""

    def _ping_branch(self) -> str:
        return slice_by_marker(
            SERVER_SRC,
            'if action == "ping":',
            'if action == "delete":',
        )

    def test_ping_exists_and_reuses_owner_auth(self):
        body = self._ping_branch()
        self.assertIn("if not ok_owner:", body,
                      "ping 必须走和其他写操作同一套 ok_owner 鉴权，"
                      "不能另开一条判定")
        self.assertIn("_write_denied_reason()", body,
                      "拒绝时必须复用同一句可操作提示，"
                      "别让 Owner 看到另一种含糊说法")

    def test_ping_auth_precedes_success_return(self):
        """⚠️ 红队实测发现的漏洞：光断言「段里出现过 ok_owner」是不够的。

        把 ping 改成
            if action == "ping":
                return {"ok": True, ...}, 200      # ← 鉴权被跳过
            if action == "ping2":
                if not ok_owner: ...
        上面那条守卫仍会绿 —— 因为"段里"确实有 ok_owner，只是它在
        一个**永远不会走到**的分支里。这就是"钉声明点不钉使用点"的
        同一个坑在顺序上的变体：**存在 ≠ 生效**。

        所以必须断言**顺序**：鉴权必须在成功返回之前执行完。
        """
        body = self._ping_branch()
        idx_auth = body.find("if not ok_owner:")
        idx_ok = body.find('"token_ok": True')
        self.assertGreater(idx_auth, -1, "ping 段里没有鉴权")
        self.assertGreater(idx_ok, -1, "ping 段里没有成功返回")
        self.assertLess(idx_auth, idx_ok,
                        "鉴权必须排在成功返回**之前** —— "
                        "反了就等于任何令牌都能通过 ping，"
                        "而 ping 的结果 Owner 会当成「令牌正确」")

    def test_ping_never_writes_data(self):
        """⚠️ 这是本守卫最重要的一条：ping 一旦碰了数据，
        「点一下确认令牌」就变成隐形写操作 —— 而这个按钮
        正是主人随手会点的东西。"""
        body = self._ping_branch()
        for forbidden in ("curated_mod.add", "curated_mod.delete",
                          "curated_mod.stats"):
            self.assertNotIn(forbidden, body,
                             f"ping 绝不能调用 {forbidden} —— "
                             "令牌自检必须是纯只读")

    def test_ping_returns_token_ok(self):
        self.assertIn("token_ok", self._ping_branch(),
                      "成功响应要明确带 token_ok，前端才不用猜")


class TestTokenUI(unittest.TestCase):
    """三、前端：填完令牌要自动自检，且去抖"""

    def _verify_fn(self) -> str:
        return slice_by_marker(
            APP_JS_SRC,
            "const verifyToken = async () => {",
            'tok.addEventListener("change"',
        )

    def test_change_triggers_verify(self):
        body = slice_by_marker(
            APP_JS_SRC,
            'tok.addEventListener("change"',
            "const paint = () => {",
        )
        self.assertIn("verifyToken", body,
                      "令牌框 change 必须触发自检")
        self.assertIn("setTimeout", body,
                      "⚠️ 必须去抖：不去抖则逐字输入时每敲一个字符"
                      "就打一次请求")

    def test_verify_calls_ping_and_toasts_result(self):
        body = self._verify_fn()
        self.assertIn('action: "ping"', body,
                      "自检必须走 ping（纯只读），不能用新增来试")
        self.assertIn("toast(", body,
                      "自检结果必须立刻告诉用户对不对")

    def test_empty_token_clears_it(self):
        """清空输入框 = 退出 Owner 模式，要真的清掉存储里的令牌，
        否则下次打开还带着一个用户以为已删除的旧令牌。"""
        body = self._verify_fn()
        self.assertRegex(body, r"if \(!v\)",
                         "空令牌要走单独分支")
        self.assertRegex(body, r'setOwnerToken\(""\)',
                         "清空输入框必须清掉已存的令牌")


if __name__ == "__main__":
    unittest.main()
