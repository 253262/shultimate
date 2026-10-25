# -*- coding: utf-8 -*-
"""2026-10-09 审计新增的守卫测试。

来历：维护者一句「还有什么没做全部都开始」⇒ 派 agent 做了一次全量代码审计，
逐条**本地复现或代码核实**后确认的 6 个缺陷（另有 4 条审计报告里的候选经
核实是误报/有意为之，见 AGENTS.md 本轮记录）。

每个用例对应一个**实测确认过**的缺陷，不是"我觉得应该这样"。
凡是把修法回退，本文件必须报红 —— 这是 AGENTS.md 第 8 节「守卫测试是活规矩」。

⚠️ 本文件只测**源码结构与纯函数**（零 API、可离线跑）；
   涉及真实网络/浏览器行为的另见 tools/ 下的探针。

⚠️⚠️ 写守卫的三个已踩过的坑（照着做，别重开）：
   ① 必须**剥注释**再断言，否则注释里写着被禁的旧代码 ⇒ 静默假绿。
      上面 strip_js_comments() / strip_py_comments() 的剥除顺序是钉死的
      （先块后行，反了会互相误伤）。
   ② 要钉**使用点**不是声明点。钉声明点只能证明"它存在"，
      证明不了"它被用对了地方"。
   ③ 断言窗口必须**掐到下一个逻辑块结束**，不能图省事取固定字符数 ——
      取太大就会把无关代码圈进来（实测栽过）。
"""
import re
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SERVER = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
CURATED = (ROOT / "app" / "curated.py").read_text(encoding="utf-8")
RETRIEVER = (ROOT / "app" / "retriever.py").read_text(encoding="utf-8")
WEBLOOKUP = (ROOT / "app" / "weblookup.py").read_text(encoding="utf-8")


def strip_js_comments(src: str) -> str:
    """剥掉 // 与 /* */ 注释。⚠️ 顺序不能反（先块后行），理由见模块 docstring。"""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", " ", src)
    return src


def strip_py_comments(src: str) -> str:
    """剥掉 # 行注释（不处理文档字符串，但本文件断言的点都不在文档串里）。

    ⚠️ 只剥行注释：Python 没有块注释。文档字符串里出现被禁字符串是可能的，
    所以断言时应另配合"上下文窗口"缩小范围，双保险。"""
    return re.sub(r"(?m)#.*$", " ", src)


APP_JS_SRC = strip_js_comments(APP_JS)
SERVER_SRC = strip_py_comments(SERVER)
CONFIG_SRC = strip_py_comments(CONFIG)
CURATED_SRC = strip_py_comments(CURATED)
RETRIEVER_SRC = strip_py_comments(RETRIEVER)

# ⚠️⚠️ **断言"注释写了什么"时必须用未剥离的原文**（2026-10-09 实测假红 6 条）。
# 上面那批 *_SRC 全都剥了注释 —— 用它们去断言注释内容 ⇒ 断言对象根本不存在。
# 这正是本项目记过多次的「守卫自己绊红」，所以这里把两套来源显式分开：
#   · 断言**代码行为** → 用 *_SRC（剥注释，防"注释里写着被禁代码"造成假绿）
#   · 断言**注释是否诚实** → 用原文（CONFIG / SERVER 本身）
# 另注：Python 的 # 分隔注释（如 `# ---------- 族内"平局打破" ----------`）
# 也被 strip_py_comments 剥掉了，所以定位必须改用注释里的**中文关键词**做锚。


def slice_func(src: str, name: str) -> str:
    """按**行号**切出一个函数的源码，不用正则找边界。

    ⚠️ 2026-10-09 实测踩坑：用 `re.search(r"def f\\(.*?\\n(?=    def )")` 找函数
    边界，剥注释后收尾锚点经常匹配不上（缩进/空行一变就 None），
    而 **None 会被 assertIsNotNone 报成「找不到函数」——真假红难分**。
    改成按行切：从 def 行到下一个同级 def，结果确定、不依赖正则锚点。
    找不到时抛 AssertionError（此时才是真的「函数没了」）。
    """
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        # 允许缩进（_send_update 是 BaseHTTPRequestHandler 的方法，缩进 4）
        if ln.lstrip().startswith(f"def {name}("):
            start = i
            break
    if start is None:
        raise AssertionError(f"源码里找不到函数 {name}（真·缺失，不是锚点问题）")
    indent = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            continue
        cur = len(ln) - len(ln.lstrip())
        if cur <= indent and ln.lstrip().startswith(("def ", "class ")):
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


# ══════════════════════════════════════════════════════════════════════
# 一、离题时仍摆出 14 条不相关条款（本轮最伤用户的一条）
# ══════════════════════════════════════════════════════════════════════
class TestNotFoundNoCitations(unittest.TestCase):
    """用户问「附近有什么好吃的」，系统回答「我只回答飞盘规则的问题」，
    下面却齐刷刷摆着 **14 条**飞盘精神条款芯片，右栏还写「本次翻出 14 条」。
    **线上实测复现过**（0.2.14 版本的真实返回）。

    为什么这比不回答坏得多：用户会认为系统在敷衍他，或者更糟 ——
    以为这 14 条一定和他的问题有关。generator.py:616 的模块注释自己
    就写明了「完全离题时列条款是错的」⇒ 那是注释与实现脱节，不是设计意图。

    ⚠️ 域内但查不到条款（domain_word 为真）**照旧要摆** ——
       那种情况摆出来是有用的（"你是不是想问这些"）。所以守卫必须同时
       钉住两个方向，只钉一个等于没钉。"""

    def test_not_found_branch_clears_hits_when_out_of_domain(self):
        # 钉使用点：not_found 分支里必须有「domain_word 为假则清空 hits」
        m = re.search(
            r'if not res\.get\("domain_word"\):\s*\n\s*hits = \[\]',
            SERVER_SRC,
        )
        self.assertIsNotNone(
            m,
            "not_found 分支必须清空 hits：完全离题时摆条款是在明示用户"
            "「这些条款跟你无关」（generator.py 模块注释原话）",
        )

    def test_citations_built_from_hits_not_raw_result(self):
        """citations 必须由**已被清空的** hits 构建。

        ⚠️ 这是上一条的**必要配套**：如果 citations 改从 res["hits"] 取，
        清空 hits 就完全失效 ⇒ 静默假绿。"""
        self.assertIn('"citations": [_cite(h) for h in hits]', SERVER_SRC,
                      "citations 必须由局部变量 hits 构建，"
                      "否则清空 hits 对它无效")

    def test_frontend_already_handles_empty_citations(self):
        """前端对空数组**已有**正确行为（这是本修复不需要改前端的原因）。

        citesRow 空数组返回 null（不渲染任何芯片）；
        paintSources 显示「这次回答没有引用条款原文」。
        钉住它 = 钉住「后端置空就够了，前端不会显示怪东西」。"""
        fn = re.search(r"function citesRow\(cites\) \{.*?\n\}", APP_JS_SRC, flags=re.S)
        self.assertIsNotNone(fn, "找不到 citesRow")
        self.assertIn("if (!cites || !cites.length) return null;",
                      fn.group(0),
                      "citesRow 必须在空数组时返回 null，否则清空 citations "
                      "会渲染出空壳容器")

    def test_domestic_offdomain_contrast_still_returns_citations(self):
        """反向钉：正常回答（confident=True）路径**不得**被误清空。

        只钉"清空"不钉"不该清空"，就会出现"全都返回 0 条引用"的过度修复，
        而这在测试里表现为绿灯 —— 必须用这个用例把它钉死。"""
        # 清空语句必须在 not 分支里（缩进 12 空格 = if 体内）
        cleared = re.search(r'\n {12}hits = \[\]', SERVER_SRC)
        self.assertIsNotNone(cleared,
                             "清空语句缩进必须是 if 体内（12 空格），"
                             "若在 8 空格就说明它跑到了 if 外面 ⇒ "
                             "正常回答也会丢掉全部引用")


# ══════════════════════════════════════════════════════════════════════
# 二、500 响应把内部异常原文回给公网访问者
# ══════════════════════════════════════════════════════════════════════
class TestNoInternalErrorLeak(unittest.TestCase):
    """实测复现：智谱 embedding 欠费时，公网访问者能直接读到
    「服务内部错误：EmbedError HTTP 401 insufficient balance」
    ⇒ 泄露了「用哪家服务 / 账户欠费 / 上游错误码」，而服务监听公网 8000。

    本文件其他地方所有错误分支都是精心设计的中文话术，唯独这里裸奔。"""

    def test_detail_does_not_interpolate_exception(self):
        self.assertNotRegex(
            SERVER_SRC,
            r'"detail"\s*:\s*f"[^"]*\{exc\}',
            "禁止把 str(exc) 回给客户端 —— 它会泄露上游服务厂商、"
            "账户欠费状态和错误码。异常原文只进日志。",
        )

    def test_traceback_still_logged(self):
        """钉住反向：日志**必须还在**。别为了脱敏把排障线索也删了。"""
        fn = re.search(r"except Exception:  # noqa: BLE001\s*\n"
                       r"\s*traceback\.print_exc\(\).*?"
                       r'self\._send_json\(\{"detail": "服务暂时不可用[^"]*"\}, 500\)',
                       SERVER, flags=re.S)
        self.assertIsNotNone(fn,
                             "必须保留 traceback.print_exc()（排障靠它）"
                             "并把客户端响应换成固定中文文案")


# ══════════════════════════════════════════════════════════════════════
# 三、MIN_SCORE 是死参数，却被当成真实闸门展示给 owner
# ══════════════════════════════════════════════════════════════════════
class TestMinScoreIsNotARealGate(unittest.TestCase):
    """全仓 grep：MIN_SCORE 只有 4 处 —— 定义、retriever 回传、
    设置页文案、一条 `assertGreater(>0)`。**从未参与任何判定**。

    真实闸门是 retriever.py:274 的
    `confident = structured or hinted_exact or domain_word`
    （类目 / 线索词 / 域内词），一个字都没提 MIN_SCORE。
    而且 retriever.py:253 已实测证伪余弦有区分度：
    域内最低 0.344 vs 离题最低 0.350，**完全重叠**。

    为什么必须改：server.py 的设置页把它标成
    「相关性闸门…这是『不确定就说不确定』的执行点」，
    前端又把它当「闸门阈值」显示在「最高分」旁边
    ⇒ owner 会据此推论「闸门漏了」并去调 MIN_SCORE，
    而**调它对系统行为零影响**。这违反 AGENTS.md「依据可核查」红线：
    给用户看一个假的判定依据，比不给看更坏。"""

    def test_retriever_no_longer_echoes_min_score_as_threshold(self):
        self.assertNotRegex(
            RETRIEVER_SRC,
            r'"threshold"\s*:\s*config\.MIN_SCORE',
            "retriever 不能把 MIN_SCORE 当 threshold 回传 —— "
            "前端会据此渲染「闸门阈值」并让 owner 做错误推论",
        )

    def test_settings_page_does_not_claim_it_is_the_gate(self):
        fn = re.search(r'def _settings\(\).*?\n(?=def )', SERVER_SRC, flags=re.S)
        self.assertIsNotNone(fn, "找不到 _settings")
        body = fn.group(0)
        seg = re.search(r'\{"key": "MIN_SCORE".*?\}', body, flags=re.S)
        self.assertIsNotNone(seg, "MIN_SCORE 条目应仍保留（让 owner 找得到它）")
        self.assertNotIn('"name": "相关性闸门"', seg.group(0),
                         "不能再叫「相关性闸门」—— 它不是闸门")
        self.assertIn("未接入", seg.group(0),
                      "必须明说这个参数没接线，否则 owner 会去调它")

    def test_frontend_no_longer_renders_fake_threshold(self):
        fn = re.search(r"function paintTest\(box, d\) \{.*?\n\}", APP_JS_SRC, flags=re.S)
        self.assertIsNotNone(fn, "找不到 paintTest")
        self.assertNotIn("闸门阈值", fn.group(0),
                         "前端不能显示「闸门阈值」—— 那是个不存在的判据")
        # 反向：最高分仍要留着（它是真实存在的参考量）
        self.assertIn("最高分", fn.group(0))


# ══════════════════════════════════════════════════════════════════════
# 四、Range 后缀区间静默返回错数据
# ══════════════════════════════════════════════════════════════════════
class TestRangeSuffixRejected(unittest.TestCase):
    """`bytes=-500` 是 HTTP 规范的**后缀区间**（"最后 500 字节"）。
    原代码 `start = int(a) if a else 0` ⇒ a 为空时算出 start=0, end=500
    ⇒ **返回文件开头 501 字节**，而且 Content-Range 是合法的
    `bytes 0-500/<size>`，**客户端无法察觉**。

    用户会拿到一个"开头 501 字节的安装包"；sha512 会拦住（不至于装坏包），
    但看到的是一句莫名其妙的"校验未通过"，而真因是客户端发了服务端
    没实现的 Range 形态。

    ⚠️ 当前 desktop/updater.js 用的是 `bytes=<start>-`，碰不到这里。
    这条守卫防的是「代理改写 Range」或「将来改用后缀区间」。"""

    def test_suffix_range_is_rejected_not_silently_mapped(self):
        body = slice_func(SERVER_SRC, "_send_file")   # ⚠️ 是 _send_file 不是 _send_update：
        # 后者只是调用方（校验路径后转发），Range 解析在前者里。
        # 锚点用代码本身：`a, _, b = spec.partition("-")` 后面那条
        # `if not a:` 是本次修复新增的拒绝分支。
        seg = re.search(r'a, _, b = spec\.partition\("-"\).*?'
                        r'start = int\(a\)', body, flags=re.S)
        self.assertIsNotNone(seg, "找不到 Range 解析点")
        window = body[seg.start():seg.start() + 700]
        self.assertRegex(window, r'if not a:',
                         "`bytes=-500` 必须显式拒绝（416）。"
                         "原来 `int(a) if a else 0` 会静默返回开头 501 字节，"
                         "且响应头合法、客户端察觉不到")
        # 反向：合法的 `bytes=<start>-` 形态必须仍然走 int(a) 正常路径
        self.assertIn("start = int(a)", window,
                      "合法的 `bytes=12345-` 必须仍然被正常解析")


# ══════════════════════════════════════════════════════════════════════
# 五、人工裁定库并发写入会静默丢数据
# ══════════════════════════════════════════════════════════════════════
class TestCuratedWriteLock(unittest.TestCase):
    """server 用 ThreadingHTTPServer（**每请求一线程**），而：
      · add()    是独立的 open("a") append —— 各线程各持一个文件偏移
      · delete() 是**无锁的读-改-写**：_load() 读全量 → 重写全量 → replace()

    两个真实丢数据的时序：
      ① add 落在 delete 的「读」和「写」之间 ⇒ 新裁定被 replace 整个抹掉，
         界面上「保存裁定」返回成功、paint() 重画，**刷新后条目不见了**；
      ② 两次 add 并发 ⇒ 两行交错 ⇒ 半条 JSON，而 _load() 对坏行是
         `continue` 静默跳过 ⇒ **这条裁定从此永不命中**，Owner 却以为它在。

    疑问库是本项目**唯一**能绕过检索+生成层、把文本当「人工核定」权威
    答案吐给用户的通道 ⇒ 这里的静默丢失是"看起来有依据、实际没有"。"""

    def test_lock_is_rlock_not_lock(self):
        """⚠️ 必须是 **RLock** 不是 Lock：delete() 持锁后还会调 _load()，
        而 _load() 自己也要取锁 ⇒ 普通 Lock 会**自锁死**（100% 死锁）。

        这是本条最容易踩的坑，已实测：换成 Lock 后 delete 立即 hang。"""
        self.assertRegex(CURATED_SRC, r"_LOCK = threading\.RLock\(\)",
                         "必须用 RLock —— delete() 持锁后调 _load()，"
                         "_load() 也要取锁，普通 Lock 会自锁死")
        self.assertNotRegex(CURATED_SRC, r"_LOCK = threading\.Lock\(\)",
                            "threading.Lock 会在 delete→_load 时自锁死")

    def test_load_takes_the_lock(self):
        """读也必须在锁内：否则可能读到 delete() replace 到一半的中间态。"""
        fn = re.search(r"def _load\(\).*?\n(?=def )", CURATED_SRC, flags=re.S)
        self.assertIsNotNone(fn, "找不到 _load")
        self.assertIn("with _LOCK:", fn.group(0),
                      "_load 必须在锁内 —— 否则可能读到 replace 的中间态，"
                      "而坏行会被 continue 静默跳过 ⇒ 静默少一条裁定")

    def test_delete_holds_lock_across_read_modify_write(self):
        """钉住 delete 的**整个** 读-改-写都在锁内 ——
        只锁 write_text 或只锁 replace 都没用，竞态窗口在 read 与 write 之间。"""
        fn = re.search(r"def delete\(.*?\n(?=\n\ndef |\n\n# ---)", CURATED_SRC, flags=re.S)
        self.assertIsNotNone(fn, "找不到 delete")
        body = fn.group(0)
        self.assertIn("with _LOCK:", body, "delete 必须持锁")
        # 三步都必须在锁内：用「with _LOCK:」到函数结束之间是否含三步来判
        idx_lock = body.find("with _LOCK:")
        seg = body[idx_lock:] if idx_lock >= 0 else ""
        for step, label in (("_load_locked()", "读"), ("write_text", "写"),
                            ("replace", "替换")):
            self.assertIn(step, seg,
                          f"delete 的「{label}」({step}) 必须在锁内")

    def test_add_takes_the_lock(self):
        fn = re.search(r"def add\(.*?\n(?=\ndef |\n\n# ---)", CURATED_SRC, flags=re.S)
        self.assertIsNotNone(fn, "找不到 add")
        body = fn.group(0)
        self.assertIn("with _LOCK:", body,
                      "add 的 append 也要在锁内 —— 并发写会交错出半条 JSON，"
                      "而坏行被静默跳过 ⇒ 该裁定从此永不命中")

    def test_lock_is_importable_and_usable(self):
        """真跑一遍：RLock 可重入 ⇒ delete 调 _load 不会 hang。"""
        import sys
        sys.path.insert(0, str(ROOT))
        from app import curated
        self.assertIsInstance(curated._LOCK, type(threading.RLock()))
        # 真实调用一次 delete（不存在的 id）—— 如果锁会自锁死，这里会 hang
        self.assertFalse(curated.delete("__guard_nonexistent_id__"))
        self.assertIsInstance(curated.all_items(), list)


# ══════════════════════════════════════════════════════════════════════
# 六、请求体解析失败被误报成"字段缺失"
# ══════════════════════════════════════════════════════════════════════
class TestBadBodyNotReportedAsMissingField(unittest.TestCase):
    """原来 `_read_body` 的 `except Exception: return {}` 把所有异常吞成空 dict，
    于是「请求体被截断 / 编码错」会被下游解读成
    「问题不能为空」「缺少原问题 q」。

    ⚠️ 这是**错误信息指向一个完全无辜的字段**：Owner 会反复检查自己
    明明没问题的提问文本，而真因在传输层。这是比报错更坏的失败 ——
    它把人往错误的方向带。"""

    def test_json_decode_error_has_its_own_message(self):
        body = slice_func(SERVER_SRC, "_read_body")
        self.assertIn("__bad_body__", body,
                      "畸形 JSON 必须有专属哨兵值，不能和正常空 dict 混同")
        self.assertIn("json.JSONDecodeError", body,
                      "必须单独捕获 JSONDecodeError，与其它异常分开")

    def test_bad_body_is_intercepted_before_handlers(self):
        """⚠️ 配套守卫：哨兵值必须**在路由分发前**被拦住。

        只在 _read_body 里塞哨兵而忘了在 do_POST 拦 ⇒ 哨兵会被当成正常
        body 传给 handle_ask，用户看到的又是「问题不能为空」，
        等于没修（且测试会假绿）。"""
        body = slice_func(SERVER_SRC, "do_POST")
        idx = body.find('if "__bad_body__" in body:')
        self.assertNotEqual(idx, -1, "do_POST 必须拦截 __bad_body__")
        idx_router = body.find('if route == "/api/ask"')
        self.assertGreater(idx_router, idx,
                           "__bad_body__ 的拦截必须在路由分发**之前**，"
                           "否则哨兵会被当正常 body 传下去")


# ══════════════════════════════════════════════════════════════════════
# 七、误导性注释：数据源头的谎言最危险
# ══════════════════════════════════════════════════════════════════════
class TestNoMisleadingComments(unittest.TestCase):
    """死代码本身不伤用户，但**注释描述的行为与代码不符**是维护期陷阱：
    下一个 agent（或半年后的 owner）会照着注释去理解、改动、调参。

    本项目栽过好几次：`tests/test_notes_rich_2026_10_07.py` 的模块 docstring
    就明确记着"注释里写着被禁的旧代码 ⇒ 静默假绿"。"""

    def test_route_tiebreak_marked_as_unwired(self):
        # ⚠️ 用**原文** CONFIG（不是 CONFIG_SRC）—— 这条断言的就是注释本身，
        # 而 CONFIG_SRC 恰恰把注释剥掉了。定位锚点用注释里的中文短语。
        seg = re.search(r'族内"平局打破".*?ROUTE_TIEBREAK = float', CONFIG, flags=re.S)
        self.assertIsNotNone(seg, "找不到 ROUTE_TIEBREAK 定义块")
        self.assertRegex(seg.group(0), r"从未接入|零引用",
                         "ROUTE_TIEBREAK 全仓零引用，"
                         "注释必须说清它没接线，别让人以为它在工作")

    def test_rrf_comment_matches_actual_fusion(self):
        """注释原文说「按名次融合」，但 retriever.py:201 实际是
        `cos = [max(pair) for pair in zip(*paths)]` —— **逐维取最大值**。
        RRF 已被实测否决（retriever.py:221-229 记录）。"""
        seg = re.search(r'多路融合（RRF）.*?FUSE_DEPTH = int', CONFIG, flags=re.S)
        self.assertIsNotNone(seg, "找不到 RRF 配置块")
        self.assertRegex(seg.group(0), r"已被实测否决|并非按名次融合",
                         "RRF 注释必须说清它已被否决、线上是取最大值")

    def test_weblookup_fixes_have_no_self_mapping(self):
        """`_FIXES` 曾有 4 条 a → a 的自映射，是从候选词表复制时忘剔的无效项。

        比"白跑几次 replace"更坏的是它们**伪装成"已处理的断字"** ——
        让人以为 to / of the / occurs 的断字问题已解决。"""
        fn = re.search(r"_FIXES: list\[tuple\[str, str\]\] = \[(.*?)\n\]",
                       WEBLOOKUP, flags=re.S)
        self.assertIsNotNone(fn, "找不到 _FIXES 列表")
        pairs = re.findall(r'\("([^"]+)",\s*"([^"]*)"\)', fn.group(1))
        self.assertTrue(pairs, "应至少保留几条有效映射")
        for a, b in pairs:
            self.assertNotEqual(a, b,
                                f"自映射 ({a!r} -> {b!r}) 是无效项："
                                "既不改任何东西，又伪装成「已处理的断字」")

    def test_weblookup_docstring_admits_clause_extraction_is_dead(self):
        """`_clauses_of` / `_pick` 零调用点，`lookup()` 实际走 `_windows()`，
        返回结构里根本没有条号。而模块 docstring 原来写的是
        「抽英文条款」—— 让人以为它在跑。"""
        doc = re.search(r'"""(.*?)"""', WEBLOOKUP, flags=re.S)
        self.assertIsNotNone(doc, "找不到模块 docstring")
        self.assertRegex(doc.group(1), r"死代码|零调用点",
                         "模块 docstring 必须承认「抽条款」这条路已放弃，"
                         "否则下个人会以为 _clauses_of 在跑")


if __name__ == "__main__":
    unittest.main()
