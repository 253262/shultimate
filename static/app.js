/* ==========================================================================
   飞盘规则助手 · 极简单栏
   信息架构（2026-10-04 第二次重构）：
     侧栏 = 品牌 / 新对话 / 助手 / 历史对话 / 头像。
     头像 = **全部其他功能的唯一入口**（规则原文、检索测试、疑问库、
     外观与阅读、帮助、关于）。侧栏不再有任何功能导航。
   为什么这么改：用户打开就该能提问。让他先在六个导航项里辨认
   「知识库是什么、检索测试有什么用」，等于要求用户先研究产品内部结构。
     纯 vanilla JS · 无框架 · 无 CDN · 无构建
   ========================================================================== */

/* ------------------------------ 常量 ------------------------------ */
/* 「直答 / 细节」的分隔标记 —— ⚠️ 必须与 app/generator.py 的 SYSTEM_PROMPT 一致。
   模型写出这个标记时，说明它后面那段是"想深看的人才需要"的依据与边界：
   默认不显示，用户点一下才展开（见 botNode / moreNode）。
   ⚠️ 2026-10-06 起**全站不再有"结论 / 依据 / 该怎么做"这类小节标题** ——
   原来这里有个 SECTIONS 数组专门把那些词渲染成加粗小标题，已整段删除：
   它和"答完就停"是对着干的（模型看到清单就会去填满）。
   ⚠️ 改这里要连同 generator.py 的提示词一起改，有守卫 test_answer_shape.py 钉着。 */
const MORE_RE = /<!--\s*more\s*-->/i;
/* 回答卡片上那个小徽章的文字。
   ⚠️ 全部是**用户能懂、且不夸大**的说法。早期写的是
   「大模型生成 / 规则原文检索 / Owner 裁定」—— 那是实现细节：
   用户看到只会想"这跟我有什么关系"，而且要先去搞懂什么叫"向量检索"。
   现在这一行只回答用户真正关心的那件事：**这条答案的依据是什么。**
   ⚠️ 「规则里没找到」不能省，也不能说成"服务异常" ——
   诚实是这个系统的第一卖点（见铁律：不确定就说不确定）。 */
const MODE_LABEL = {
  llm: "依据规则原文", retrieval_only: "只给原文",
  not_found: "规则里没找到", fallback_error: "只给原文",
  curated: "人工核定", chat: "日常对话",
  // ⚠️ 2026-10-15 补上两条**一直缺的**：
  //   `out_of_scope`（闸门拒答，早就有了）和 `beyond_rules`（本轮新增）。
  //   缺了会怎样：下面用的是 `MODE_LABEL[p.mode] || p.mode || ""`，
  //   查不到就**显示英文原文**（`out_of_scope`）—— 用户看到的是内部标识符。
  //   ⇒ **规矩：新增任何一种 mode，都要回来这里补一行。**
  out_of_scope: "不在范围", beyond_rules: "这个我答不了",
};
/* ------------------------------ 对话空态 ------------------------------
   ⭐ 这里**曾经**放过 12 条可点问题（场上争议 6 + 流程与恢复 6），
   后来全部删掉，只留输入框。删的理由不是"不重要"，是**它们在替用户做决定**：
     ① 玩飞盘的基本都知道一些规则，示例问题反而像在教用户"你该问什么"，
        暗示"你不会问" —— 一次微小的居高临下；
     ② 12 行列表让首屏看起来像个功能介绍页，而不是一个"等你提问"的框；
     ③ 真要举例，一句 placeholder 就够（"比如：读秒读太快了怎么办"）。
   ⚠️ 若要恢复任何首屏引导，先照这条量一遍：
   **空白的输入框 > 任何示例清单。** 界面是入口，不是说明书。 */

/* 最近一次全量评测（来源 docs/eval-report.md，2026-10-03）——
   故意放在概览页最显眼处：这个系统的第一卖点是"不确定就说不确定"，
   那就把它的准确率连同口径一起摊开，而不是只宣传能力。 */
const EVAL = {
  date: "2026-10-03",
  faq: { n: 447, wide: { 1: 65.1, 3: 88.4, 5: 95.1 }, strict: { 1: 53.2, 3: 79.9, 5: 88.6 } },
  accept: { n: 54, hit8: 100 },
  refuse: { n: 12, hit: 12 },
};

/* ------------------------------ 头像浮层内容 ------------------------------
   ⭐ 2026-10-05 收敛：头像从七项收到三项。
   之前是七项（规则检索/规则原文/人工裁定/召回测试/评测报告/使用指南/设置），
   看起来像"功能目录"，实际上是把设置页的东西又抄了一遍到浮层里 ——
   同一个功能两处都能点，导航就永远是分裂的（这条原则见文件末尾教训）。
   ⇒ 那些**配置项**全部下沉成设置里的分组目录，浮层只负责"我要出去"。
   ⭐ 2026-10-06 变四项：见下面紧挨着的说明（加的是「我的笔记」，它不是配置项）。 */

/* ⚠️ 2026-10-06 从三项变四项，理由写在这里免得下一轮被当成"又加回来了"：
   加的是「我的笔记」，而它不是**配置项**，所以不该下放进设置目录 ——
   设置里的东西是"我要改什么"，笔记是"我要看我的东西"。
   owner 这一轮的原话：「比如'笔记'功能，初衷就是想让用户能够记得自己问过什么问题，
   就像做收藏和笔记一样。」而在此之前，笔记**只有对话页右栏一个入口**，
   人一离开对话页就再也找不回来 —— 他都不知道有这个功能。
   ⇒ 「三项上限」这条规矩的**本意**是"别把设置项抄进浮层"（见上一版七项平铺），
     不是"数字必须是 3"。笔记不违反它的本意。
   ⚠️ 顺序按"用得上的程度"：笔记 / 反馈最常按，退出最后（最不可逆）。
   ⚠️ 一行一项，不加任何解释性副标题（owner 定）——
   副标题是说明书不是入口，会让人先读完再决定。 */
/* ⚠️ 2026-10-17「查规则」提到第一项（一级页 `#/rules`）。
   起因：owner 自己在设置里翻到「意见反馈」附近才看见它 ——
   **用户找不到的功能 = 不存在**（§11 那条已经踩过一次：笔记）。
   ⚠️ 它**不是配置项**（不是"我要改什么"），是"我要翻规则"，
      与「练规则」「我的笔记」同类，归属本来就是浮层。
   ⚠️ 提上来之后**设置目录里不再保留同一项** —— 两处都能点，导航就分裂
      （07 项平铺那次就是这个毛病，见本文件末尾教训）。
   ⚠️ 普通用户看到的入口总数没变：设置·常用 5 项 → 4 项，浮层 5 项 → 6 项。
      这不是"又堆入口"，是**搬家**。
   ⚠️ 当天晚些时候「意见反馈」下放到设置后，浮层回落到 **5 项**（见下）。 */
/* ⚠️ 2026-10-17（同一天，owner 要求）：「意见反馈」从浮层**下放进设置·常用**。
   它是"我要说句话 / 有事找维护者"，属于**支持入口**；
   而浮层现在承担的是"查规则 / 练规则 / 笔记"这些日常动作。
   ⚠️ **只搬不复制**：浮层里删掉、设置里新增 —— 两处都能点就又是导航分裂。
   ⚠️ 这条**改掉的是 2026-10-06 的旧决定**（当时把它留在头像、不进设置，
      理由是"它不是配置项"）。旧理由没错，但 owner 现在的需求覆盖它了；
      别再拿旧理由把它搬回来。 */
const ME_MENU = [
  { id: "rules",             name: "查规则" },
  { id: "replay",            name: "复盘" },
  { id: "practice",          name: "练规则" },
  { id: "notes",             name: "我的笔记" },
  { id: "settings",          name: "设置" },
  { id: "__signout",         name: "退出登录" },
];
/* 2026-10-20 加「复盘」：它是重要使用场景，不是产品唯一入口。
   2026-10-23 产品准则以 docs/product.md 为准；当前空 hash 实际进入 #/chat，
   默认入口待用户证据决定，不得从旧 ADR/注释反推当前行为。
   ⚠️ 上界从 5 → 6：这一项让盘友更容易回看和理解当时的情境，
   不是设置项、不是支持项。守卫 test_exact_items / test_common_group_is_not_bloated
   同步改过，别把它当成"又堆入口"。 */
/* ⚠️ 2026-10-12 加「练规则」（2026-10-17 起排第二），理由（免得下一轮被当成"又把入口堆回来了"）：
   它服务的是"看懂/记住"这一半。此前全部能力都是"你问我答"，
   而产品要降低理解门槛——"看懂一次"不等于"记住"（判据见 docs/product.md）。
   练习器后端（`GET/POST /api/practice`）当天已落地，这里补上界面。
   ⚠️ 放第一位而不是最后：它是**用户主动想做的事**（我想练练），
   不是配置项；"退出登录"永远最后（最不可逆）。
   ⚠️ 仍然一行一项、不加副标题（沿用上面那条规矩）。 */
/* ⚠️ 2026-10-06 从三项变四项，理由写在这里免得下一轮被当成"又加回来了"：
   加的是「我的笔记」，而它不是**配置项**，所以不该下放进设置目录 ——
   设置里的东西是"我要改什么"，笔记是"我要看我的东西"。
   owner 这一轮的原话：「比如'笔记'功能，初衷就是想让用户能够记得自己问过什么问题，
   就像做收藏和笔记一样。」而在此之前，笔记**只有对话页右栏一个入口**，
   人一离开对话页就再也找不回来 —— 他自己都没发现有这个功能。
   ⇒ 「三项上限」这条规矩的**本意**是"别把设置项抄进浮层"（见上一版七项平铺），
     不是"数字必须是 3"。笔记不违反它的本意。
   ⚠️ 顺序按"用得上的程度"：笔记最常按，退出最后（最不可逆）。 */

/* ------------------------------ 设置页分组目录 ------------------------------
   原来设置是一排 12 个 tab，横向排不下、换行后又和页面标题重复。
   改成 DeepSeek/系统设置那种"分组 + 目录项"：一屏看完全部入口，
   点进去才是具体页。分组标题用小字，是目录本身的结构，不是功能说明。

   ⚠️ 2026-10-06 重组为**两组**（owner 原话：「不要让用户看到那些给开发人员
   看的专业术语，用户看了只会觉得头疼」「视觉体验上不给用户添乱」）。
   之前是 12 项平铺三组，对一个"想弄懂昨天那场球"的队员来说，
   「检索参数」「语料来源」「召回测试」「评测报告」是**纯噪音**：
   他不知道该不该点，点进去也看不懂，只会觉得"这软件好像是给程序员用的"。
   ⇒ 拆成：
        · 常用        —— 四个队员真会用到的
        · 开发者选项  —— 维护知识库的人看的，**默认折叠**
   折叠不是删除：懂行的人（以及我）仍然一处不丢，只是不再摆在脸上。
   ⚠️ 12 个入口一个都没删 —— 守卫 test_all_old_entries_moved_into_settings
      就是钉这一点：收敛只能改**摆法**，不能顺手丢功能。 */
const SET_GROUPS = [
  {
    name: "常用",
    items: [
      { id: "settings/main",   name: "外观与阅读", desc: "字号、要不要显示条款相关度这些细节" },
      // ⚠️ 「查规则」2026-10-17 已升到头像浮层第一项（一级页 `#/rules`）。
      //    这里**刻意不再留同一项** —— 同一功能两处都能点，导航就分裂。
      { id: "settings/answer", name: "助手与回答", desc: "同一件事，换一种讲法（新手 / 速查 / 复盘）" },
      { id: "settings/help",   name: "使用指南",   desc: "三步上手、快捷键、常见疑问" },
      { id: "settings/feedback", name: "意见反馈", desc: "哪里不对、想要什么，都可以说" },
      { id: "settings/about",  name: "关于",       desc: "版本与语料来源" },
    ],
  },
  {
    name: "开发者选项",
    desc: "给维护这个知识库的人看的：语料、检索参数、评测数字。平时不用管。",
    dev: true,
    items: [
      { id: "settings/curated",   name: "人工裁定",   desc: "系统答不出的问题，由维护者补上正确答案" },
      { id: "settings/test",      name: "召回测试",   desc: "只测检索层能否找对条款，不生成回答" },
      { id: "settings/overview",  name: "评测报告",   desc: "447 个真实盘友问法的实测命中与拒答" },
      { id: "settings/model",     name: "模型与生成", desc: "回答由哪个模型写、参数是什么" },
      { id: "settings/retrieval", name: "检索参数",   desc: "命中与排序的具体做法" },
      { id: "settings/source",    name: "语料来源",   desc: "原文来源、切分方式、检索口径" },
    ],
  },
];
/* ⚠️ 「意见反馈」2026-10-17 起**放进设置·常用**（owner 要求）。
   以前它只挂在头像浮层，来回翻烧饼的理由都记在这儿：
     · 2026-10-06 的判断：它不是配置项（"我要说句话"）⇒ 留在浮层；
     · 2026-10-17 的判断：它是**支持入口**（有事找维护者）⇒ 归到设置更顺，
       而浮层留给"查规则 / 练规则 / 笔记"这些日常动作。
   ⚠️ 仍然是**只搬不复制**：浮层里删掉、设置里只留一份 ——
      同一功能两处都能点，导航就又分裂了（这条原则见文件末尾）。
   ⚠️ 守卫 test_desktop 的 `test_feedback_lives_in_settings_not_avatar` 钉住"只留一份"。 */

/* 外观偏好：存在 localStorage，改完立刻生效，不需要任何后端配合。
   ⭐ 存在的理由不是"功能多"，而是"术语是可以关掉的"——
   条款号、相关度、检索路径这些是给较真的人看的，
   但**默认必须是关的**：owner 2026-10-06 的原话是「不要让用户看到那些
   给开发人员看的专业术语，用户看了只会觉得头疼」。想核对的人自己打开就行。 */
const PREF_KEY = "frisbee.rag.pref";
const PREF = { size: "md", tech: false, dense: false, devnav: false,
  // 每轮练习几道题。⚠️ **暂时不做设置项**（owner 没提，加了就是多一个要解释的旋钮），
  // 但读的时候带默认值，将来要放进设置页直接改这里即可，不用动 viewPractice。
  practiceN: 10 };
function loadPref() {
  try { Object.assign(PREF, JSON.parse(localStorage.getItem(PREF_KEY) || "{}")); } catch (e) { }
  applyPref();
}
function savePref() { try { localStorage.setItem(PREF_KEY, JSON.stringify(PREF)); } catch (e) { } }
function applyPref() {
  document.body.dataset.size = PREF.size;      // md | lg
  document.body.dataset.tech = PREF.tech ? "1" : "0";
  document.body.dataset.dense = PREF.dense ? "1" : "0";
}

const PAGE_META = {
  replay:   ["复盘", "把刚才那一下，放回场上"],
  chat:     ["对话", "用大白话问，回答只依据 WFDF 规则原文"],
  practice: ["练规则", "做题、当场判对错 —— 看懂一次不等于记住"],
  rules:    ["查规则", "按章节系统翻，或记得大概意思就模糊查找"],
  notes:    ["我的笔记", "你摘下来的回答和随手记的东西，只存在这台设备上"],
  files:    ["数据文件", "规则原文由哪些文件构成"],
  settings: ["设置", "外观、模型与检索参数"],
};
/* 设置页的页内目录：分组 + 目录项（见 SET_GROUPS）。
   ⚠️ 原来这里是一排 12 个横向 tab，1280 宽的窗口里排不下、换行后又和
   页面标题重复，看起来像控制台而不像设置。
   现在改成系统设置那种"左列分组目录 / 右列内容"，一屏看完全部入口。 */
const SUB_META = {
  "settings/main":     ["外观与阅读", "字号、要不要显示条款相关度这些细节"],
  "settings/help":     ["使用指南", "三步上手、快捷键、常见疑问"],
  "settings/answer":   ["助手与回答", "同一件事，换一种讲法"],
  "settings/model":    ["模型与生成", "回答由哪个模型写、参数是什么"],
  "settings/retrieval":["检索参数", "命中与排序的具体做法"],
  "settings/source":   ["语料来源", "原文来源、切分方式、检索口径"],
  "settings/about":    ["关于", "版本、语料核验与运行信息"],
  "settings/rules":    ["查规则", "按章节系统翻，或记得大概意思就模糊查找"],
  // ⚠️ 旧 hash 的标题**故意保留**：老链接/老书签还能进来（viewSettings 里会转到合并页）。
  //   别当成"漏删"清理掉，否则老笔记里的链接会变成空白页。
  "settings/kb":       ["查规则 · 按章节翻", "按章节浏览 368 条规则、附录与术语定义"],
  "settings/search":   ["查规则 · 模糊查找", "输入大白话，检索最相关的条款"],
  "settings/curated":  ["人工裁定", "系统答不出的问题，由维护者补上正确答案"],
  "settings/test":     ["召回测试", "输入问题，查看会召回哪几条条款"],
  "settings/overview": ["评测报告", "447 个真实盘友问法的实测数字"],
  "settings/feedback": ["意见反馈", "哪里不对、想要什么，都可以说"],
};

/* ------------------------------ 状态 ------------------------------ */
const AS = {  // 全局应用状态
  stats: null, settings: null, chapters: null, files: null, assistants: [], assistantId: "standard",
};
const CH = {  // 对话页状态
  sessions: [], current: null, active: null, busy: false, ctrl: null, stick: true, queried: false,
  histQ: "", sortAsc: false, storageBroken: false, lastRaw: null, conflict: false,
};
/* `ruleTab` = 「查规则」页当前停在哪个 tab（2026-10-10 合并页用）。
   ⚠️ 默认值走 `viewRules()` 里的 `KB.ruleTab || "browse"`，不写在这里 ——
      这样"首次默认模糊查找"这条判据集中在一处、守卫也好钉。 */
const KB = { chapter: null, ruleTab: null };
const SEARCH = { q: "", res: null, seq: 0 };
/* 复盘页状态（2026-10-20）。
   ⚠️ 必须声明在**这里**，不能放在文件末尾的复盘代码块里：
   bootUI() 在文件末尾之前就调用 routeNow() → viewReplayPage()，
   而 `const` 在那之前处于 TDZ（暂时性死区）⇒ 整页空白且 console 才看得到
   `Cannot access 'RP' before initialization`。函数声明会提升，const 不会。 */
const RP_KEY = "frisbee.replay.cards";
const RP = { card: null, store: [], pos: null, busy: false, br: null, bookQuery: "",
             broken: false, recovery: "", lastRaw: null, conflict: false };
const TEST = { q: "", k: 8, res: null, seq: 0 };
// 评分反馈。reasons 从 /api/feedback 拉，保证与前端口径同源；拉不到时用兜底。
const FB = {
  reasons: [
    { id: "wrong_clause", label: "条款引错了" },
    { id: "missed_clause", label: "明明有这条却说没有" },
    { id: "unclear_action", label: "没说清场上该怎么做" },
    { id: "too_long", label: "太啰嗦了" },
    { id: "too_jargon", label: "术语太多看不懂" },
    { id: "off_topic", label: "答非所问" },
    { id: "other", label: "其他" },
  ],
  stats: null,
};

/* ------------------------------ 工具 ------------------------------ */
const $ = (id) => document.getElementById(id);
const el = (tag, cls) => { const n = document.createElement(tag); if (cls) n.className = cls; return n; };

/* 图片点击放大（2026-10-18）：注释扫描件/手势图在窄栏里字太小，点开全屏看。
   ⚠️ 只放大已有图片，不改变任何数据；Esc 或点空白关闭。 */
document.addEventListener("click", (e) => {
  const img = e.target && e.target.closest && e.target.closest(".doc-img, .fig-grid img, .scan img");
  if (!img || img.closest(".lightbox")) return;
  const box = el("div", "lightbox");
  const big = el("img");
  big.src = img.currentSrc || img.src;
  big.alt = img.alt || "";
  box.appendChild(big);
  const close = () => box.remove();
  box.addEventListener("click", close);
  document.addEventListener("keydown", function onKey(ev) {
    if (ev.key === "Escape") { close(); document.removeEventListener("keydown", onKey); }
  });
  document.body.appendChild(box);
});
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const fmtBytes = (b) => b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(1) + " MB";
const fmtDate = (iso) => { try { return new Date(iso).toLocaleString("zh-CN", { hour12: false }); } catch (e) { return iso; } };
const pct = (v) => (typeof v === "number" ? v.toFixed(1) + "%" : "—");

/* ==========================================================================
   笔记富文本内核（2026-10-07）
   ==========================================================================
   为什么有这一层：笔记正文原来是光秃秃的 <textarea>，想记「3 条规则 + 一句
   自己的看法」都排不了版。加了 contenteditable 之后问题才真正开始 ——
   **正文成了 HTML，而 HTML 一旦被渲染就是执行面**。

   ⚠️⚠️ 这里必须当成安全边界来写，不是当成"格式化功能"来写：
     · body 存在 localStorage，任何能写这个源的脚本都能改它；
     · 上一轮审计刚确认「Electron 里一处 XSS = 任意下载 + 任意 exe 执行」；
     · 所以**唯一允许进 innerHTML 的路径是 noteSanitize()，没有第二扇门**。
   白名单是"少即是多"：只留排版真正需要的标签，其余一律拆成纯文本。
   尤其注意：a / img / script / style / iframe / svg 全部不放行 ——
   前两个能外联（笔记数据会主动打出去），后三个能直接执行。 */

/* 允许的标签 → 属性白名单。**没有 href / src / style / on*** ——
   排版不需要它们，而它们每一个都是一条外联或执行通道。 */
const NOTE_TAGS = {
  b: [], strong: [], i: [], em: [], u: [], s: [], strike: [],
  h2: [], h3: [], p: [], div: [], br: [],
  ul: [], ol: [], li: [],
  blockquote: [],
  /* 待办用 <input type="checkbox">：勾选状态要跟着文字一起存。
     ⚠️⚠️ 勾选状态**不能靠 checked 属性存**（2026-10-07 真 bug，浏览器探针抓到）：
     `el.innerHTML` 序列化 checkbox 时**不输出 checked** ——
     `<input type=checkbox checked>` 读回来变成 `<input type="checkbox">`。
     浏览器把"勾没勾"放在内部属性映射里，不进序列化结果。
     ⇒ 后果是：用户打勾 → touch() 读 innerHTML → 勾没了 → 存下去是未完成。
       重开页面勾没了，"打了没用"。而界面上那个勾当时**是真的在**，用户看不出问题。
     改法：勾选状态存data-on="1"，渲染时再还原到 checked 属性上。 */
  input: ["type", "data-on"],
  code: [], pre: [],
};
/* void 元素：只开口不闭。见 noteSanitize 里那处注释。 */
const NOTE_VOID = { br: 1, input: 1 };

/* HTML → 纯文本。**导出和搜索都必须走这里**，不能直接拿 body 搜 ——
   否则搜"补"会命中 "<strong>补</strong>"，用户看到"搜到 12 条"却不知道是哪 12 条。 */
function noteText(html) {
  const s = String(html ?? "");
  if (!s) return "";
  /* 结构 → 纯文本。**这层改过三次错法**，每次症状都不同，全记在这儿：
     ① 只把**闭合**标签换成换行 ⇒ "<b>读秒</b><div>第二条</div>" 挤成
        "读秒第二条"（那个 \n 落在末尾被 trim 吃掉）⇒ 导出文件分段全丢。
     ② 开闭都换 ⇒ 每个 div 两个 \n，被 \n{3,} 归成空行，且
        <li> 的 "· " 被自己开标签的 \n 顶开 ⇒ "· \n一"。
     ③ "· " 和"\n" 分开拼、两个地方各吐一次 ⇒ 一旦漏掉重排就是 "· \n一"。
     ⇒ 现在换成**一张表，每种标签只吐一次，且一次性吐完整**： */
  const OPEN  = { p: "\n", div: "\n", h2: "\n", h3: "\n", blockquote: "\n", pre: "\n",
                  li: "\n· ", br: "\n" };
  /* ⚠️ CLOSE 表**故意是空的**。
     </li> 一开始给了 "\n"，但下一个 <li> 自己会给 "\n· "，
     两个一叠就是 "· 一\n\n· 二" —— 每条清单之间多一个空行。
     同理 </div> 不给换行：下一个块的开标签会给。
     也就是说：**换行只在"块的开头"产生一次**，闭合一律不产生。
     列表项之间不需要额外换行，因为 <li> 是块（它的开标签自带换行）。 */
  const CLOSE = {};
  let out = "", i = 0;
  while (i < s.length) {
    const lt = s.indexOf("<", i);
    if (lt < 0) { out += s.slice(i); break; }
    out += s.slice(i, lt);
    const gt = s.indexOf(">", lt);
    if (gt < 0) { out += s.slice(lt); break; }
    const raw = s.slice(lt + 1, gt).trim();
    i = gt + 1;
    const close = raw[0] === "/";
    const tag = (close ? raw.slice(1) : raw).split(/[\s/]/)[0].toLowerCase();
    if (!tag) continue;
    /* 复选框的勾选态要走data-on，原因见下面那段注释。 */
    if (tag === "input") {
      /* ⚠️⚠️ 勾与不勾**必须用不同符号**（2026-10-09 真 bug）：
         原来无论勾没勾都输出 ☐。而 body 是**导出 txt 与搜索**的唯一依据
         ⇒ 导出的文件里"做完了"和"还没做"长得一模一样，
           搜索也分不出完成度 —— 用户会以为那些事根本没记过。
         唯一能区分的字段是 data-on（checked 不进 innerHTML 序列化，
         见 noteSanitize 里那段注释）。 */
      if (/type\s*=\s*["']?checkbox/i.test(raw)) {
        out += /\bdata-on\s*=\s*["']?1/i.test(raw) ? "\u2611 " : "\u2610 ";
      }

      continue;
    }
    /* ⚠️ ul / ol 是**容器**，不出标记 —— 换行归它们的子项 <li> 管。
       把它们也算成块级会让每个列表项前面多一个空行。 */
    if (close) { if (CLOSE[tag]) out += CLOSE[tag]; continue; }
    if (OPEN[tag]) out += OPEN[tag];
  }
  return out
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/[ \t]{2,}\n/g, "\n")          // 行尾多余缩进（≥2 个才删，
    /*   单个空格是 "· " 的一部分，删了就成了 "·\n一"） */
    .replace(/\n{3,}/g, "\n\n")             // 多余空行
    .replace(/^\s+|\s+$/g, "");
}

/* 纯文本 → HTML（只在两处用：导入 txt、迁移老笔记）。
   逐行 esc 后用 <div> 包，**不把用户内容当 HTML 解析**。 */
function noteFromText(txt) {
  const lines = String(txt ?? "").replace(/\r\n?/g, "\n").split("\n");
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  if (!lines.length) return "";
  return lines.map(l => `<div>${esc(l) || "<br>"}</div>`).join("");
}

/* ⭐ 白名单过滤器。**这是本文件里唯一被允许把笔记 HTML 变成 innerHTML 的门。**
   实现说明（为什么这么写）：
     · 不做正则式替换 —— 正则无法正确解析嵌套标签，
       `<scr<script>ipt>` 这类经典绕过会直接穿过去；
     · 用 DOM 解析后**按节点重建**，未知标签"拆开保留文字"而不是整个丢掉 ——
       用户粘进来一段带颜色的文字，宁可丢样式也要保住内容。 */
function noteSanitize(html) {
  const src = String(html ?? "");
  if (!src) return "";
  const doc = new DOMParser().parseFromString("<div>" + src + "</div>", "text/html");
  const root = doc.body.firstChild;
  if (!root) return "";
  const out = [];
  /* ⚠️ 这两个是**内容也不留**的特例，不能走"拆开留文字"：
     <script>alert(1)</script> 留下文字 = 用户看到一句莫名其妙的 alert(1)，
     而那是别人粘进来的内容，跟他记的事没关系。 */
  const DROP_WITH_CONTENT = Object.assign(Object.create(null), {
    script: 1, style: 1, template: 1, noscript: 1,
  });
  const walk = (node) => {
    node.childNodes.forEach(ch => {
      if (ch.nodeType === 3) {                       // 文本
        out.push(esc(ch.nodeValue));
        return;
      }
      if (ch.nodeType !== 1) return;                 // 注释 / PI 一律丢
      const tag = ch.tagName.toLowerCase();
      // ⚠️ 用 hasOwnProperty 而不是直接查对象：`constructor` / `__proto__`
      // 这类原型键在普通对象上恒为 truthy，会把 <constructor> 的正文误删。
      if (Object.prototype.hasOwnProperty.call(DROP_WITH_CONTENT, tag)) return;
      const allowed = Object.prototype.hasOwnProperty.call(NOTE_TAGS, tag)
        ? NOTE_TAGS[tag] : null;
      if (!allowed) { walk(ch); return; }            // 未知标签：拆开留文字
      /* let 不是 const：input 分支里要重建这个数组（把 data-on 归一）。
         写成 const 会抛 "Assignment to constant variable" ⇒
         **整页笔记一条都渲染不出来**，且报红信息与真因无关，很难自查。 */
      let attrs = [];
      /* ⚠️ 不能对 attributes 直接 reduce（2026-10-07 真 bug，浏览器里才暴露）。
         Node 垫片给的attributes 是真数组，`.reduce` 一切正常 ⇒ 假绿；
         真实 Chromium 里它是 NamedNodeMap，**没有 reduce**，直接抛
         TypeError ⇒ 整页笔记渲染失败。
         而且这个错在**第一条笔记上就会触发**，用户看到的现象是
         "笔记功能整个坏了"，不是"某条笔记显示不对"。
         解法是按 DOM 标准写法遍历 —— NamedNodeMap 有 length 和 item()。 */
      const an0 = ch.attributes;
      let hadChecked = false;
      for (let ai = 0; ai < an0.length; ai++) {
        const a = an0[ai];
        const an = a.name.toLowerCase();
        /* checked 不在白名单里（见 NOTE_TAGS 的注释），但**要记住它**——
           它会被翻译成 data-on。 */
        if (an === "checked") { hadChecked = true; continue; }
        if (allowed.indexOf(an) < 0) continue;   // 属性不在白名单 ⇒ 丢
        attrs.push([an, a.value]);
      }
      /* ⭐⭐ input 特殊处理。三条都是实测踩出来的，不是预防性设计：
         ① type 强制 checkbox —— 粘贴进来的 <input type=image onerror=…>
            能触发一次外部请求。属性白名单挡的是属性，type 得单锁。
         ② **不输出 disabled** —— disabled 的复选框点不动 ⇒ 用户勾不上、
            也取消不了；而正因为点不动，change 永不触发，勾选状态存不下来。
            防注入靠 ① 与「属性白名单不含 on*」两道，disabled 是多余的第三道，
            反而挡住了正常用法。
         ③ ⭐ **checked 改写成 data-on**（这是本文件最难查的一个 bug）：
            `el.innerHTML` 序列化 checkbox 时**不输出 checked** ——
            `<input type=checkbox checked>` 读回来是 `<input type=checkbox>`。
            浏览器把"勾没勾"放在内部属性映射里，不进序列化结果。
            ⇒ 用户打勾 → touch() 读 innerHTML → 勾没了 → 存下去是未完成。
               重开页面勾没了，"打了没用"。而当时界面上那个勾**是真的在**，
               用户完全看不出问题。data-on 是普通属性，能正常往返。 */
      if (tag === "input") {
        // 2026-10-24：原来只滤 data-on，粘贴进来的 `<input type=image>` 会保留
        // 原 type，再追加一个 type=checkbox ⇒ 序列化成两个 type，浏览器取第一个。
        // 白名单里没有 src/onerror，构不成执行，但"强制 checkbox"的承诺不成立。
        attrs = attrs.filter(a => a[0] !== "data-on" && a[0] !== "type");
        if (hadChecked || ch.hasAttribute("data-on")) attrs.push(["data-on", "1"]);
        attrs.push(["type", "checkbox"]);
      }      const as = attrs.map(([k, v]) => v === "" ? ` ${k}` : ` ${k}="${esc(v)}"`).join("");
      /* void 元素不能给闭合标签：多写一个 </input> 在 HTML5 解析器眼里是
         被忽略的，但会让"这条笔记只有 <br>"的判空判断变得不可预测。 */
      if (NOTE_VOID[tag]) { out.push(`<${tag}${as}>`); return; }
      out.push(`<${tag}${as}>`);
      walk(ch);
      out.push(`</${tag}>`);
    });
  };
  walk(root);
  return out.join("").replace(/(<div><br><\/div>)+$/g, "");
}

/* 把 data-on 还原成 checked 的显示状态。
   ⚠️ 与 noteSanitize 里的 ③ 是一对：sanitize 把 checked 翻成 data-on
   （因为 innerHTML 序列化不认 checked），这里再翻回去给用户看。
   少了这一步的表现很怪：**存都对、刷新后也还在存里，但界面上全是未勾选** ——
   数据是对的、显示是错的，比数据丢还难查（用户会以为是自己勾错了）。 */
/* 保存前把复选框的**当前勾选状态**写回 data-on 属性。
   ⚠️ 这是整条链路的一环，缺了它前面两步都白做：
     浏览器把"勾没勾"存在内部状态里，innerHTML 序列化**不输出 checked**
     ⇒ 不主动写回去，sanitize 读到的就是"未勾选"，存下去永远是没勾的。
     所以顺序必须是：用户点 → change → syncTodoAttrs → sanitize → 存。
     注意要**双向同步**（勾上写 1、取消要删掉），否则取消勾选也存不下去。 */
function syncTodoAttrs(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll("input[type=checkbox]").forEach(cb => {
    if (cb.checked) cb.setAttribute("data-on", "1");
    else cb.removeAttribute("data-on");
  });
}

function applyTodoState(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll("input[data-on]").forEach(cb => {
    cb.checked = cb.getAttribute("data-on") !== "0" && cb.hasAttribute("data-on");
  });
}

/* 判断是不是"空笔记"。**不能只查 !body** —— 富文本正文可能有
   "<div><br></div>" 或 "<br>" 这种只剩换行的壳（编辑器清空后就是它），
   那不是内容，是残留。真判据是"去掉排版标记后有没有字"。 */
function noteBlank(n) {
  const txt = noteText((n && (n.rich || n.body)) || "");
  return !txt.trim() && !(n && n.title) && !(n && n.star);
}

/* 搜索用的正文。**优先 body 这个纯文本镜像**，只有它缺失才回退到
   rich 现场转纯文本 —— 兜的是两种情况：迁移前的老数据（body 还是 HTML），
   以及导入时 body 被外部文件覆盖的那一条。
   ⚠️ 判据不能写成"body 里有没有 <"：笔记正文写"a < b"是常事，
   拿尖括号判断格式，会把纯文本误当 HTML 送去 noteText，
   结果 "< b 之后全没了" —— 搜一条明明在的笔记却搜不到。 */
function noteSearchable(n) {
  if (!n) return "";
  const plain = String(n.body || "");
  if (n.fmt === "html" && plain) return plain;
  return noteText(n.rich || plain);
}

async function api(path, opt) {
  const r = await fetch(path, opt);
  const d = await r.json().catch(() => ({ detail: r.statusText }));
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}

/* ⚡ 流式问答：读 SSE，边收边渲染（2026-10-13 上）
 *
 * ## 为什么值得单独写一个（而不是复用 post()）
 * `post()` 等的是**一个完整 JSON**；流式要求"边到边处理"，
 * 两者的生命周期完全不同 —— 硬塞进 post() 只会让两边都变难读。
 *
 * ## 帧契约（服务端 app/server.py `_ask_stream`）
 * ```
 * data: {"k":"meta","d":{...}}      # 检索刚跑完就发：依据/条款/改写后的问法
 * data: {"k":"token","d":"一小段"}  # 模型边写边发（可能有几百帧）
 * data: {"k":"done","d":{...}}      # 完整 payload（等于旧 /api/ask 的返回）
 * data: {"k":"error","d":{...}}     # 出错了
 * : ping                            # 心跳，忽略
 * ```
 *
 * ## 三条必须做对的（每条都对应一个真实故障）
 * ① **按 `\n\n` 切帧，不按行**：SSE 以空行分帧，一个 `data:` 里可能有换行。
 *    按行读会把一个 JSON 拆坏 → `JSON.parse` 抛错 → 整条流被丢弃。
 * ② **`done` 之前断开也要有结果**：网络抖动时 token 已经渲染了，
 *    不能让用户看到半截然后什么都没有 —— 用已收到的内容兜底。
 * ③ **`signal` 要真的传下去**：否则前端的 90 秒看门狗只能"不管了"，
 *    底层请求还挂着（连接和服务端线程都占着）。
 */
async function askStream(body, signal, onToken) {
  const r = await fetch("/api/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
    signal,
  });
  if (!r.ok || !r.body) {
    // 服务端不支持流式（或代理吞了 body）⇒ 退回一次性接口，**不能让功能消失**
    try {
      const d = await r.json();
      if (d && d.detail) throw new Error(d.detail);
    } catch (e) { /* 落到下面的兜底 */ }
    return await post("/api/ask", body, signal);
  }

  const reader = r.body.getReader();
  const dec = new TextDecoder("utf-8");
  let buf = "", sofar = "", payload = null, errored = "";
  // ⚠️⚠️ 「依据」那一帧（meta）里的字段要**留着**，最后并进 payload。
  //
  // 2026-10-17 修的线上 bug：服务端的 `done` 帧一度只带
  // `{answer, mode, provider, glossary, usage}` —— 生成层不知道条款，
  // 而服务端那一处漏了合并 ⇒ 前端拿到的 `citations` 是 undefined ⇒
  // 答案下面没有条款芯片、右栏永远是"没有引用条款原文"、点条款号右栏是空的。
  //
  // 服务端已改成一份上下文两个出口共用（见 `_retrieval_context`）。
  // 这里**再兜一层**：万一以后又一次漏字段，meta 帧里已经发过的东西能补回来。
  // ⚠️ 合并方向是 `{...ctx, ...payload}`：done 帧的答案/模式/用量永远优先。
  let ctx = {};
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) {
        const s = line.trim();
        if (!s || s.startsWith(":")) continue;         // 心跳
        if (!s.startsWith("data:")) continue;
        let j;
        try { j = JSON.parse(s.slice(5).trim()); }
        catch (e) { continue; }                        // 坏帧跳过，别毁掉整条流
        const k = j && j.k, d = j && j.d;
        if (k === "token") {
          sofar += (typeof d === "string" ? d : "");
          if (onToken) { try { onToken(sofar); } catch (e) { /* 渲染失败不能中断收流 */ } }
        } else if (k === "meta") {
          if (d && typeof d === "object") ctx = Object.assign(ctx, d);
        } else if (k === "done") {
          payload = (d && typeof d === "object") ? d : {};
        } else if (k === "error") {
          errored = (d && d.message) || "生成出错";
        }
      }
    }
  }
  if (payload) {
    payload = Object.assign({}, ctx, payload);          // ⚠️ 见上面 ctx 的说明
    if (!payload.answer) payload.answer = sofar;       // done 没带正文就用已收到的
    return payload;
  }
  if (errored) throw new Error(errored);
  if (sofar) return Object.assign({}, ctx, { answer: sofar, mode: "partial", provider: null });
  throw new Error("服务没有返回任何内容");
}

/* 把流式收到的半截答案渲染进"正在理解"那个气泡里（原地更新，不新建节点）。
 * ⚠️ 原地更新而不是每帧 appendChild：一次回答可能有 400+ 帧，
 *    每帧都动 DOM 会让页面明显卡顿（而且会把滚动位置顶乱）。 */
function renderStreaming(inner, text) {
  if (!inner) return;
  // ⚠️⚠️ 选择器必须**同时匹配 `.loading` 和 `.streaming`**（2026-10-13 实测踩过）：
  //    第一次调用会把 class 从 `.loading` 改成 `.streaming`，
  //    如果这里只查 `.loading`，**第二次调用就找不到气泡了** ⇒ 直接 return
  //    ⇒ 用户只看到**第一个字**，后面 400 多个 token 全部丢弃。
  //    症状极具迷惑性：服务端确实发了 416 个 token 帧，前端只显示了 1 个字。
  const box = inner.querySelector(".loading, .streaming");
  if (!box) return;
  if (!box.dataset.streaming) {
    box.dataset.streaming = "1";
    box.className = "streaming";
  }
  box.textContent = text;
  scrollDown(false);
}


/* ⚠️⚠️ 2026-10-13：**前端界面自己的诊断上报**（owner 报「一直转圈不出答案」）
 *
 * 为什么需要它：那个 bug **只在真实浏览器里出现**，服务端日志完全正常
 * （200 / answer_len 719），JS 控制台**没有任何异常**。
 * 我从外面用 CDP 猜 DOM 猜了三次，三次都错。
 * ⇒ 与其继续从外面猜，不如**让前端把它当时看到的现场自己记下来**。
 *
 * ⚠️ 三条纪律：
 *  ① **节流**：每个页面生命周期最多报 8 条，否则一个坏页面能刷爆日志。
 *  ② **绝不阻塞**：fire-and-forget，失败静默 —— 诊断代码不能影响用户拿到答案。
 *  ③ **不含个人身份**：只报 UI 结构计数与内部 id，没有问题文本、没有 IP/UA。
 *     （`sid` 是前端自己生成的会话 id，不是用户标识。）
 */
const UI_DIAG_MAX = 8;
let uiDiagSent = 0;
function uiDiag(info) {
  try {
    if (uiDiagSent >= UI_DIAG_MAX) return;
    uiDiagSent++;
    const body = JSON.stringify(Object.assign({
      kind: "ui-stuck", url: location.pathname + location.hash,
      t: Date.now(),
    }, info || {}));
    // ⚠️ 不用 post()（它带 ownerHeaders 且会 await）；用裸 fetch + keepalive，
    //    页面正在跳转时也能发出去。
    fetch("/api/ui-diag", { method: "POST", keepalive: true,
      headers: { "Content-Type": "application/json" }, body }).catch(() => {});
  } catch (e) { /* 诊断代码永远不许抛 */ }
}
const post = (path, body, signal) => api(path, {
  method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, ownerHeaders(path)),
  body: JSON.stringify(body || {}), signal,
});

/* ---- Owner 写令牌（2026-10-07 起后端要求）----
   疑问库是唯一能绕过检索+生成层、直接把一段文本当「人工核定」权威答案
   吐给用户的通道，而服务部署在公网且此前零鉴权 ⇒ 任何人都能注入答案。
   后端已改成 fail-closed：没有令牌就拒绝写入。令牌由 Owner 在
   「人工裁定」页填一次，存本机 localStorage，之后写请求自动带上。
   ⚠️ 只存在本机；读取人工裁定时也发送给本项目服务器。 */
const OWNER_KEY = "frisbee.rag.ownerToken";
const ownerToken = () => { try { return localStorage.getItem(OWNER_KEY) || ""; } catch (e) { return ""; } };
const setOwnerToken = (v) => {
  try { v ? localStorage.setItem(OWNER_KEY, v) : localStorage.removeItem(OWNER_KEY); } catch (e) { /* 无痕模式等 */ }
};
function ownerHeaders(path) {
  // 2026-10-24：令牌只发给真正需要它的 `/api/curated` 写接口。
  // 旧实现把它附加到**所有** POST（ask/feedback/practice/card），
  // 一旦任何页面有 XSS，攻击者拿到的就是可写权威答案的 Owner 令牌；
  // 而且这些请求本来不需要令牌，多带一次就是多一次暴露面。
  if (path !== "/api/curated") return {};
  const t = ownerToken();
  return t ? { "X-Owner-Token": t } : {};
}

/* 极简 toast：只有一条消息、几秒后自动消失。
   用于"试问一次"这类即时反馈，不值得为它做一套组件。 */
let _toastTimer = null;
function toast(msg) {
  let t = $("toast");
  if (!t) {
    t = el("div", "toast");
    t.id = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove("on"), 4200);
}

/* 轻量 Markdown：只覆盖本系统实际会产出的语法，不引库 */
function md(src) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\((\d{1,2}(?:\.\d{1,2}){1,4})\)/g, (m, id) => `<span class="cite" data-id="${id}">${id}</span>`);
  const out = []; let list = null;
  const flush = () => { if (list) { out.push("<ul>" + list.join("") + "</ul>"); list = null; } };
  for (const raw of String(src ?? "").split("\n")) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    if (line.startsWith("> ")) { flush(); out.push("<blockquote>" + inline(line.slice(2)) + "</blockquote>"); continue; }
    if (line.startsWith("- ")) { (list = list || []).push("<li>" + inline(line.slice(2)) + "</li>"); continue; }
    flush();
    // ⚠️ 这里原本有一条"把 **结论** / **依据** 渲染成小标题"的分支，已删除。
    //    现在一律当普通段落走，`**xx**` 就是加粗 —— 界面不再替模型长出小节标题。
    out.push(`<p>${inline(line)}</p>`);
  }
  flush();
  return out.join("");
}

/* ==========================================================================
   路由
   ========================================================================== */
function parseHash() {
  // 此处的 #/replay 是解析层兜底；正常空 hash 启动会先在 bootUI() 设为 #/chat。
  // 首页入口仍待 Owner 按用户证据决定；复盘是重要场景，不是全部产品定义。
  // ⚠️ 先切掉 ?query —— 否则 `#/chat?ACT=xxx` 会被当成 main="chat?ACT=xxx"，
  // 匹配不到视图映射就掉进"非对话页"，顶栏会莫名冒出来。
  const raw = (location.hash || "#/replay").split("?")[0];
  const m = raw.replace(/^#\/?/, "").split("/");
  const main = m[0] || "chat";
  // 设置页缺省落在"外观与阅读"；其余页面无子页。
  const sub = m[1] || (main === "settings" ? "main" : "");
  return { main, sub };
}
function go(path) { location.hash = "#/" + path; }

/* 视图挂载点（设置页 = 左目录 + 右内容两栏）。
   ⚠️⚠️ 这里踩过一个很典型的坑，过程值得留着：

   最初把外壳建在 routeNow() 里 ——
     pbody.innerHTML = ""; pbody.appendChild(shell); mountPoint = shell.querySelector(".setmain")
   然后 mount(wrap) 里再pbody.innerHTML = ""。结果**左目录整块消失**，
   量出来 setnav 宽度 0，而 console 干干净净。

   原因：mount() 是"清空挂载区再写入"，而它清的就是 pbody ——
   外壳刚挂上去就被自己擦掉了，.setmain 成了 detached 节点，
   往里 append 的内容 nobody 看得见。
   **节点存在 ≠ 在文档里**，这种错不会报错，只能靠量 getBoundingClientRect 发现。

   ⇒ 结论：外壳和内容的组装必须在**同一个函数**里完成。
   mount() 自己建 setwrap，routeNow 只负责决定"这次要不要两栏"。 */
let twoCol = false;      // 本次路由是否走两栏（设置页）
let setCurSub = "";      // 左目录当前高亮项
function mount(wrap) {
  const pbody = $("pbody");
  pbody.innerHTML = "";
  if (!twoCol) { pbody.appendChild(wrap); return; }
  const shell = el("div", "setwrap");
  shell.appendChild(buildSetNav(setCurSub));
  const main = el("div", "setmain");
  main.appendChild(wrap);
  shell.appendChild(main);
  pbody.appendChild(shell);
}
function buildSetNav(cur) {
  const box = el("div", "setnav");
  SET_GROUPS.forEach(g => {
    const isDev = !!g.dev;
    const hasCur = g.items.some(it => cur === it.id.split("/")[1]);
    // 开发者组默认折叠（见 SET_GROUPS 上方说明）。
    // ⚠️ 但**当前正停在它里面**时必须展开 —— 否则点进去之后
    //    左列里找不到自己那一项、高亮消失，看着就像页面坏了。
    const open = !isDev || !!PREF.devnav || hasCur;

    let head;
    if (isDev) {
      head = el("button", "setnav-t dev");
      head.title = g.desc || "";
      head.innerHTML = `<i class="cv">${open ? "▾" : "▸"}</i><span>${esc(g.name)}</span>`;
      head.addEventListener("click", () => {
        PREF.devnav = !open; savePref();
        const fresh = buildSetNav(cur);
        box.replaceWith(fresh);
      });
    } else {
      head = el("div", "setnav-t");
      head.textContent = g.name;
    }
    box.appendChild(head);
    if (!open) return;

    g.items.forEach(it => {
      const b = el("button", "setnav-i" + (cur === it.id.split("/")[1] ? " on" : ""));
      b.textContent = it.name;
      b.addEventListener("click", () => go(it.id));
      box.appendChild(b);
    });
  });
  return box;
}

/* 侧栏只有一件事：历史对话。
   ⚠️ 这里**不要**再加任何功能入口 —— 全部功能在左下角头像里（ME_MENU）。
   同一个功能出现两处，导航就会重新分裂；这是上一版踩过的坑。 */
function renderNav(route) {
  const box = $("navScroll");
  box.innerHTML = "";
  const hist = el("div", "hist"); hist.id = "hist"; box.appendChild(hist);
  paintHist();
  $("nav").classList.toggle("on-chat", route.main === "chat");
}

function renderHead(route) {
  let title, desc;
  const key = route.sub ? route.main + "/" + route.sub : route.main;
  if (SUB_META[key]) { [title, desc] = SUB_META[key]; }
  else { [title, desc] = PAGE_META[route.main] || ["—", ""]; }
  $("pgTitle").textContent = title;
  $("pgDesc").textContent = desc || "";

  // 设置页不再用横向 tabs —— 12 个排不下、换行又和标题重复。
  // 目录改到左列（.setnav，见 buildSetNav），这里清掉旧槽位。
  $("tabsSlot").innerHTML = "";

  // 顶栏右侧：非对话页唯一的"回去"出口。
  // 侧栏已经没有对话入口了，这里必须给一条回来的路，否则用户在子页会迷路。
  const act = $("pgAct");
  act.innerHTML = "";
  if (route.main !== "chat") {
    const b = el("button", "btn sm back2chat");
    b.textContent = "← 回到对话";
    b.addEventListener("click", () => go("chat"));
    act.appendChild(b);
  }
}

function routeNow() {
  let route = parseHash();
  /* 「查规则」2026-10-17 升到一级（`#/rules`，头像浮层第一项）。
     ⚠️ 旧 hash（`#/settings/rules` / `kb` / `search`）**仍然能进** ——
        老笔记、老书签、练习页里点条款号留下的链接不能变成空白页。
     ⚠️ 归一放在这里、而不是在 viewSettings 里再画一份：
        查规则不是配置项，不该套着设置页的「左目录 + 右内容」外壳
        （目录里已经没有它了，套进去会"当前页不在目录里"）。
     ⚠️ 归一后**不改写地址栏** —— 改了会触发新的 hashchange，
        还会把用户看到 / 收藏的旧链接悄悄换掉。 */
  if (route.main === "settings" &&
      (route.sub === "rules" || route.sub === "kb" || route.sub === "search")) {
    KB.ruleTab = route.sub === "kb" ? "browse"
               : route.sub === "search" ? "search" : null;
    route = { main: "rules", sub: "" };
  }
  renderNav(route);
  renderHead(route);

  const pbody = $("pbody");
  pbody.className = "pbody";
  const chatFlush = route.main === "chat";
  const replayMode = route.main === "replay";
  if (chatFlush || replayMode) pbody.classList.add("flush");
  $("body").classList.toggle("flush", chatFlush || replayMode);
  // 复盘页是"一张纸"：没有侧栏、没有顶栏，整屏都是它。
  // ⚠️ 必须每次路由都 toggle（不是只在进复盘时 add）—— 否则从复盘切到设置，
  //    侧栏会一直消失，用户找不到回去的路。
  // ⚠️ 这里用 document.body，不是 $("body")：后者是 #body 主区，
  //    而侧栏是它的**兄弟** —— 要藏侧栏只能从 <body> 上选。
  document.body.classList.toggle("replay", replayMode);
  // 对话页把整条顶栏藏掉。理由：空态要"极简"，
  // 而「对话 / 用大白话问，回答只依据 WFDF 规则原文」这行标题在首屏最显眼的位置
  // 重复了输入框已经表达的事 —— 用户一进来看到的是"这是个功能"，不是"我可以问"。
  // 代价：对话页顶栏没有「← 回到对话」，但侧栏的 ＋新对话 / 历史条目就是出口。
  $("body").classList.toggle("bare", chatFlush || replayMode);

  // 设置页 = 左目录 + 右内容。目录常驻不重画，
  // 否则用户切子页时列表闪一下、还要重新找刚才看到哪。
  if (route.main === "settings") {
    pbody.classList.add("twocol");
    twoCol = true;
    setCurSub = route.sub;
  } else {
    pbody.classList.remove("twocol");
    twoCol = false;
    setCurSub = "";
  }
  disposeHero();          // 离开对话页时拆掉飞盘的 WAAPI 动画与 matchMedia 监听
  pbody.innerHTML = "";

  const view = {
    replay: viewReplayPage,
    overview: viewOverview, chat: viewChat, search: viewSearch,
    kb: viewKB, curated: viewCurated, files: viewFiles, settings: viewSettings,
    notes: viewNotes, practice: viewPractice, rules: viewRules,
  }[route.main] || viewChat;
  view(route);
  pbody.scrollTop = 0;
}

/* ==========================================================================
   概览
   ========================================================================== */
function viewOverview() {
  const s = AS.stats || {};
  const w = EVAL.faq.wide, st = EVAL.faq.strict;
  const wrap = el("div", "wrap");

  wrap.innerHTML = `
    <div class="sec">
      <div class="stats">
        <div class="stat"><div class="k">规则条款</div><div class="v num">${s.rules ?? "—"}<span class="u">条</span></div><div class="n">与官方英文原文逐章一致</div></div>
        <div class="stat"><div class="k">术语定义</div><div class="v num">${s.definitions ?? "—"}<span class="u">条</span></div><div class="n">官方名词表全量</div></div>
        <div class="stat"><div class="k">实测问法</div><div class="v num">${EVAL.faq.n}<span class="u">条</span></div><div class="n">真实盘友的日常问法</div></div>
        <div class="stat"><div class="k">不该答的</div><div class="v num">${EVAL.refuse.hit}/${EVAL.refuse.n}</div><div class="n">问错了会直说"规则里没有"</div></div>
      </div>
    </div>

    <div class="sec">
      <div class="sec-t">它答得准不准 · ${EVAL.date} 实测<div class="ln"></div></div>
      <div class="card">
        <div class="card-b">
          <div class="note">用 ${EVAL.faq.n} 条真实盘友问法跑了一遍，看它给出的前几条里有没有该出现的那条规则。
            两档都摊开：<b>宽松</b>允许父子条款互通，<b>严格</b>必须精确到条款号。
            —— 数字不好看也不改，因为它比一句"很准"有用。</div>
          <div class="kv" style="margin-top:14px">
            <div class="r"><div class="k">日常问法 · 宽松</div><div class="v">
              <b>前 3 条里有正确答案 ${pct(w[3])}</b> · 前 1 条 ${pct(w[1])} · 前 5 条 ${pct(w[5])}
              <div class="sub">这是大多数人实际感受到的准确度</div></div></div>
            <div class="r"><div class="k">日常问法 · 严格</div><div class="v">
              <b>前 3 条里有正确答案 ${pct(st[3])}</b> · 前 1 条 ${pct(st[1])} · 前 5 条 ${pct(st[5])}
              <div class="sub">必须精确命中条款号，父子条款打通不算</div></div></div>
            <div class="r"><div class="k">场上情形题</div><div class="v">
              <b>${EVAL.accept.hit8}% 全中</b>（${EVAL.accept.n}/${EVAL.accept.n}）
              <div class="sub">人工编写的复杂场景，例如"盘落在中心区、对方站着不捡盘"</div></div></div>
            <div class="r"><div class="k">问错的时候</div><div class="v">
              <b>${EVAL.refuse.hit}/${EVAL.refuse.n} 明确拒答</b>
              <div class="sub">问"飞盘多少钱一个"，它必须说"规则里没写"，不能编一条出来</div></div></div>
          </div>
        </div>
      </div>
    </div>

    <div class="sec">
      <div class="sec-t">它的底线<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">找不到就说不确定</div><div class="v">
          宁可回一句"规则里没写这条"，也不给你一条像模像样但根本不存在的规定。
          <div class="sub">这是它和"查百度"最大的区别 —— 编错一条规则，比答不出来伤害大得多。</div></div></div>
        <div class="r"><div class="k">每条回答都给出处</div><div class="v">
          回答里的条款号（如 <span class="cite" style="cursor:default">14.2</span>）可以点开看原文。
          <div class="sub">你可以自己核对它有没有说错，而不是只能信它。</div></div></div>
        <div class="r"><div class="k">没有裁判</div><div class="v">
          飞盘是场上自己执法的运动，它不会说"裁判判罚"，只会说"可以示意违规"。
          <div class="sub">极限飞盘没有裁判，用错词本身就是外行信号。</div></div></div>
      </div></div></div>
    </div>

    <div class="sec">
      <div class="sec-t">它背后在跑什么<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv" id="ovRun"></div></div></div>
    </div>`;

  const run = wrap.querySelector("#ovRun");
  const cfg = s.llm_configured;
  run.innerHTML = `
    <div class="r"><div class="k">写答案的模型</div><div class="v">
      <span class="badge ${cfg ? "ok" : "gray"}"><i></i>${cfg ? "已就绪" : "未配置 · 只能看原文"}</span>
      <div class="sub">${esc(s.llm_chain || "")}　每条回答下方会标注实际用的是哪个</div></div></div>
    <div class="r"><div class="k">规则原文</div><div class="v">${esc(s.source || "—")}
      <div class="sub">WFDF Rules of Ultimate 2025-2028 官方英文原文的中文对应版本，
        共 ${s.chunks ?? "—"} 个可检索片段</div></div></div>`;

  mount(wrap);
}

/* ==========================================================================
   知识库
   ========================================================================== */
function viewKB(route) {
  if (route.sub === "test") return viewKBTest();
  if (route.sub === "config") return viewKBConfig();
  return viewKBDataset();
}

/* ⭐⭐ 「查规则」：按章节翻 + 模糊查找，合并成一个入口（2026-10-10）

   本轮**唯一的导航改动**。理由写在这里，免得下一轮被"拆回两个菜单项"：

     原来设置里有两项：
       「规则原文」按章节浏览（页内还自带一个字符串搜索框）
       「规则检索」走语义检索（输入一句大白话）
     **两件事都得留着** —— 前者给想系统翻规则的人，
     后者给"只记得意思、不记得术语"的人（⚠️ 后者才是新手真正需要的）。

     但并列成两个菜单项，等于**逼用户先回答「我该点哪个」**，
     而他答不出来 —— 他不知道这两个词的区别。
     这和owner 否决"按新手/老队员分档"是同一个错：
     **让用户先自我认知才能走通的路，就是没走通的路。**

   ⇒ 所以不是砍掉一个（那会让另一半人失去入口），
     而是并成一个入口 + 页内两个 tab，把选择挪进他已经看得懂的场景。

   ⚠️ 默认停在「按章节翻」，且它在左边（owner 2026-10-18 改，推翻上一版的默认）：
   翻规则是这一页的主路径；「模糊查找」放右边，需要的人自己点过去。
   ⚠️ 别只看这行就改回去 —— 两种默认都有人主张过，**现在的判据是 owner 的明确指令**。 */
function viewRules() {
  const wrap = el("div", "wrap wide");
  const tabs = el("div", "rules-tabs");
  const body = el("div", "rules-body");

  // ⭐ 默认「按章节翻」，且它排在左边（owner 2026-10-18 定，推翻 2026-10-10 的默认）。
  //    原话：「把『按章节翻』从右边挪到左边，作为默认打开查规则的方式；
  //    『模糊查找』放到右边，用户想模糊查找时，让他往右边点一下。」
  //    ⚠️ 检索没有降级，只是不再是开门第一眼 —— 它是"记不清条号"时的备选路径，
  //       而"我知道我要查哪一章"才是这一页叫「查规则」时最直白的读法。
  const cur = KB.ruleTab || "browse";

  const paint = () => {
    [...tabs.children].forEach(b => b.classList.toggle("on", b.dataset.k === KB.ruleTab));
    body.innerHTML = "";
    const sub = el("div");
    body.appendChild(sub);
    if (KB.ruleTab === "browse") viewKBDataset(sub);
    else viewSearch(sub);
  };

  [["browse", "按章节翻"], ["search", "模糊查找"]].forEach(([k, name]) => {
    const b = el("button", "rules-tab");
    b.dataset.k = k;
    b.textContent = name;
    b.addEventListener("click", () => { KB.ruleTab = k; paint(); });
    tabs.appendChild(b);
  });

  KB.ruleTab = cur;
  wrap.appendChild(tabs);
  wrap.appendChild(body);
  mount(wrap);
  paint();
}

function viewKBDataset(into) {
  const wrap = el("div", "wrap wide");
  // `into` = 挂到指定容器（合并页用）；不传就照旧自己 mount（独立入口用）。
  // 守卫 test_rules_page_has_two_tabs 钉住"传 into 时不许再 mount"——
  // 一旦两处都做，mount 会把外壳擦掉，tab 消失（mount 的注释里记过这个坑）。
  const put = (w) => { if (into) { into.innerHTML = ""; into.appendChild(w); } else mount(w); };
  const chs = AS.chapters || [];
  if (!chs.length) { wrap.innerHTML = `<div class="empty">章节结构加载中…</div>`; put(wrap); return; }
  // ⚠️ 默认不要停在附录上：附录是**置顶的一层参考资料**，不是"第一章"。
  //    用户开「按章节翻」时想看的是规则正文（1. 飞盘精神）。
  if (!KB.chapter) KB.chapter = (chs.find(c => c.chapter === "1") || chs[0]).chapter;

  wrap.innerHTML = `
    <div class="split">
      <div class="split-l" id="chList"></div>
      <div class="split-r" id="itemList"></div>
    </div>`;

  const chList = wrap.querySelector("#chList");
  const itemList = wrap.querySelector("#itemList");

  // 文档身份（owner 2026-10-18）：「我在看的到底是哪一份文件？」
  // 附录 / 官方注释 / 规则正文，三份东西必须一眼分得清 —— 尤其是注释是旧版（2021–2024）。
  const DOC = {
    APP:  { name: "WFDF 官方附录 v2.0", meta: "2026-01-01 生效 · 中文参考译本（1.0 底本 + v2.0 校订）", tag: "非正文", warn: false },
    ANN:  { name: "WFDF 官方注释", meta: "2021–2024 版 · 扫描原件", tag: "非现行规则", warn: true },
    RULE: { name: "WFDF 团队飞盘规则 2025–2028", meta: "中文版", tag: "正文", warn: false },
  };
  const docBanner = (kind) => {
    const d = DOC[kind];
    const n = el("div", "doc-banner" + (d.warn ? " warn" : ""));
    n.innerHTML = `<span class="db-k">你正在看</span><b>${esc(d.name)}</b>
      <span class="db-m">${esc(d.meta)}</span><span class="db-tag">${esc(d.tag)}</span>`;
    return n;
  };

  const figBlock = (imgs) => `<div class="fig-grid">` + imgs.map(im =>
    `<figure${im.wide ? ' class="wide"' : ''}><img loading="lazy" src="/${esc(im.src)}" alt="${esc(im.alt)}">
      <figcaption>${esc(im.alt)}</figcaption></figure>`).join("") + `</div>`;

  const paintItems = () => {
    itemList.innerHTML = "";
    const c = chs.find(x => x.chapter === KB.chapter) || chs[0];
    const items = c.items;
    if (!items.length) { itemList.innerHTML = `<div class="empty" style="padding:22px 16px">这一章还没有条款。</div>`; return; }
    const kind = KB.chapter === "APP" ? "APP" : (KB.chapter === "ANN" ? "ANN" : "RULE");
    itemList.appendChild(docBanner(kind));
    const head = el("div", "item");
    head.style.background = "var(--bg-2)";
    const headName = kind === "APP" ? "附录" : (kind === "ANN" ? "官方注释（2021–2024）" : "第 " + esc(KB.chapter) + " 章");
    head.innerHTML = `<div class="ih"><b>${headName}</b>
      <span class="d">${items.length} 条</span></div>`;
    itemList.appendChild(head);
    if (kind === "APP") {
      // 附录层的**身份说明**：不写这一句，用户会以为这是正文第几章。
      const note = el("div", "app-note");
      note.textContent = "以教练提供的 1.0 版中文译本为底本，按官方英文 v2.0 校订：未改动的条文照录译本，"
        + "v2.0 改动过的条文（A5.4、A5.8、A7、A12、B6、D2–D8）由我们重译。属于非正文条款；"
        + "适用于 WFDF 锦标赛，与正文冲突时以附录为准；国内比赛是否采用，由该场竞赛规程决定。";
      itemList.appendChild(note);
    }
    if (kind === "ANN") {
      // 注释是**旧版**，且冲突时以现行规则为准 —— 这两句必须让人看见，不能只靠标签。
      const w = el("div", "doc-warn");
      w.textContent = "这不是现行规则。它解释的是 2021–2024 版；与 2025–2028 规则冲突时，"
        + "以现行规则和附录为准。下面默认是 OCR 文字稿，可能有识别错误；每页可展开扫描原件核对。";
      itemList.appendChild(w);
    }
    let lastSec = null;
    items.forEach(it => {
      if (it.type === "annotation_page") {
        const pg = el("div", "item page-item");
        const blocks = it.ocr_blocks || [];
        const hasText = blocks.length > 0;
        const body = blocks.map(b => {
          if (b.t === "h") return `<div class="ann-h">${esc(b.x)}</div>`;
          if (b.t === "li") return `<div class="ann-li">${esc(b.x)}</div>`;
          return `<p class="ann-p">${b.label ? `<b>${esc(b.label)}：</b>` : ""}${esc(b.x)}</p>`;
        }).join("");
        pg.innerHTML = `<div class="ih"><b>${esc(it.label)}</b>
            <span class="d tech">${hasText ? "OCR 文字稿" : "扫描原件"}</span></div>`
          + (hasText ? `<div class="it ann-text">${body}</div>` : "")
          + `<details class="scan"><summary>看扫描原件（${esc(it.label)}）</summary>
               <img class="doc-img" loading="lazy" src="/${esc(it.image)}" alt="官方注释 ${esc(it.label)}">
             </details>`;
        itemList.appendChild(pg);
        return;
      }
      const isAppx = it.type === "appendix";
      // 附录按 A/B/C… 分段（分段标题只做分界，不是新的一"章"）
      if (isAppx && it.section !== lastSec) {
        lastSec = it.section;
        const sh = el("div", "app-sec");
        sh.innerHTML = `<b>${esc(it.section === "总说明" ? "总说明" : "附录 " + it.section)}</b>
          <span>${esc(it.section_title || "")}</span>`;
        itemList.appendChild(sh);
      }
      const d = el("div", "item" + (isAppx ? " app-item" : (it.depth <= 1 ? " d1" : "")));
      // 条号按层级上色（owner 2026-10-22）：同级同色、不同级不同色。
      // 只给规则正文打标；附录/注释/术语保持原样（它们的编号不是这个层级体系）。
      if (it.type === "rule") d.dataset.depth = it.depth;
      // 层级缩进（owner 2026-10-18）：6.1.1 / 6.1.2 是 6.1 的**款**，和 6.2 不是平级。
      // 只按官方深度缩进，不猜父子关系：depth 2 = 条（6.1 / 6.2）、3 = 款、4 = 项、5 = 目。
      // ⚠️ 搜索结果是跨章平铺（同一屏深度混在一起），缩进反而误导 ⇒ 只在「按章节翻」里缩。
      if (!isAppx && it.depth > 2) {
        d.style.paddingLeft = (16 + (it.depth - 2) * 26) + "px";
      }
      const tag = it.type === "definition" ? `<span class="tag gray">术语</span>`
        : it.type === "preface" ? `<span class="tag gray">前言</span>`
        : isAppx ? `<span class="tag gray">非正文</span>` : "";
      // 附录显示 "A1 场地" 这种人话标签，不显示 depth（"第 1 层"对用户没有意义）
      const name = isAppx ? esc(it.label || it.id) : esc(it.id);
      const tail = isAppx ? `<span class="d tech">原文 p.${esc(String(it.page || "?"))}</span>`
        : `<span class="d tech">第 ${it.depth} 层</span>`;
      // "depth N" 是原文的层级标记（1=章，2=条，3=款）。中文用户读不懂这个英文，
      // 改成"第 N 层"并且归入 tech —— 缩进本身已经表达了层级，这行是冗余的。
      d.innerHTML = `<div class="ih"><b>${name}</b>${tag}
          ${tail}</div>
        <div class="it">${esc(it.text)}</div>`
        + ((it.images && it.images.length) ? figBlock(it.images) : "")
        + (it.parent ? `<div class="par">上一层：${esc(it.parent)}</div>` : "");
      itemList.appendChild(d);
    });
  };

  const paintChs = () => {
    chList.innerHTML = "";
    chs.forEach(c => {
      const b = el("button", "ch" + (c.chapter === KB.chapter ? " on" : ""));
      // 附录 / 官方注释**不标序号**：它们是参考资料，不是"第几章"。
      b.innerHTML = c.chapter === "APP"
        ? `<span class="cn">A–H</span>${esc(c.title)}
           <span class="cn" style="float:right">${c.items.length}</span>`
        : c.chapter === "ANN"
        ? `<span class="cn">注</span>${esc(c.title)}
           <span class="cn" style="float:right">${c.items.length}</span>`
        : `<span class="cn">${esc(c.chapter === "0" ? "—" : c.chapter)}</span>${esc(c.title)}
           <span class="cn" style="float:right">${c.items.length}</span>`;
      b.addEventListener("click", () => { KB.chapter = c.chapter; paintChs(); paintItems(); });
      chList.appendChild(b);
    });
  };

  paintChs(); paintItems();
  put(wrap);
}

function viewKBTest() {
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="note" style="margin-bottom:16px">
      用你自己的问题验一验它：<b>这一页不写答案</b>，只把"它会翻出哪几条规则"摊开给你看。
      如果翻出来的那几条明显不对，那这一问的答案就别信了 —— 直接点下面的反馈告诉我们。
    </div>
    <div class="searchbox">
      <input class="inp" id="tq" placeholder="输入一个问题，例如：读秒读太快了怎么办" value="${esc(TEST.q)}">
      <select class="inp sm" id="tk" style="flex:0 0 108px">
        ${[3, 5, 8, 12, 20].map(k => `<option value="${k}"${k === TEST.k ? " selected" : ""}>翻 ${k} 条</option>`).join("")}
      </select>
      <button class="btn pri" id="tgo">检索</button>
    </div>
    <div id="tres"></div>`;

  const run = async () => {
    const q = wrap.querySelector("#tq").value.trim();
    TEST.q = q; TEST.k = parseInt(wrap.querySelector("#tk").value, 10);
    const box = wrap.querySelector("#tres");
    if (!q) { box.innerHTML = `<div class="empty">先输入一个问题。</div>`; return; }
    box.innerHTML = `<div class="loading">正在检索<span class="bar"></span></div>`;
    /*⚠️ 请求序号：连点两次搜索时，先发的可能后到（top_k=20 更慢）。
      没有这道闸门，界面会显示**上一次**问题的结果 —— 而输入框里是新的问法，
      TEST.res 也被污染，切走再回来看到的还是错的。
      ask() 那边靠 AbortController，搜索这两处当时漏了。*/
    const my = ++TEST.seq;
    try {
      const d = await post("/api/search", { q, top_k: TEST.k });
      if (my !== TEST.seq) return;          // 已有更新的请求在跑，丢弃本次结果
      TEST.res = d;
      paintTest(box, d);
    } catch (e) {
      if (my !== TEST.seq) return;
      box.innerHTML = `<div class="err">检索失败：${esc(e.message)}</div>`;
    }
  };

  wrap.querySelector("#tgo").addEventListener("click", run);
  wrap.querySelector("#tq").addEventListener("keydown", e => { if (e.key === "Enter") run(); });

  mount(wrap);
  if (TEST.res) paintTest(wrap.querySelector("#tres"), TEST.res);
  else wrap.querySelector("#tq").focus();
}

function paintTest(box, d) {
  const hits = d.hits || [];
  // ⚠️ 原来这里还有 `const best = d.best_cos, th = d.threshold;`，
// 其中 th 是 **MIN_SCORE**。2026-10-09 查明它全仓从未参与任何判定
// （真实闸门是「类目/线索词/域内词」的结构化判据，见 retriever.py:274），
// 却在这页显示成「闸门阈值」挨着「最高分」⇒ owner 会拿它做推论并去调 MIN_SCORE，
// 而调它对系统行为零影响。这里只留「最高分」当参考，不给假判据。
const best = d.best_cos;
  const routes = d.routes || [];
  box.innerHTML = "";
  // 这一页整体是"给较真的人"的，所以分数一类的数字全部标 tech，
  // 关掉开关后只剩"翻出了哪几条 + 有没有信心"这两个用户真正关心的判断。
  const g = el("div", "gauge");
  g.innerHTML = `<div class="g1">
      <span class="badge ${d.confident ? "ok" : "warn"}"><i></i>${d.confident
        ? "它觉得找到的条款够回答，会正常作答"
        : "它觉得不够，会直接回你「规则里没写这条」"}</span>
      <div class="tech" style="font-size:11.5px;color:var(--ink-3);margin-top:4px">
        最高分 <b>${typeof best === "number" ? best.toFixed(4) : "—"}</b>
        · 耗时 ${d.elapsed_ms ?? "—"} ms
        · 命中类目 ${routes.length} 个 ${routes.length ? "（" + esc(routes.join("、")) + "）" : ""}
      </div></div>`;
  box.appendChild(g);

  if (!hits.length) { const e = el("div", "empty", "没有翻出任何条款。"); box.appendChild(e); return; }
  hits.forEach((h, i) => {
    const n = el("div", "hit" + (i === 0 ? " top" : ""));
    const cos = typeof h.cos === "number" ? h.cos : 0;
    n.innerHTML = `
      <div class="hh"><span class="rk">${i + 1}</span><span class="id">${esc(h.id)}</span>
        <span class="ch">${esc(h.chapter_title || "")}</span>
        <span class="sc tech">匹配度 ${typeof h.score === "number" ? h.score.toFixed(4) : "—"}</span></div>
      <div class="tx">${esc(h.text || "")}</div>
      ${h.parent_text ? `<div class="tx" style="color:var(--ink-4);font-size:12px;margin-top:5px">上一层 ${esc(h.parent_id || "")}：${esc(h.parent_text)}</div>` : ""}
      <div class="mt tech"><span style="width:${Math.max(2, Math.min(100, cos * 100)).toFixed(1)}%"></span></div>`;
    box.appendChild(n);
  });
}

function viewKBConfig() {
  const s = AS.settings || {};
  const emb = s.embedding || {};
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="sec">
      <div class="sec-t">语料来源<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">原文版本</div><div class="v">${esc(s.source || "—")}
          <div class="sub">WFDF Rules of Ultimate 2025-2028 官方规则的中文对应版本</div></div></div>
        <div class="r"><div class="k">规则条款</div><div class="v">${(s.runtime && s.runtime.stats && s.runtime.stats.rules) ?? "—"} 条 · 20 章
          <div class="sub">与官方英文原文逐章比对一致：43/7/4/8/4/6/16/18/17/22/18/18/39/6/18/12/33/52/17/10</div></div></div>
        <div class="r"><div class="k">术语定义</div><div class="v">${(s.runtime && s.runtime.stats && s.runtime.stats.definitions) ?? "—"} 条
          <div class="sub">官方名词表，问「什么叫盘权」靠它</div></div></div>
        <div class="r"><div class="k">前言</div><div class="v">${(s.runtime && s.runtime.stats && s.runtime.stats.preface) ?? "—"} 条</div></div>
      </div></div></div>
    </div>
    <div class="sec">
      <div class="sec-t">它是怎么找条款的<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">先猜类型</div><div class="v">${esc(emb.model || "—")} · ${emb.dims || "—"} 维
          <div class="sub">把规则原文逐段算成一串数字，提问时也转成一串数字，
            找出"意思最接近"的那几段。不逐字匹配，所以你能用大白话问。</div></div></div>
        <div class="r"><div class="k">提问时会加一句前缀</div><div class="v">${esc(emb.prefix || "（无）")}
          <div class="sub">非对称检索：只给问题加，规则原文不加。实测这一句带来 +12.6 个百分点的命中率。</div></div></div>
        <div class="r"><div class="k">按条款切，不按字数切</div><div class="v">一条规则就是一块，父条款和子条款各自成块
          <div class="sub">所以"检索条目数"比"规则条数"多 —— 那是同一条规则的导读行。
            提问时会把父条款一起给模型，避免断章取义。</div></div></div>
        <div class="r"><div class="k">一共切出多少块</div><div class="v">${emb.chunks || "—"} 块
          <div class="sub">${(s.runtime && s.runtime.stats && s.runtime.stats.rules) || "?"} 规则 + ${(s.runtime && s.runtime.stats && s.runtime.stats.definitions) || "?"} 定义 + ${(s.runtime && s.runtime.stats && s.runtime.stats.preface) || "?"} 前言</div></div></div>
      </div></div></div>
    </div>
    <div class="sec">
      <div class="sec-t">为什么不上向量数据库<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="note">语料只有 ${emb.chunks || 413} 条 × ${emb.dims || 2048} 维 ≈ 3.4 MB。
        直接在内存里做矩阵点乘，一次全量打分约 <b>60–70 毫秒</b>，
        引入 Qdrant / Milvus 只会抬高部署门槛，换不来可感知的收益。
        这也是整个项目能保持<b>零第三方依赖、clone 即跑</b>的原因。</div>
      </div></div>
    </div>`;

  const feats = [{ name: "语料源文件", go: "files" }];
  if (feats.length) { /* 占位：保持结构清晰 */ }
  mount(wrap);
}

/* ==========================================================================
   搜索
   ========================================================================== */
function viewSearch(into) {
  const wrap = el("div", "wrap");
  const put = (w) => { if (into) { into.innerHTML = ""; into.appendChild(w); } else mount(w); };
  wrap.innerHTML = `
    <div class="searchbox">
      <input class="inp" id="sq" placeholder="用一句话描述场上的情况，例如：我在界外把盘拍回界内算得分吗" value="${esc(SEARCH.q)}">
      <button class="btn pri" id="sgo">搜索</button>
    </div>
    <div id="sres"></div>`;

  const run = async () => {
    const q = wrap.querySelector("#sq").value.trim();
    SEARCH.q = q;
    const box = wrap.querySelector("#sres");
    if (!q) { box.innerHTML = `<div class="empty">用大白话讲讲你遇到的情况就行，不用记条款关键词。</div>`; return; }
    box.innerHTML = `<div class="loading">正在检索<span class="bar"></span></div>`;
    const my = ++SEARCH.seq;      // 同上：防旧响应覆盖新结果
    try {
      const d = await post("/api/search", { q, top_k: 8 });
      if (my !== SEARCH.seq) return;
      SEARCH.res = d;
      paintSearch(box, d);
    } catch (e) {
      if (my !== SEARCH.seq) return;
      box.innerHTML = `<div class="err">检索失败：${esc(e.message)}</div>`;
    }
  };
  wrap.querySelector("#sgo").addEventListener("click", run);
  wrap.querySelector("#sq").addEventListener("keydown", e => { if (e.key === "Enter") run(); });

  put(wrap);
  if (SEARCH.res) paintSearch(wrap.querySelector("#sres"), SEARCH.res);
  else wrap.querySelector("#sq").focus();
}

function paintSearch(box, d) {
  const hits = (d.hits || []).slice(0, 8);
  box.innerHTML = "";
  if (!hits.length) { box.innerHTML = `<div class="empty">没有找到相关条文。换一种说法试试。</div>`; return; }
  const head = el("div");
  head.style.cssText = "font-size:12px;color:var(--ink-3);margin-bottom:11px";
  head.textContent = `找到 ${hits.length} 条，最像的排在前面`;
  box.appendChild(head);
  hits.forEach(h => {
    const n = el("div", "hit");
    const cos = typeof h.cos === "number" ? h.cos : 0;
    n.innerHTML = `<div class="hh"><span class="id">${esc(h.id)}</span>
        <span class="ch">${esc(h.chapter_title || "")}</span>
        <span class="sc tech">相关度 ${cos.toFixed(3)}</span></div>
      <div class="tx">${esc(h.text || "")}</div>
      ${h.parent_text ? `<div class="tx" style="color:var(--ink-4);font-size:12px;margin-top:5px">上一层 ${esc(h.parent_id || "")}：${esc(h.parent_text)}</div>` : ""}`;
    box.appendChild(n);
  });
}

/* ==========================================================================
   数据文件
   ========================================================================== */
function viewFiles() {
  const wrap = el("div", "wrap");
  const rows = (AS.files && AS.files.items) || [];
  wrap.innerHTML = `
    <div class="note" style="margin-bottom:16px">
      它的答案全部来自下面这几个文件，没有别的来源。<b>规则原文</b>是规则正文，
      <b>检索索引</b>是把原文切段后算出的对照表，服务启动时一次性读进内存。
    </div>
    <div class="card"><table class="tbl">
      <thead><tr><th>文件</th><th>类型</th><th class="r">体积</th><th>修改时间</th></tr></thead>
      <tbody id="fileRows"></tbody>
    </table></div>`;
  const tb = wrap.querySelector("#fileRows");
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="4" class="empty">没有读到文件清单。</td></tr>`; }
  rows.forEach(f => {
    const tr = el("tr");
    tr.innerHTML = `<td class="mono">${esc(f.name)}</td><td>${esc(f.kind)}</td>
      <td class="r">${fmtBytes(f.bytes)}</td><td style="color:var(--ink-3);font-size:12px">${esc(fmtDate(f.mtime))}</td>`;
    tb.appendChild(tr);
  });
  mount(wrap);
}

/* ==========================================================================
   疑问库
   ========================================================================== */
const CUR = { items: [], stats: null, loaded: false, open: null, prefill: "" };

function viewCurated() {
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="note" style="margin-bottom:16px">
      它答错或答不出的时候，你在这里把正确说法写下来。<br>
      写完之后<b>同类问题会直接用你的答案</b>，不再去猜。
      查找顺序是：<b>你写过的 → 规则原文 368 条 → 联网找 WFDF 官网原文</b>。
      联网那一层会单独标出来，因为它不能冒充规则原文。<br>
      <span style="font-size:12px;color:var(--dim)">
      右上角的令牌是你自己的写入凭证（服务器上CURATED_TOKEN 环境变量）。它只存在这台电脑上。
      填完会自动验一次对错；查看、新增和删除裁定都需要令牌。</span>
    </div>
    <div class="cur-bar">
      <div class="cur-stats" id="curStats">读取中…</div>
      <input id="curToken" type="password" placeholder="Owner 令牌" autocomplete="off"
             style="width:150px;font-size:12px;padding:6px 8px;border:1px solid var(--line);border-radius:6px">
      <button class="btn pri" id="curNew">+ 新建裁定</button>
    </div>
    <div id="curForm"></div>
    <div class="card" style="margin-top:14px"><div id="curList"></div></div>`;

  const form = wrap.querySelector("#curForm");
  const list = wrap.querySelector("#curList");
  // 回填已保存的令牌：不回填的话 Owner 每次刷新都要重输，而密码框的
  // type=password 配合 autocomplete=off 就是"存了但要自己填"的意思，
  // 与这里持久化的意图矛盾。
  const tok = wrap.querySelector("#curToken");
  tok.value = ownerToken();
  // 令牌自检（2026-10-09 加）：填完立刻验一次对不对，别让 Owner 只能靠
  // "新增一条 → 被拒" 才知道填错了。ping 不碰任何数据（见 server.py）。
  // ⚠️ 去抖 400ms：粘贴后失焦只触发一次，但逐字输入时会连续触发，
  //   不去抖等于每敲一个字符打一次请求。
  let tokTimer = null;
  const verifyToken = async () => {
    const v = tok.value.trim();
    if (!v) {
      setOwnerToken(""); CUR.items = []; CUR.stats = null; CUR.loaded = false;
      paint(); return;
    }   // 清空 = 退出 Owner 模式
    setOwnerToken(v);
    try {
      await post("/api/curated", { action: "ping" });
      await loadCurated(true);
      paint();
      toast("令牌可用 ✓");
    } catch (e) {
      toast("令牌不可用：" + e.message);
    }
  };
  tok.addEventListener("change", () => {
    clearTimeout(tokTimer);
    tokTimer = setTimeout(verifyToken, 400);
  });

  const paint = () => {
    const s = CUR.stats || {};
    const by = s.by_source || {};
    wrap.querySelector("#curStats").innerHTML =
      `共 <b>${s.total || 0}</b> 条裁定` +
      (by.owner ? ` · 你亲自裁定 <b>${by.owner}</b>` : "") +
      (by.web ? ` · 联网补充 <b>${by.web}</b>` : "");
    if (!CUR.items.length) {
      list.innerHTML = `<div class="card-b"><div class="empty">
        ${ownerToken() ? "还没有裁定。" : "填写 Owner 令牌后可查看裁定。"}<br><span style="font-size:12px">
        在对话页遇到"规则里没有"或"答得不对"的问题，点一下<b>「这条我不确定」</b>，
        就会出现在这里等你回答。</span></div></div>`;
      return;
    }
    list.innerHTML = "";
    CUR.items.forEach(it => {
      const open = CUR.open === it.id;
      const card = el("div", "cur-item" + (open ? " open" : ""));
      const head = el("div", "cur-h");
      head.innerHTML = `
        <div class="cur-q">${esc(it.question)}</div>
        <div class="cur-m">
          <span class="tag ${it.source === "owner" ? "t-own" : "t-web"}">${
            it.source === "owner" ? "人工核定" : "联网补充"}</span>
          ${(it.clauses || []).map(c => `<span class="cchip">${esc(c)}</span>`).join("")}
          <span class="dim">${esc(it.by || "")} · ${esc(fmtDate(it.created_at * 1000))}</span>
        </div>`;
      head.addEventListener("click", () => { CUR.open = open ? null : it.id; paint(); });
      card.appendChild(head);

      if (open) {
        const b = el("div", "cur-b");
        b.innerHTML = `<div class="cur-a">${esc(it.answer)}</div>`;
        if ((it.keywords || []).length) {
          b.innerHTML += `<div class="cur-kw"><span class="dim">同义问法：</span>${
            it.keywords.map(k => `<span class="kw">${esc(k)}</span>`).join("")}</div>`;
        }
        const acts = el("div", "cur-acts");
        const test = el("button", "btn");
        test.textContent = "试问一次";
        test.addEventListener("click", async () => {
          test.textContent = "提问中…";
          await askAndToast(it.question);
          test.textContent = "试问一次";
        });
        const del = el("button", "btn danger");
        del.textContent = "撤掉这条";
        del.addEventListener("click", async () => {
          if (!confirm("撤掉这条裁定？之后同类问题会重新走检索。")) return;
          // ⚠️ 这里必须有 try/catch（2026-10-09 修）：令牌不对 / 网络断，
          // post() 抛出的错会变成未处理的 Promise rejection ⇒ confirm 弹窗过了，
          // 然后**什么都没发生** —— 条目还在、按钮无响应，用户以为软件卡了。
          // 而删除恰恰是最需要令牌的操作，失败概率最高。
          // 失败时把按钮文字改成失败原因并允许重试，成功路径复原。
          del.disabled = true;
          del.textContent = "撤掉中…";
          try {
            await post("/api/curated", { action: "delete", id: it.id });
            await loadCurated(true);
            // ⚠️ loadCurated 只更新 CUR.items，重绘责任在这个闭包里的 paint()。
            //   原来漏了这一句 ⇒ 服务端已删、卡片却还留在屏幕上，
            //   用户会以为删除失败（还能再点一次「试问一次」）。
            //   参照 viewFeedback 的正确写法：load 完就重画当前页。
            paint();
          } catch (e) {
            del.disabled = false;
            del.textContent = "撤掉失败";
            toast("撤掉失败：" + e.message);
            setTimeout(() => { del.textContent = "重试撤掉"; }, 1600);
          }
        });
        acts.appendChild(test); acts.appendChild(del);
        b.appendChild(acts);
        card.appendChild(b);
      }
      list.appendChild(card);
    });
  };

  const showForm = (seed) => {
    form.innerHTML = "";
    const f = el("div", "card cur-form");
    f.innerHTML = `
      <div class="sec-t" style="margin-bottom:12px">新建裁定</div>
      <label class="fl">系统答不出的问题
        <input id="cfQ" placeholder="用你当时的原话问一遍" value="${esc(seed || "")}">
      </label>
      <label class="fl">你的裁定（这会直接作为答案展示给用户）
        <textarea id="cfA" rows="5" placeholder="按大白话写清楚：结论是什么、场上该怎么做。"></textarea>
      </label>
      <div class="row2">
        <label class="fl">相关条款号（可选，逗号分隔）
          <input id="cfC" placeholder="14.2, 10.1">
        </label>
        <label class="fl">同义问法（可选，逗号分隔）
          <input id="cfK" placeholder="得分后停下, 得停吗">
        </label>
      </div>
      <div class="cur-tip dim">
        条款号填了，回答里会带上可点击的原文引用（但会标明"人工核定"，不会伪装成官方条款）。
        同义问法很关键：用户不会用你的原话来提问，填了才能让同族问法也命中。
      </div>
      <div class="cur-acts" style="margin-top:14px">
        <button class="btn pri" id="cfOk">保存裁定</button>
        <button class="btn" id="cfNo">取消</button>
        <span id="cfMsg" class="dim"></span>
      </div>`;
    form.appendChild(f);
    f.querySelector("#cfQ").focus();
    f.querySelector("#cfNo").addEventListener("click", () => { form.innerHTML = ""; });
    f.querySelector("#cfOk").addEventListener("click", async () => {
      const q = f.querySelector("#cfQ").value.trim();
      const a = f.querySelector("#cfA").value.trim();
      const msg = f.querySelector("#cfMsg");
      if (!q || !a) { msg.textContent = "问题和答案都要填。"; return; }
      const split = v => v.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean);
      try {
        await post("/api/curated", {
          action: "add", question: q, answer: a,
          clauses: split(f.querySelector("#cfC").value),
          keywords: split(f.querySelector("#cfK").value),
        });
        form.innerHTML = "";
        await loadCurated(true);
        paint();     // 同上：不重画就看不到刚保存的条目
      } catch (e) { msg.textContent = "保存失败：" + e.message; }
    });
  };

  wrap.querySelector("#curNew").addEventListener("click", () => showForm(""));
  paint();
  mount(wrap);
  if (CUR.prefill) { const s = CUR.prefill; CUR.prefill = ""; showForm(s); }
  if (!CUR.loaded) loadCurated().then(paint);
}

/* ==========================================================================
   设置
   ========================================================================== */
/* 设置 = 所有非对话功能的唯一容器。
   ⚠️ 这里做的是**路由归并**，不是"再加一层壳"：
   规则原文/检索/人工裁定/召回测试/评测报告原来各自是一级路由，
   头像菜单里七项平铺 → 现在全部收进 settings/*，头像只剩三项。
   视图函数一个没删，只是换了调用入口 —— 少写一套页面，也少一处能漂移的副本。 */
function viewSettings(route) {
  const s = route.sub;
  if (s === "retrieval") return viewSetRetrieval();
  if (s === "answer") return viewSetAnswer();
  if (s === "about") return viewSetAbout();
  if (s === "help") return viewSetHelp();
  if (s === "model") return viewSetModel();
  if (s === "source") return viewKBConfig();
  // ⚠️ 旧 hash（rules / kb / search）**不在这里处理** —— routeNow 已把它们
  //    归一成一级页 `#/rules`（并停在对应 tab）。别在设置外壳里再画一份：
  //    查规则不是配置项，而目录里已经没有它，套进来会"当前页不在目录里"。
  if (s === "curated") return viewCurated();   // 人工裁定
  if (s === "test") return viewKBTest();       // 召回测试
  if (s === "overview") return viewOverview(); // 评测报告
  if (s === "feedback") return viewFeedback(); // 意见反馈（设置·常用，2026-10-17 起）
  if (s === "main") return viewSetMain();
  // 兜底：hash 被人手改成不存在的子页时，别给一片空白，回外观页。
  return viewSetMain();
}

/* 「外观与阅读」= 唯一需要为普通用户存在的设置页。
   它只回答一个问题：**哪些术语你想看？**
   字号、条款相关度、"我理解成…"改写、检索路径 —— 全部可关。
   开关走本地存储，改完立刻生效，不碰后端。 */
function viewSetMain() {
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="sec">
      <div class="sec-t">字号<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="seg" id="segSize">
          <button data-v="md" class="s1">标准</button>
          <button data-v="lg" class="s2">大</button>
        </div>
        <div class="note" style="margin-top:10px">回答里的正文、条款原文、设置项文字都会一起变大。手机上看字费劲就调大。</div>
        <div class="note" style="margin-top:6px">侧栏历史对话、顶栏与按钮不跟着变 —— 那两块是固定的导航骨架，放大它们只会挤掉内容。</div>
      </div></div>
    </div>

    <div class="sec">
      <div class="sec-t">要不要显示这些<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="opt" id="optTech">
          <div class="ov">
            <div class="on">显示打分细节与检索过程</div>
            <div class="od">每条依据右边的 0.873 这类数字、右栏底部可展开的
              「检索路径 / 命中类目 / 耗时」、以及写答案用的是哪个模型。
              <b>默认关闭</b>，关着不影响任何回答内容 ——
              这些是给要核对的人看的，日常用不着。</div>
          </div>
          <button class="sw" data-k="tech" role="switch" aria-checked="false" aria-label="显示技术细节"><i></i></button>
        </div>
        <div class="opt" id="optDense">
          <div class="ov">
            <div class="on">紧凑排版</div>
            <div class="od">把所有留白压小一点，一屏能看到更多对话内容。默认关闭。</div>
          </div>
          <button class="sw" data-k="dense" role="switch" aria-checked="false" aria-label="紧凑排版"><i></i></button>
        </div>
        <div class="note" style="margin-top:12px">「我理解成…」那一行属于第一类：它解释系统把你的大白话翻成了什么规则说法。
          不喜欢可以在下面关掉 —— 不过建议留着，它答错的时候能帮你看出它理解歪在哪。</div>
      </div></div>
    </div>

    <div class="sec">
      <div class="sec-t">怎么改<span class="ln"></span></div>
      <div class="card"><div class="card-b">
        <div class="note">这些开关只存在你自己的浏览器里，不会影响别人，也不需要联网。
          换设备或清了浏览器数据会回到默认。</div>
        <button class="btn sm" id="prefReset" style="margin-top:12px">恢复默认</button>
      </div></div>
    </div>`;

  // 字号分段
  const seg = wrap.querySelector("#segSize");
  const paintSeg = () => seg.querySelectorAll("button").forEach(b =>
    b.classList.toggle("on", b.dataset.v === PREF.size));
  seg.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    PREF.size = b.dataset.v; savePref(); applyPref(); paintSeg();
  }));
  paintSeg();

  // 开关（真button：可 Tab、可用空格/回车触发、读屏播报 on/off）
  const syncSw = () => wrap.querySelectorAll(".sw").forEach(x => {
    const on = !!PREF[x.dataset.k];
    x.classList.toggle("on", on);
    x.setAttribute("aria-checked", on ? "true" : "false");
  });
  wrap.querySelectorAll(".sw").forEach(sw => sw.addEventListener("click", () => {
    PREF[sw.dataset.k] = !PREF[sw.dataset.k]; savePref(); applyPref();
    syncSw();
  }));
  syncSw();

  wrap.querySelector("#prefReset").addEventListener("click", () => {
    PREF.size = "md"; PREF.tech = false; PREF.dense = false; PREF.devnav = false;
    savePref(); applyPref(); viewSetMain(); toast("已恢复默认");
  });

  mount(wrap);
}

/* 「怎么用」= 给不想读文档的人。全部用大白话，不出现一个产品内部术语。 */
function viewSetHelp() {
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="sec">
      <div class="sec-t">三步就能用起来<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="steps">
          <div class="stp"><b>1</b><div>
            <div class="sh">把场上的事直接说出来</div>
            <div class="sd">不用记条款号，也不用改成"规则语言"。就像跟人描述一样写：
              「起跳接盘的时候被对面撞了一下，没接住」「叫了犯规之后读秒从几开始」。</div></div></div>
          <div class="stp"><b>2</b><div>
            <div class="sh">看它给的结论和依据</div>
            <div class="sd">回答分几块：<b>结论</b>（谁对谁错）、<b>依据</b>（哪一条）、
              <b>该怎么做</b>（现场怎么办）。条款号比如 <span class="cite" style="cursor:default">14.2</span> 可以点开看原文。</div></div></div>
          <div class="stp"><b>3</b><div>
            <div class="sh">不同意就点「没用」</div>
            <div class="sd">选一个原因（条款引错了 / 明明有却说没有 / 没说清怎么做…），
              可以补一句话。这条反馈会直接决定它下一版改哪里。</div></div></div>
        </div>
      </div></div>
    </div>

    <div class="sec">
      <div class="sec-t">它做不到的事，先说清楚<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">规则里没写的，它会直说没写</div><div class="v">
          这是刻意的。给一条编出来的规定，比说"不知道"危险得多。
          <div class="sub">所以你也可以故意问它"飞盘多少钱一个"试试。</div></div></div>
        <div class="r"><div class="k">不问比赛规则的事</div><div class="v">
          它只答 WFDF 2025-2028 规则范围内的问题。训练战术、装备、其他项目的规则都不管。
          <div class="sub">国内赛制（大学赛、俱乐部联赛的特殊规定）要看各自赛事规程。</div></div></div>
        <div class="r"><div class="k">答案永远要给条款出处</div><div class="v">
          如果它说的情况规则里真没有，那它会告诉你没有，而不是拼几条相近的条款蒙你。
          <div class="sub">看不懂依据的时候，点条款号看原文，那才是真的。</div></div></div>
      </div></div></div>
    </div>

    <div class="sec">
      <div class="sec-t">快捷键<div class="ln"></div></div>
      <div class="card"><table class="tbl">
        <thead><tr><th style="width:200px">按键</th><th>做什么</th></tr></thead>
        <tbody>
          <tr><td class="mono">Enter</td><td>发送</td></tr>
          <tr><td class="mono">Shift + Enter</td><td>换行</td></tr>
          <tr><td class="mono">Cmd / Ctrl + K</td><td>开一个新对话</td></tr>
          <tr><td class="mono">点击条款号</td><td>右栏展开那条规则原文</td></tr>
          <tr><td class="mono">左下角头像</td><td>规则原文、规则检索、设置、这些都在里面</td></tr>
        </tbody>
      </table></div>
    </div>

    <div class="sec">
      <div class="sec-t">其他入口<div class="ln"></div></div>
      <div class="megrid" id="helpGrid"></div>
    </div>`;

  const g = wrap.querySelector("#helpGrid");
  [["settings/search", "规则检索", "知道大概哪一条时，直接搜原文"],
   ["settings/kb", "规则原文", "368 条规则 + 附录，按章节翻"],
   ["settings/test", "召回测试", "只测检索层，不生成回答"],
   ["settings/curated", "人工裁定", "把答错的地方补上正确答案"],
   ["settings/overview", "评测报告", "447 个真实问法的实测数字"],
  ].forEach(([p, t, d]) => {
    const b = el("button", "meitem");
    b.innerHTML = `<div class="mt2">${esc(t)}</div><div class="md2">${esc(d)}</div>`;
    b.addEventListener("click", () => go(p));
    g.appendChild(b);
  });

  mount(wrap);
}

/* 意见反馈页。
   ⚠️ 它必须和「有用/没用」是**同一个池子**，否则反馈会散在两个地方，
   统计出来的满意率就没意义了。
   所以走同一个 /api/feedback 接口，只是 rating 用 "note"、原因记为 kind=note；
   后端统计会跳过 note —— 一句"界面能不能小一点"不该被算成差评。 */
function viewFeedback() {
  const st = FB.stats || {};   // /api/feedback 返回 {reasons, stats}，loadFeedback 已取出 stats 这一层
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="sec">
      <div class="sec-t">说一句话<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="fbKindWrap">
          <select id="fbKind" class="inp">
            <option value="answer">某条回答答得不对</option>
            <option value="clause">引的条款不对</option>
            <option value="missing">规则里有，但它说没有</option>
            <option value="ui">界面不好用</option>
            <option value="feature">想要某个功能</option>
            <option value="other">别的</option>
          </select>
        </div>
        <textarea id="fbText" class="inp" rows="5" style="width:100%;margin-top:10px;resize:vertical"
          placeholder="比如：问「接盘被撞算不算违例」，它引了 17.2.1，但没说清我该做什么。"></textarea>
        <div class="fbSendRow">
          <button class="btn pri" id="fbSend">发出去</button>
          <span id="fbTip" style="font-size:12px;color:var(--ink-3)"></span>
        </div>
        <div class="note" style="margin-top:14px">每条反馈会连同你问的原问题一起记下来，
          这样才知道它是在哪一步理解歪的 —— 只记"答得不对"是查不出原因的。</div>
      </div></div>
    </div>

    <div class="sec">
      <div class="sec-t">已经收到的<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">评分反馈</div><div class="v num">${st.total ?? 0} 条
          <div class="sub">有用 ${st.up ?? 0} · 没用 ${st.down ?? 0}${st.notes ? ` · 意见 ${st.notes}` : ""}</div></div></div>
        <div class="r"><div class="k">最近一条</div><div class="v">
          ${st.recent && st.recent.length ? esc(st.recent[0].q || "").slice(0, 40) : "—"}
          <div class="sub">${st.recent && st.recent.length ? esc((st.recent[0].ts || "").replace("T", " ").slice(0, 16)) : ""}</div></div></div>
      </div></div></div>
    </div>`;

  const tip = wrap.querySelector("#fbTip");
  wrap.querySelector("#fbSend").addEventListener("click", async () => {
    const q = wrap.querySelector("#fbText").value.trim();
    if (!q) { tip.textContent = "先写一句"; return; }
    const kind = wrap.querySelector("#fbKind").value;
    // 用当前会话最近一条作为上下文；没有会话就只记这一句。
    const last = (CH.sessions.find(s => s.id === CH.current) || {}).msgs || [];
    // ⚠️ 两个字段名都得对（2026-10-07 修）：消息存的是 role:"user" / content，
    //   这里写的是 "u" / .text ⇒ filter 恒空、lastQ 恒 undefined，
    //   于是每条意见反馈的 q 都是那句占位符 —— 这一页承诺的
    //   「连同你问的原问题一起记下来」等于没有，事后无法定位是哪一步理解歪了。
    const lastQ = last.filter(m => m.role === "user").slice(-1)[0];
    try {
      await post("/api/feedback", {
        rating: "note", kind, comment: q,
        q: lastQ ? lastQ.content : "（来自意见反馈页）",
        answer: "", citations: [],
      });
      wrap.querySelector("#fbText").value = "";
      tip.textContent = "收到了，谢谢";
      loadFeedback().then(() => { if (parseHash().sub === "feedback") routeNow(); });
    } catch (e) {
      tip.textContent = "没发出去：" + (e && e.message || e);
    }
  });
  mount(wrap);
}

/* ============================ 弹窗 ============================
   自绘遮罩弹窗，不用 Electron 的 dialog.showMessageBox：
   那个在 Windows 上是灰色系统框，和整套浅色 UI 打架，
   而且标题栏按钮会跑到右上角、盖住我们自己的菜单。
   官方 DeepSeek Harness 也是自己画（DesktopUpdateDialog + 遮罩层）。
   用法：openDlg({kind, title, body, okText, onOk})。 */
function openDlg(o) {
  const m = el("div", "dlg-mask");
  m.innerHTML = `
    <div class="dlg" role="dialog" aria-modal="true">
      <button class="dlg-x" aria-label="关闭">✕</button>
      <div class="dlg-t">${esc(o.title || "")}</div>
      <div class="dlg-b">${o.body || ""}</div>
      <div class="dlg-f">
        ${o.cancelText ? `<button class="btn" id="dlgCancel">${esc(o.cancelText)}</button>` : ""}
        <button class="btn pri" id="dlgOk">${esc(o.okText || "确定")}</button>
      </div>
    </div>`;
  /* ⚠️ onEsc 必须先于 close 声明：close 里要用 removeEventListener(onEsc)，
     而 close 是 const 箭头函数、在定义它的位置就已经可能被调用（✕ 的点击处理器
     紧接着就挂上了）。若把 const onEsc 写在 close 后面，就形成 TDZ ——
     语法能过、真点一下就抛 ReferenceError。 */
  const prevFocus = document.activeElement;
  const onEsc = (e) => {
    if (e.key === "Escape" && o.dismissable !== false) closeAsCancel();
  };
  const close = () => {
    m.remove();
    // ⚠️ 监听器必须在**所有**关闭路径上摘掉，不能只写在 Esc 分支里
    //（2026-10-07 修）。原来只有 Esc 那条remove，其余四条
    //   （确定 / ✕ / 稍后 / 点遮罩）全把监听器留在 document 上：
    //   · dismissable:false 的两张弹窗（正在检查 / 正在更新）Esc 条件永不成立
    //     ⇒ 监听器连被摘掉的可能都没有，每点一次「检查更新」泄漏一个；
    //   · 更糟的是副作用会重放 —— 已关闭弹窗的 onCancel 会在用户下次按 Esc 时
    //     **再执行一次**，等于又给主进程发一次 update-dismissed。
    document.removeEventListener("keydown", onEsc);
    // 关闭后把焦点还给触发它的元素：否则焦点掉到 <body>，键盘用户丢失位置。
    if (prevFocus && prevFocus.focus) { try { prevFocus.focus(); } catch (e) { /* 已移除 */ } }
    o.onClose && o.onClose();
  };
  /* ⚠️ 「不是点确定」的关闭方式要走 onCancel（2026-10-07 加）。
     为什么要区分：更新弹窗里「稍后」「✕」「Esc」是**同一个动作** ——
     "等一下再更新"。而 `close()` 是三条路共用的出口，
     所以 onCancel 必须挂在 close 的**所有非确定分支**上，
     只挂在「稍后」按钮上是不够的（用户按 Esc 也是"稍后"）。
     漏掉的后果很具体：主进程以为用户还没处理 ⇒ 切一下窗口又弹一次
     ⇒ 「稍后」变成了骚扰，正好和 owner 要的相反。 */
  const closeAsCancel = () => { close(); o.onCancel && o.onCancel(); };
  m.querySelector(".dlg-x").addEventListener("click", closeAsCancel);
  m.querySelector("#dlgOk").addEventListener("click", () => { close(); o.onOk && o.onOk(); });
  // 有明确语义的第二按钮：更新弹窗里是「稍后」，不是「取消」——
  // 同一个动作换个说法，用户对结果的预期完全不同。
  if (o.cancelText) m.querySelector("#dlgCancel").addEventListener("click", closeAsCancel);
  m.addEventListener("click", (e) => { if (e.target === m && o.dismissable !== false) closeAsCancel(); });
  document.addEventListener("keydown", onEsc);
  document.body.appendChild(m);
  m.querySelector("#dlgOk").focus();
  return m;
}

/* 标题栏「应用」菜单的两条：关于 / 检查更新。
   主进程只负责发消息，弹窗由页面画 —— 这样它和界面是同一套视觉，
   也才有"背景变暗 + 右上角✕ + 确定按钮"这个形态。 */
function bindAppMenu() {
  const W = window.__shultWin;
  if (!W || typeof W.popupMenu !== "function") return;
  const pop = (btn, name) => btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const r = btn.getBoundingClientRect();
    W.popupMenu(name, r.left, r.bottom + 2);
  });
  pop($("mApp"), "application");
  pop($("mEdit"), "edit");

  W.onAppDialog(({ kind, version, name }) => {
    if (kind === "about") {
      openDlg({
        kind: "about",
        title: `关于 ${name || "Shultimate"}`,
        body: `<div class="dlg-ver">版本 ${esc(version || "—")}</div>
          <p>飞盘规则助手。回答只依据 WFDF 2025-2028 规则原文，
          规则里没写的会直说没写，不编条款。</p>`,
        okText: "确定",
      });
    } else if (kind === "update") {
      // 只给一个"正在检查"的回执；真实结论由 win-update 事件送来。
      // ⚠️ 别在这里写死"已是最新版本"—— 那是没接更新通道时的占位话，
      // 一旦写死，用户点了菜单永远看不到真实版本变化，功能等于不存在。
      // ⚠️ 这张弹窗必须记进 UPD.checking：结果回来时它要被换成真弹窗，
      // 不记的话「正在检查…」会一直留在屏幕上盖住真结果。
      UPD.checking = openDlg({
        kind: "update-checking",
        title: "检查更新",
        body: `<div class="dlg-busy">正在检查…</div>`,
        okText: "知道了",
        dismissable: false,
      });
    }
  });

  bindUpdateFlow();
}

/* 更新通道的共享状态。只放"当前开着哪张更新弹窗"这一件事 ——
   更新是一个线性流程（检查 → 发现 → 下载 → 安装），
   任何时刻只有一张更新弹窗在屏幕上，靠这几个引用就能保证不叠。 */
/* downloading：下载/安装进行中。用来挡"又来一个 found:true 把进度弹窗拆掉"。 */
const UPD = { checking: null, live: null, downloading: false };

/* ============================ 自动更新 ============================
   目标体验对标 DeepSeek 客户端：用户点开原来的图标 → 有新版时弹一句「有新版本」
   → 点「立即更新」→ 进度条 → 装完自动重开。**不需要去官网下载新文件。**

   ⚠️ 这条链路上的三种静默失败都踩过：
     ① 更新事件只在客户端态存在 —— 网页版没有 __shultWin，必须整段跳过，
        否则设置页会报 undefined。
     ②「已是最新」和「查不到更新」不是一回事：断网时不能报「已是最新」，
        那是在骗用户说他的版本是最新的。必须区分。
     ③ 免安装版没有自动替换能力（那个 exe 就是程序本身，Windows 锁着它）。
        对它要说清「需要装一次安装版」，而不是假装能原地更新。 */
function bindUpdateFlow() {
  const W = window.__shultWin;
  if (!W || typeof W.onUpdate !== "function") return;

  const fmtSize = (n) => {
    if (!n && n !== 0) return "";
    return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.round(n / 1024) + " KB";
  };

  // 关掉所有更新流程的弹窗。「正在检查」和结果弹窗必须一起关 ——
  // 只关结果的话，"正在检查…" 会孤零零留在屏幕上。
  const closeLive = () => {
    if (UPD.live) { UPD.live.remove(); UPD.live = null; }
    if (UPD.checking) { UPD.checking.remove(); UPD.checking = null; }
  };

  function showUpdateFound(info) {
    // ⚠️ 下载/安装进行中**不要**被"有新版本"打断（2026-10-07 修）。
    //   77MB 要下一两分钟，这期间用户必然切窗口；而主进程那会儿
    //   版本号还没变、服务器仍报 available ⇒ 又推来一个 found:true
    //   ⇒ 这里若无脑 closeLive()，**「正在下载 43%」的进度弹窗会被拆掉**
    //   换成「有新版本」：下载在后台继续，界面却在骗人。
    if (UPD.downloading) return;
    closeLive();
    const portableNote = info.portable
      ? `<p class="dlg-warn">你用的是免安装版，那个文件就是程序本身，没法原地替换。
          这次会装上正式版（会多一个桌面图标），以后点那个图标就能自动更新。</p>`
      : "";
    UPD.live = openDlg({
      kind: "update-available",
      title: "有新版本",
      body: `
        <div class="dlg-ver">${esc(info.current)} → ${esc(info.latest)}</div>
        ${info.notes ? `<div class="dlg-notes">${esc(info.notes).replace(/\n/g, "<br>")}</div>` : ""}
        <p>下载 ${fmtSize(info.installer && info.installer.size)}，下载完自动安装并重开。
          <span class="dim">中途关掉也不会丢，下次打开会接着下。</span></p>
        ${portableNote}`,
      okText: "立即更新",
      cancelText: "稍后",
      onOk: () => { UPD.live = null; W.updateStarted && W.updateStarted(); startDownload(info); },
      /* 「稍后」= 等一下再更新，不是"永远别提"。
         这条回执让主进程知道"他处理过了"，于是下一次切窗口/30 分钟
         到点时会**再问一次**（他可能错过了），但不会在同一次打扰里反复弹。*/
      onCancel: () => { UPD.live = null; W.updateHandled && W.updateHandled(); },
    });
  }

  function startDownload(info) {
    // ⚠️ 全程置位、结束才清（失败/取消都要清）。见 UPD.downloading 的用途：
    //   这段时间里主进程随时可能又推来 found:true（版本号没变），
    //   showUpdateFound 靠它拒绝拆掉进度弹窗。
    UPD.downloading = true;
    const endDownload = () => { UPD.downloading = false; };
    // ⚠️ 必须提前说清"下一步会发生什么"：安装那一刻客户端会自己退出，
    //    不说的话用户把它当闪退（2026-10-06 owner 报的就是这个观感）。
    //    ⚠️ 注意 `.dlg-x` 在 openDlg 里是无条件加的，dismissable:false 只挡
    //    遮罩点击和 Esc —— 所以这里**是有个 ✕ 的**。关掉不等于下载停了：
    //    下载在主进程继续，未完成的分片留在 .part，下次会续传。
    const tail = info && info.portable
      ? "下载完成后会启动安装程序；装好新版会自动打开（桌面会多一个图标）。"
      : "下载完成后会自动安装并重新打开，这段期间请别关掉客户端。";
    UPD.live = openDlg({
      kind: "update-download",
      title: "正在更新",
      body: `
        <div class="dlg-ver" id="upPct">0%</div>
        <div class="dlg-bar"><div class="dlg-bar-fill" id="upBar"></div></div>
        <div class="dlg-sub" id="upSub">准备下载…</div>
        <p class="dim">${tail}</p>`,
      okText: "在后台继续",
      dismissable: false,
      onOk: () => { UPD.live = null; },
    });
    W.startUpdate(info).then(() => endDownload()).catch((e) => {
      endDownload();
      closeLive();
      const m = openDlg({
        kind: "update-failed",
        title: "更新没成功",
        body: `<p>${esc(String(e && e.message || e))}</p>
          <p>当前版本仍可正常使用，不影响提问。</p>`,
        okText: "知道了",
        cancelText: "手动下载",
      });
      const c = m.querySelector("#dlgCancel");
      if (c) c.addEventListener("click", () => W.openUpdateFile(info));
    });
  }

  function showResult(state) {
    // 只有用户主动点的「检查更新」才在没结果时说话；启动时的静默检查不弹。
    const cur = state.current || "—";
    if (state.state === "up-to-date") {
      closeLive();
      openDlg({
        kind: "update-latest",
        title: "检查更新",
        body: `<div class="dlg-ver">已是最新版本</div>
          <p>当前版本 ${esc(cur)}。</p>`,
        okText: "确定",
      });
      return;
    }
    if (state.state === "offline") {
      closeLive();
      openDlg({
        kind: "update-offline",
        title: "检查更新",
        // ⚠️ 不能写"已是最新"。连不上只能说连不上 ——
        // 把"查不到"说成"最新"，用户就不会再试了。
        body: `<p>连不上服务器，没能确认有没有新版本。
          当前版本 ${esc(cur)}，可以正常用。</p>
          <p class="dim">稍后再点一次「检查更新」就行。</p>`,
        okText: "确定",
      });
      return;
    }
    if (state.state === "no-manifest") {
      closeLive();
      openDlg({
        kind: "update-none",
        title: "检查更新",
        body: `<p>服务器上还没有发布过更新包。当前版本 ${esc(cur)}。</p>`,
        okText: "确定",
      });
      return;
    }
    if (state.state === "disabled") {
      closeLive();
      openDlg({
        kind: "update-disabled",
        title: "检查更新",
        body: `<p>当前运行的是开发版本，不参与更新检查。版本 ${esc(cur)}。</p>`,
        okText: "确定",
      });
    }
  }

  W.onUpdate((s) => {
    if (!s) return;
    if (s.found) { showUpdateFound(s); return; }
    // ⚠️ 字段名必须和主进程发的一致：updater.js 的 onStage 回的是
    // { stage, received, total, percent }，**没有 state 字段**。
    // 早先这里写成 s.state === "download"，主进程发的每一个进度事件都被忽略，
    // 表现是**弹窗正常、百分比永远 0%、进度条不动**——
    // 而下载其实在进行，用户会以为卡死了还去点别的地方。
    // 这类"字段名对不上"没有报错，只能靠真下载 + 连采多帧测出来。
    if (s.stage === "download") {
      const pct = Math.round((s.percent || 0) * 100);
      const bar = document.getElementById("upBar");
      const pt = document.getElementById("upPct");
      const sub = document.getElementById("upSub");
      if (bar) bar.style.width = pct + "%";
      if (pt) pt.textContent = pct + "%";
      if (sub) sub.textContent = s.total
        ? `${fmtSize(s.received)} / ${fmtSize(s.total)}`
        : `${fmtSize(s.received)}`;
      return;
    }
    if (s.stage === "install") {
      const pt = document.getElementById("upPct");
      const sub = document.getElementById("upSub");
      if (pt) pt.textContent = "";
      if (sub) sub.textContent = "下载完成，正在安装…";
      return;
    }
    if (s.state === "checking") return;   // 已有"正在检查"弹窗，不重复
    if (s.state === "installed") { closeLive(); return; }
    showResult(s);
  });
}

function viewSetModel() {
  const s = AS.settings || {};
  const g = s.generation || {};
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="sec">
      <div class="sec-t">生成模型链<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="note">按顺序尝试，前一个失败（欠费 / 超时 / 报错）自动切下一个。
          每条回答下方都会标注<b>实际用的是哪个模型</b>，不会静默替换。</div>
        <div class="kv" style="margin-top:14px">
          <div class="r"><div class="k">可用性</div><div class="v">
            <span class="badge ${g.configured ? "ok" : "gray"}"><i></i>${g.configured ? "已配置" : "未配置 Key"}</span>
            <div class="sub">${esc(g.describe || "—")}</div></div></div>
          <div class="r"><div class="k">兜底链</div><div class="v">${esc((g.chain || []).join(" → ") || "—")}
            <div class="sub">主链路：DeepSeek。兜底：智谱。切换后端只需改 <span class="mono">LLM_PROVIDER_CHAIN</span>。</div></div></div>
          <div class="r"><div class="k">deepseek</div><div class="v mono">${esc((g.models && g.models.deepseek) || "—")}</div></div>
          <div class="r"><div class="k">zhipu</div><div class="v mono">${esc((g.models && g.models.zhipu) || "—")}</div></div>
        </div>
      </div></div>
    </div>

    <div class="sec">
      <div class="sec-t">生成参数<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">回答 temperature</div><div class="v">${(g.temperature && g.temperature.answer) ?? "—"}
          <div class="sub">刻意压低：规则问答要稳，不要发挥。</div></div></div>
        <div class="r"><div class="k">回答 max_tokens</div><div class="v">${(g.max_tokens && g.max_tokens.answer) ?? "—"}</div></div>
        <div class="r"><div class="k">改写 temperature</div><div class="v">${(g.temperature && g.temperature.rewrite) ?? "—"}
          <div class="sub">改写器负责把口语翻成规则术语，进一步压低随机性。</div></div></div>
        <div class="r"><div class="k">改写 max_tokens</div><div class="v">${(g.max_tokens && g.max_tokens.rewrite) ?? "—"}</div></div>
      </div></div></div>
    </div>

    <div class="sec">
      <div class="sec-t">系统提示词的两条铁律<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="kv">
          <div class="r"><div class="k">① 无裁判</div><div class="v">极限飞盘由场上队员自己执法
            <div class="sub">提示词里明令禁止出现「裁判」「判罚」「处罚」这类说法 —— 规则里根本没有。正确说法是「示意（犯规/违例/违规）」「比赛暂停」「验盘」「重新开始比赛」。</div></div></div>
          <div class="r"><div class="k">② 不给行号</div><div class="v">给模型看的条款列表**不带行号**
            <div class="sub">实测：一旦给了 [1][2]，模型会把行号当条款号写进回答（出现过「构成间接犯规 (5)」）。只用条款号做标题，模型就无从混淆。</div></div></div>
        </div>
      </div></div>
    </div>`;
  mount(wrap);
}

function viewSetRetrieval() {
  const s = AS.settings || {};
  const rows = s.retrieval || [];
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="note warn" style="margin-bottom:16px">
      <b>这些参数是只读的，这不是偷懒。</b>
      <span style="display:block;margin-top:5px">
      尤其是「类目命中加分」，它是扫描 0.20 ~ 0.50 全区间后定在 0.30 的 ——
      正解的原始余弦常常只有 0.740，而同题的通用原则高达 0.990，是靠这个常数把正解抬到能同台竞争的位置。
      把它做成滑块，只会让一个「扫过参才定住」的值被随手调坏。所以这里把它们连同取值依据一起摊开。</span>
    </div>
    <div class="card"><table class="tbl">
      <thead><tr><th>参数</th><th class="r" style="width:78px">取值</th><th>说明与取值依据</th></tr></thead>
      <tbody id="prmRows"></tbody>
    </table></div>`;
  const tb = wrap.querySelector("#prmRows");
  rows.forEach(p => {
    const tr = el("tr");
    tr.innerHTML = `<td><b>${esc(p.name)}</b><div class="mono" style="color:var(--ink-4);font-size:11px">${esc(p.key)}</div></td>
      <td class="r mono"><b>${esc(String(p.value))}</b></td>
      <td style="color:var(--ink-2);font-size:12.5px">${esc(p.note)}</td>`;
    tb.appendChild(tr);
  });
  mount(wrap);
}

function viewSetAnswer() {
  const wrap = el("div", "wrap");
  const list = AS.assistants || [];
  wrap.innerHTML = `
    <div class="note" style="margin-bottom:16px">
      换助手只改变<b>讲法</b> —— 说多详细、先给结论还是先解释。
      <b>不会</b>改变它守的底线：飞盘没有裁判、依据不够就必须说不确定。
      选好之后，在对话页左栏顶部随时能换。
    </div>
    <div class="card"><table class="tbl">
      <thead><tr><th>助手</th><th>什么时候用</th><th style="width:96px">翻几条</th></tr></thead>
      <tbody id="astRows"></tbody>
    </table></div>`;
  const tb = wrap.querySelector("#astRows");
  list.forEach(a => {
    const tr = el("tr");
    if (a.id === AS.assistantId) tr.className = "on";
    tr.style.cursor = "pointer";
    tr.innerHTML = `<td><b>${esc(a.name)}</b>${a.id === AS.assistantId ? ' <span class="tag">正在用</span>' : ""}</td>
      <td style="color:var(--ink-2);font-size:12.5px">${esc(a.desc)}
        <div class="sub" style="color:var(--ink-4);margin-top:3px">开场白：${esc(a.opener)}</div></td>
      <td class="num">${a.top_n ? a.top_n + " 条" : "默认"}</td>`;
    tr.addEventListener("click", () => {
      AS.assistantId = a.id;
      try { localStorage.setItem("frisbee.rag.assistant", a.id); } catch (e) { }
      $("astName").textContent = ast().name;
      if (parseHash().main === "chat") paintChat();
      viewSetAnswer();
      toast("已切到「" + ast().name + "」");
    });
    tb.appendChild(tr);
  });
  mount(wrap);
}

function viewSetAbout() {
  const s = AS.settings || {};
  const app = s.app || {};
  const rt = s.runtime || {};
  const wrap = el("div", "wrap");
  wrap.innerHTML = `
    <div class="sec">
      <div class="sec-t">关于<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">名称</div><div class="v">${esc(app.name || "飞盘规则助手")} · v${esc(app.version || "—")}</div></div>
        <div class="r"><div class="k">定位</div><div class="v">${esc(app.desc || "")}
          <div class="sub">给中国高校与社群的盘友用：有规则疑问时，用自己的话问明白、核对依据。</div></div></div>
        <div class="r"><div class="k">它和老系统的区别</div><div class="v">不是"从规则库捞最像的条文"，而是
          <b>听懂问题 → 检索 → 组织人话回答</b>
          <div class="sub">第一优先级不是"答得多"，而是"不确定就说不确定"——绝不编造条款。</div></div></div>
      </div></div></div>
    </div>
    <div class="sec">
      <div class="sec-t">技术栈<div class="ln"></div></div>
      <div class="card"><div class="card-b"><div class="kv">
        <div class="r"><div class="k">依赖</div><div class="v">零第三方依赖
          <div class="sub">Python 标准库 http.server + 手写前端，无需 pip install / npm install，clone 即跑。</div></div></div>
        <div class="r"><div class="k">检索</div><div class="v">语义向量（主）+ 字词法（辅）+ 规则类目必检
          <div class="sub">类目必检：用字面线索判断"这题属于哪类情形"，把该条款族强制拉进候选，不再听凭余弦排名。</div></div></div>
        <div class="r"><div class="k">生成</div><div class="v">DeepSeek（主）→ 智谱（兜底）
          <div class="sub">每次回答标注实际使用的模型，不静默替换。</div></div></div>
        <div class="r"><div class="k">运行地址</div><div class="v mono">${esc(rt.host || "")}:${rt.port ?? ""}
          <div class="sub">语块 ${rt.chunks ?? "—"} 个</div></div></div>
      </div></div></div>
    </div>
    <div class="sec">
      <div class="sec-t">语料核验<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="note">官方英文原文 368 条带编号条款，与中文语料<b>逐章一致</b>（20 章条数为
          43/7/4/8/4/6/16/18/17/22/18/18/39/6/18/12/33/52/17/10），
          连 10 条五段编号（如 18.1.1.1.1）都逐一对得上 —— <b>不存在漏规则</b>。</div>
      </div></div>
    </div>
    <div class="sec">
      <div class="sec-t">桌面版<div class="ln"></div></div>
      <div class="card"><div class="card-b">
        <div class="note">如果你是从桌面图标打开的，那个图标只是一个"干净窗口"。
          <b>界面和内容每次都从服务器取最新的</b>，不用更新也是新的；
          只有那个窗口本身偶尔需要换一次。</div>
        <div class="upd-row">
          <button class="btn" type="button" id="updCheck">检查窗口版本</button>
          <span class="upd-tip" id="updTip"></span>
        </div>
        <div id="updBox"></div>
      </div></div>
    </div>`;
  mount(wrap);
  bindUpdateCheck();
}

/* 「关于 → 桌面版」里的那个检查按钮（2026-10-16 加）。
 *
 * ⚠️ 为什么加它 —— 起因是 owner 的一个真实处境：
 *   他已经把 0.3.0 的安装包发给别人了，而 0.3.0 有个 bug：
 *   **机器上没装 Chrome / Edge 的用户，永远走不到自动更新检查那一步**
 *   （`main()` 里"退回系统默认浏览器"之后直接 `return` 了）。
 *   ⇒ 那类用户会**永远停在 0.3.0**，而且他没法再发一次安装包。
 *
 * ⚠️ 为什么加在这里能救回来：
 *   这一段是**网页**的一部分，而网页是**每次打开都从服务器实时拉的** ——
 *   所以**连 0.3.0 的用户也会看到它**。这是唯一能绕过"旧启动器不检查更新"
 *   的通道。（实测确认过：0.3.0 的窗口能正常打开当前网页。）
 *
 * ⚠️ 为什么不让它自动弹：
 *   大多数用户不需要更新（内容本来就是新的）。**只在用户主动问的时候告诉他**，
 *   比每次打开都提醒一次更符合这个产品的调性（"不打扰"）。
 *   `bl.downloads` 只在带 `?download=1` 时才有 —— 这条链接从来不带，
 *   所以正常情况下不会误报"有新版本"。
 */
function bindUpdateCheck() {
  const btn = $("updCheck");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const tip = $("updTip"), box = $("updBox");
    btn.disabled = true;
    if (tip) tip.textContent = "查一下…";
    if (box) box.innerHTML = "";
    try {
      const m = await api("/api/update");
      const l = (m && m.launcher) || {};
      const ver = l.version || "";
      const name = l.name || "";
      if (!ver) {
        if (tip) tip.textContent = "服务器上暂时查不到版本信息。";
        return;
      }
      if (tip) tip.textContent = "";
      if (box) {
        box.innerHTML =
          '<div class="note">服务器上当前的窗口版本：<b>v' + esc(ver) + "</b>。" +
          "如果你打开的这个窗口更旧，点下面这个链接下载新的，" +
          "双击装一下就好（会覆盖旧的，历史对话不会丢）。</div>" +
          '<div class="upd-row"><a class="btn" href="' + esc(BASE_DL + name) +
          '" download>下载 v' + esc(ver) + "</a></div>";
      }
    } catch (e) {
      if (tip) tip.textContent = "没查到：" + ((e && e.message) || "网络问题");
    } finally {
      btn.disabled = false;
    }
  });
}

/* 下载用的基地址 —— 和服务地址同源。 */
const BASE_DL = location.origin + "/dl/";

/* ==========================================================================
   对话
   ========================================================================== */
const CK = "frisbee.rag.v1";

function loadSessions() {
  try {
    const raw = localStorage.getItem(CK) || "";
    if (raw) {
      const d = JSON.parse(raw);
      // 2026-10-24 R2：JSON 可解析 ≠ schema 正确。根/sessions 不是数组时
      // 不能当空历史继续跑（下一次 saveSessions 会写 []）。
      if (!d || typeof d !== "object" || Array.isArray(d)
          || !Array.isArray(d.sessions)
          || d.sessions.some(s => !s || typeof s !== "object" || Array.isArray(s))) {
        throw new Error("sessions schema");
      }
      CH.sessions = d.sessions;
      CH.current = typeof d.current === "string" ? d.current : null;
    }
    CH.lastRaw = raw;
    CH.storageBroken = false;
  } catch (e) {
    // 同 loadNotes：解析失败绝不能"清空后继续跑"，下一次 saveSessions()
    // 就会把全部历史覆盖掉。保留原文并停止写入，等用户处理。
    console.error("[storage] 会话解析失败，已停止写入以免覆盖原始数据", e);
    CH.storageBroken = true;
    CH.recovery = localStorage.getItem(CK) || "";
    CH.sessions = CH.sessions || [];
    CH.current = CH.current || null;
    if (!CH.sessions.length) CH.sessions = [newSession()];
    return;
  }
  if (!CH.sessions.length) CH.sessions = [newSession()];
  if (!CH.sessions.find(s => s.id === CH.current)) CH.current = CH.sessions[0].id;
}
function saveSessions() {
  // storageBroken 时**不写**：宁可让新会话只活在内存里（刷新即丢 visibly），
  // 也不要用空数据覆盖掉用户还没来得及抢救的历史。
  try {
    if (CH.storageBroken) return false;
    const val = JSON.stringify({ sessions: CH.sessions, current: CH.current });
    const cur = localStorage.getItem(CK) || "";
    // 2026-10-24 R2：最低限度冲突检测 —— 另一个窗口改过就拒绝静默覆盖。
    if (CH.lastRaw !== null && cur !== CH.lastRaw) {
      CH.conflict = true;
      toast("另一个窗口也改了历史；本次没有覆盖它。请先导出/关闭另一个窗口。");
      return false;
    }
    if (!storeSet(CK, val)) return false;
    CH.lastRaw = val;
    return true;
  } catch (e) { return false; }
}
function newSession() { return { id: uid(), title: "新对话", ts: Date.now(), msgs: [] }; }
function curS() { return CH.sessions.find(s => s.id === CH.current) || null; }
function ast() { return AS.assistants.find(a => a.id === AS.assistantId) || AS.assistants[0] || { name: "助手", opener: "" }; }

/* ==========================================================================
   我的笔记
   ==========================================================================
   用户的私人草稿本：记自己的理解、队里的约定、上回争论的结论。

   ⭐ 只存本机 localStorage —— 不落服务器。笔记是私人的（可能写"我老是记不住"
   这种话），也不该混进 feedback.jsonl 那种会被统计的口径里。
   ⭐ 和「来源与引用」共用右侧一个槽位（NT.view 切换），不并排两栏：
   侧栏 240 + 引用 326 + 笔记 326 会把对话正文挤到没法读。
   ⭐ 开合只有一处真相 = NT.open + .hide 类，见 syncRail()。
      NT.open 为 null 表示"跟随内容"（有消息才自动出现）—— 用户手动开关过
      之后就锁定他的选择，不再自作主张。 */

/* ==========================================================================
   「撤销」浮条：底部浮起 + 右侧一个动作按钮，5 秒后自己退场。
   —— 这是一条**可撤销的操作**该有的样子。参照 Gmail / Notion 的撤销条：
   永远比"你确定吗？"对话框好，因为**对话框逼你先做决定，撤销条让你先做事**。
   真删错了一句话捞回来；真删对了，不用管它自己消失。 */
let _undoTimer = null;
function undoToast(msg, actionLabel, onAction) {
  let u = $("undoBar");
  if (!u) {
    u = el("div", "undo-bar"); u.id = "undoBar";
    document.body.appendChild(u);
  }
  u.innerHTML = "";
  const s = el("span", "undo-t"); s.textContent = msg;
  const b = el("button", "undo-b"); b.textContent = actionLabel || "撤回";
  b.addEventListener("click", () => {
    onAction && onAction();
    u.classList.remove("on");
    clearTimeout(_undoTimer);
  });
  u.appendChild(s); u.appendChild(b);
  /* 二次入场：先摘动画类、强制回流、再挂上 —— 否则连续删两次没有动效 */
  u.classList.remove("on");
  void u.offsetWidth;
  u.classList.add("on");
  clearTimeout(_undoTimer);
  _undoTimer = setTimeout(() => u.classList.remove("on"), 5000);
}

/* ==========================================================================
   导出 / 导入：笔记是**用户自己攒的东西**，不能被浏览器一次清缓存带走。
   导出成**纯文本 .txt** 而不是 jsonl —— 用户要能自己看懂、自己留档、能
   发给队友。导入按「标题 + 正文」去重合并，不会覆盖已有的。
   ⚠️ 纯本地文件操作（Blob + FileReader），不碰网络 —— 守住 test_notes_panel
      那条「笔记代码里不许有网络调用」的守卫。*/
function downloadText(text, filename) {
  try {
    const blob = new Blob([String(text || "")], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename || "shultimate-recovery.txt";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast("已导出原始数据；请先另存，再决定是否重置");
  } catch (e) { toast("导出失败，请复制浏览器控制台里的 NT.recovery / RP.recovery"); }
}
function exportNotes() {
  const live = liveNotes();
  if (!live.length) { toast("还没有笔记可以导出"); return; }
  const lines = ["我的飞盘规则笔记",
    `导出于 ${new Date().toLocaleString("zh-CN")} · 共 ${live.length} 条`, ""];
  live.forEach((n, i) => {
    lines.push(`${i + 1}. ${n.title || "（无标题）"}`);
    if (n.from) {
      if (n.from.cites && n.from.cites.length) lines.push(`   依据：${n.from.cites.join("、")}`);
      if (n.from.q) lines.push(`   摘自：${n.from.q}`);
    }
    if (n.body) lines.push(noteText(n.rich || n.body).split("\n").map(l => "   " + l).join("\n"));
    lines.push("");
  });
  const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `飞盘笔记-${new Date().toISOString().slice(0, 10)}.txt`;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  toast(`已导出 ${live.length} 条笔记`);
}
function importNotes(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { toast("文件太大了（上限 2MB）"); return; }
  const fr = new FileReader();
  fr.onload = () => {
    try {
      const raw = String(fr.result || "");
      const MAX_ITEMS = 500, MAX_TITLE = 200, MAX_BODY = 20000;
      const before = NT.items.slice();
      const adds = [];
      let dup = 0;
      const isNew = (t, b) => !before.some(x => (x.title || "") === t && (x.body || "") === b)
        && !adds.some(x => (x.title || "") === t && (x.body || "") === b);
      const push = (title, body, star, createdAt) => {
        title = String(title || "").slice(0, MAX_TITLE);
        body = String(body || "").slice(0, MAX_BODY);
        if (!title && !body) return;
        if (adds.length >= MAX_ITEMS) return;
        if (!isNew(title, body)) { dup++; return; }
        adds.push(newNoteFromText({ title, body, star: !!star, createdAt }));
      };
      if (/^\s*\[/.test(raw)) {
        /* 兼容本工具导出的原始数组（早期版本 / 用户手写 json） */
        const arr = JSON.parse(raw);
        if (!Array.isArray(arr)) throw new Error("格式不对");
        arr.forEach(o => {
          if (!o || typeof o !== "object" || Array.isArray(o)) return;
          // 2026-10-24：只收基本类型。原来 `o.title || ""` 会把对象
          // 变成 "[object Object]"，长度/条数也不设限。
          const title = typeof o.title === "string" ? o.title : "";
          const body = typeof o.body === "string" ? o.body : "";
          const createdAt = typeof o.createdAt === "number" && Number.isFinite(o.createdAt)
            ? o.createdAt : undefined;
          push(title, body, o.star === true, createdAt);
        });
      } else {
        /* txt：靠「行首 数字点」当新条目的边界（导出时就是这么写的）。
           ⚠️ 必须**先跳掉导出时写的文件头两行**（2026-10-07 修）：
               它们是"我的飞盘规则笔记" / "导出于 … 共 N 条"，不属于任何条目。
               原实现把它们塞进 buf，等到第一条 "1. xxx" 时 flush() ——
               buf 非空 ⇒ 生成一条标题为空、正文是文件头的垃圾笔记。
               症状很确定：每次导入自己的导出文件都多一条，
               而且它还会被再次导出、再次导入，垃圾只增不减。 */
        let title = "", buf = [];
        const flush = () => {
          const body = buf.join("\n").trim();
          if (title || body) push(title, body, false, undefined);
          title = ""; buf = [];
        };
        const lines = raw.split(/\r?\n/).slice(0, 20000);
        let started = false;
        lines.forEach((line, idx) => {
          const mm = line.match(/^\s*\d+\.\s*(.+)$/);
          if (mm) { if (title || buf.length) flush(); title = mm[1].trim(); started = true; return; }
          if (/^\s*(依据|摘自)：/.test(line)) return;
          // 还没遇到第一个编号行之前，只丢掉表头（最多前2 行），其余照常进 buf
          if (!started && buf.length < 2 && idx < 2 && line.trim()) return;
          buf.push(line.replace(/^\s{3}/, ""));
        });
        flush();
      }
      NT.items = adds.concat(before);
      if (!saveNotes()) {
        NT.items = before;   // 写入失败必须回滚内存，否则界面显示"已导入"但磁盘没变
        toast("本地存储写入失败，导入未生效");
        return;
      }
      toast(adds.length ? `已导入 ${adds.length} 条${dup ? `，跳过 ${dup} 条重复` : ""}` : `没有新笔记（${dup} 条已存在）`);
      paintNotesPage();
    } catch (e) { toast("这个文件读不出来，请选本工具导出的文件"); }
  };
  fr.readAsText(file, "utf-8");
}
const NT_KEY = "frisbee.rag.notes";
const NT = { items: [], view: "src", open: null, broken: false, recovery: "",
             lastRaw: null, conflict: false };

function loadNotes() {
  try {
    const raw = localStorage.getItem(NT_KEY) || "";
    const d = JSON.parse(raw || "[]");
    // 2026-10-24 R2：结构坏（非数组 / 元素不是对象）必须保留原文，
    // 不能像旧实现那样 filter 成空数组，下一次 saveNotes 写 []。
    if (!Array.isArray(d)
        || d.some(n => !n || typeof n !== "object" || Array.isArray(n))) {
      throw new Error("notes schema");
    }
    NT.lastRaw = raw;
    NT.broken = false;
    NT.items = d
      // 空笔记不留（新建了没写就关掉的那种）；但**标了重点的不算空**——
      // 星标本身就是一个用户的决定，不该被"没写正文"清掉。
      // ⚠️ 判空必须用 noteBlank 而不是 `!n.body`（2026-10-07）：
      //    正文改成富文本后，清空的编辑器会留下 "<div><br></div>" 这种壳，
      //    它 truthy ⇒ 空笔记会被当成有内容的笔记留下来，列表越攒越脏。
      ? d.filter(n => !noteBlank(n))
      : [];
    // 迁移：老笔记没有 createdAt / rev，用 ts 补齐。
    // ⚠️ ts 历史上被"编辑即刷新"（见 noteNode 的 touch），所以它一直
    //    表达的是"最后修改时间"—— 不能拿它当创建时间，日期分组会跟着
    //    每次编辑漂移。新字段 createdAt 才是"我什么时候记的这条"。
    let dirty = false;
    NT.items.forEach(n => {
      if (!n.createdAt) { n.createdAt = n.ts || Date.now(); dirty = true; }
      if (typeof n.rev !== "number") { n.rev = 0; dirty = true; }
      // lastRev：复习调度基准（见 reviewDue）。老笔记没有这个字段，
      // 退回用 createdAt 初始化 = 从创建日算起，不会一迁移就全量涌进来。
      if (typeof n.lastRev !== "number") { n.lastRev = 0; dirty = true; }
      /* 富文本迁移（2026-10-07）：老笔记的 body 是**纯文本**，没有 fmt 标记。
         判据是 fmt === "html"，而不是"body 里有没有 <"——
         用户的笔记里写"a < b"是常事，用有没有尖括号来判断会把它误当 HTML，
         渲染时" < b>" 之后的内容会被浏览器吃掉，**内容永久丢失**。
         没有这个标记 ⇒ 一律按纯文本转义后存进 rich。 */
      if (n.fmt !== "html") {
        n.rich = noteSanitize(noteFromText(n.body || ""));
        n.body = noteText(n.rich);
        n.fmt = "html";
        dirty = true;
      }
    });
    if (dirty) saveNotes();
  } catch (e) {
    /* ⚠️ 解析失败时**不要**把内存清空后就完事（2026-10-07 修）。
       原写法 `NT.items = []`：磁盘上的旧值还在，但内存已经是空的 ——
       接下来用户做任何写操作（记一条笔记、问一句话）都会用
       "空数组 + 一条新数据" 覆盖掉全部历史，丢得干干净净。
       现在：换个内存 key 兜住原始串（不再写入，但可导出/人工恢复），
       并且**不把它变成空列表继续跑** —— 用户看得到自己还有数据在。 */
    console.error("[storage] 笔记解析失败，原文已保留在 NT.recovery", e);
    NT.recovery = localStorage.getItem(NT_KEY) || "";
    NT.broken = true;              // 2026-10-24：禁止后续 saveNotes() 覆盖原始串
    NT.items = NT.items || [];
  }
}
/* 本地存储写入。**返回是否成功** —— 调用方必须知道，因为笔记的唯一副本
   就在这里，写失败 = 永久丢失。
   ⚠️ 原来这里是 `try { ... } catch (e) { }`，空 catch。而 saveNotes() 之后
      照常 flashSaved() 闪「已保存」⇒ **界面明确告诉用户存好了，实际没存**。
      用户失去信任且无从察觉 —— 这比报错糟得多。localStorage 约 5MB 上限，
      而 CH.sessions 没有条数上限，是必然到达的路径。 */
let _lastSaveErr = "";
function storeSet(key, value) {
  try {
    localStorage.setItem(key, value);
    _lastSaveErr = "";
    return true;
  } catch (e) {
    // QuotaExceededError：真的写不进去了。这条提示必须显眼且持久，
    // 因为它意味着"这一条可能没存上"，而笔记没有服务器备份。
    _lastSaveErr = "本地存储已满，最新的笔记可能没存上。建议先导出一份 txt 备份。";
    const t = $("toast");
    if (t) { t.textContent = _lastSaveErr; t.classList.add("on"); clearTimeout(_toastTimer); _toastTimer = setTimeout(() => t.classList.remove("on"), 6000); }
    console.error("[storage] 写入失败", key, e);
    return false;
  }
}
function saveNotes() {
  // 2026-10-24：解析失败的原始串还在磁盘上；此时写空数组/新数组会**永久覆盖**它。
  // 先让用户导出 NT.recovery，或显式恢复默认（见设置页）。
  if (NT.broken) return false;
  const val = JSON.stringify(NT.items);
  const cur = localStorage.getItem(NT_KEY) || "";
  // 2026-10-24 R2：最低限度冲突检测 —— 另一个窗口改过就拒绝静默覆盖。
  if (NT.lastRaw !== null && cur !== NT.lastRaw) {
    NT.conflict = true;
    toast("另一个窗口也改了笔记；本次没有覆盖它。请先导出/关闭另一个窗口。");
    return false;
  }
  if (!storeSet(NT_KEY, val)) return false;
  NT.lastRaw = val;
  return true;
}
function newNote(o) {
  const now = Date.now();
  /* ⚠️ `lastRev` 必须在这里就带上（2026-10-07 补）。
   * 复习调度的基准是「上次复习时间」（见 reviewDue），而新建笔记从没复习过 ⇒
   * 基准 = 创建时间。之前靠 loadNotes 的迁移补字段，但**运行期新建**的笔记
   * 走不到迁移 ⇒ lastRev 恒 undefined ⇒ reviewDue 退回 createdAt，
   * 也就是那个「间隔退化成固定日期、满档后永久滞留」的缺陷又回来了。 */
  return Object.assign({ id: uid(), title: "", body: "", rich: "", fmt: "html",
    ts: now, createdAt: now, lastRev: now, star: false, rev: 0 }, o || {});
}
/* 用纯文本造一条带富文本结构的笔记。**导入（txt / json）和对话里
   "记进笔记"走的都是这条** —— 它们拿到的都是纯文本，
   在这里统一转一次，比在三个调用点各写一遍可靠得多。
   刻意不做"如果看起来像 HTML 就当 HTML 处理"的判断：笔记正文里
   写"a < b"很常见，误判成 HTML 会让 "< b" 之后的内容被浏览器吃掉。 */
function newNoteFromText(o) {
  const t = String((o && o.body) || "");
  const rich = noteSanitize(noteFromText(t));
  return newNote(Object.assign({}, o || {}, { rich, body: noteText(rich), fmt: "html" }));
}
/* 活着的笔记 = 回收站之外的全部。**所有列表都必须过这里** ——
   漏一处就会让删掉的东西还出现在页面上。 */
function liveNotes() { return NT.items.filter(n => !n.del); }

/* ==========================================================================
   删除 = 先进回收站，不真删。
   为什么必须这样：笔记只存在浏览器 localStorage 里，**没有服务器备份**。
   一次误触 × 就永久丢失，而这个本子里存的是"我踩过的坑"—— 丢了没法重来。
   Obsidian/Notion 给的是文件级可恢复，我们能给的是**30 天 + 一次撤销**，
   已经是这个存储条件下的上限。
   ⚠️ delAt 存时间戳而非 true —— 30 天后才能自动清理，也才能给回收站排序。*/
const TRASH_DAYS = 30;
function trashNote(n) {
  n.del = Date.now();
  n.ts = n.createdAt || n.ts || Date.now();   // 别让"删除时间"污染日期分组
  saveNotes();
}
function restoreNote(n) { delete n.del; saveNotes(); }
function purgeNote(n) { const i = NT.items.findIndex(x => x.id === n.id); if (i >= 0) NT.items.splice(i, 1); saveNotes(); }
function trashedNotes() { return NT.items.filter(n => n.del).sort((a, b) => (b.del || 0) - (a.del || 0)); }
/* 超过 30 天的自动清走 —— 回收站不是永久仓库，不给它上限就会变成第二个垃圾场。 */
function sweepTrash() {
  const cut = Date.now() - TRASH_DAYS * 86400000;
  const before = NT.items.length;
  NT.items = NT.items.filter(n => !(n.del && n.del < cut));
  if (NT.items.length !== before) saveNotes();
}

/* 右栏该不该出现。窄屏下右栏是抽屉，默认收起 —— 否则遮罩会一直盖住对话区。 */
function panelOpen() {
  if (NT.open !== null) return !!NT.open;
  const s = curS();
  return !!(s && s.msgs.length) && window.innerWidth > 1280;
}
/* 把开合状态写进 DOM。**只有这里改 .hide** —— 否则就会出现"JS 认为开着、
   CSS 认为关着"的分裂，表现是点了没反应。 */
function syncRail() {
  const r = $("rail"); if (!r) return;
  const open = panelOpen();
  r.classList.toggle("hide", !open);
  const chat = $("pbody") && $("pbody").querySelector(".chat");
  if (chat) chat.classList.toggle("no-rail", !open);
  const navOpen = !!($("nav") && $("nav").classList.contains("open"));
  $("scrim").style.display = (navOpen || (open && window.innerWidth <= 1280)) ? "block" : "none";
}
function openPanel(view, open) {
  if (view) NT.view = view;
  NT.open = open;
  paintRail(); syncRail();
}

/* 把一条回答摘进笔记：带原问题和引用条号。
   条号是关键 —— 日后回看时能立刻回到"当时依据的是哪几条"。 */
function captureNote(m) {
  const p = m.payload || {};
  const txt = String(m.content || "")
    .replace(/^#+\s*/gm, "").replace(/[*`>]/g, "").replace(/\n{3,}/g, "\n\n").trim();
  NT.items.unshift(newNoteFromText({
    title: (m.q || "").slice(0, 40),
    body: txt.slice(0, 1200),
    from: { q: m.q || "", cites: (p.citations || []).map(c => c.id) },
  }));
  saveNotes();
  NT.view = "note"; NT.open = true;
  paintRail(); syncRail();
  focusNoteBody($("railBody"));
  toast("已记进笔记");
}

/* 把光标放进笔记正文开头。
   ⚠️ 这里原来写的是 `b.setSelectionRange(0, 0)` —— 那是 **textarea 专有**方法，
   换成 contenteditable 后调用会直接抛 TypeError，整个函数后面的 toast 都执行不到。
   现在按元素类型分派。选区必须显式构造：光标放进一个空 div 但没有 Range 时，
   Chrome 不会立刻显示插入符，得先给一个 0 长度的 Range。 */
function focusNoteBody(scope) {
  const b = scope && scope.querySelector ? scope.querySelector(".nt-b") : null;
  if (!b) return;
  b.focus();
  if (typeof b.setSelectionRange === "function") { b.setSelectionRange(0, 0); return; }
  const sel = window.getSelection();
  if (!sel) return;
  const r = document.createRange();
  r.selectNodeContents(b);
  r.collapse(true);
  sel.removeAllRanges();
  sel.addRange(r);
}

/* 保存状态：右上角闪一下「已保存」。
   ⚠️ 存在的理由（owner 2026-10-06：「能用是能用，但我感觉很难用」）：
   "自动保存"这件事原来只写在**空笔记的 placeholder** 里 —— 你一打字它就没了，
   于是用户写完一段话，屏幕上没有任何东西告诉他这玩意儿存没存。
   一个没有回执的自动保存，和没有保存是一样的体感。 */
let _savedTimer = null;
function flashSaved() {
  const n = $("ntSaved"); if (!n) return;
  n.classList.add("on");
  if (_savedTimer) clearTimeout(_savedTimer);
  _savedTimer = setTimeout(() => n.classList.remove("on"), 1300);
}

/* 笔记的**分组**：重点（星标）在最上，其余按时间倒序分成 今天 / 昨天 / 某月某日。
   分组的用处不是好看 —— 是"我现在看的是哪一批"。

   ⭐ 2026-10-07 加「按规则章」：这是**别的笔记软件抄不走的一条**。
   为什么必须有：Notion/Obsidian 不知道"条款"是什么，所以只能按时间/标签分；
   而飞盘玩家的真实心智是「**我关于掩护犯规记过什么**」—— 他记东西的
   分类轴本来就是条款，不是日期。一堆按时间排的笔记，等于把
   "关于读秒的那 3 条" 埋在 60 条里翻不出来。
   取哪一条？**优先取笔记来源里那个"最像主条款"的**（位数最浅的那个），
   因为人记一条笔记时引的往往是一串条款，正解是其中最概括的那条。 */
const CHAPTERS = {
  1: "精神与自我裁决", 2: "场地", 3: "装备", 4: "得分与时长", 5: "掷盘",
  6: "站位", 7: "开盘与生命", 8: "捡盘与跑动", 9: "读秒", 10: "验盘",
  11: "出界", 12: "接盘与阻挡", 13: "攻防转换", 14: "得分判定", 15: "判罚",
  16: "停止比赛", 17: "动作限制", 18: "防盘与掩护", 19: "停表与安全",
  20: "暂停",
};
/* 挑"主条款"：位数最浅的优先（9.5 → 9 章；18.3.1 → 18 章）。 */
function leadClause(cites) {
  let best = null;
  (cites || []).forEach(c => {
    const cid = String(c || "");
    if (!/^\d+(\.\d+)*$/.test(cid)) return;          // 跳过 DEF:xxx 这类定义条目
    const depth = cid.split(".").length;
    if (!best || depth < best.depth) best = { id: cid, depth };
  });
  return best ? best.id : null;
}
function noteGroups(items, byChapter) {
  const out = [];
  const starred = items.filter(n => n.star);
  if (starred.length) out.push(["重点", starred]);
  const rest = items.filter(n => !n.star);
  if (byChapter) {
    /* 按章节分：没有条款的手写笔记单独收一档「未归档」，
       不丢东西 —— 它只是没归类，不是没价值。 */
    const bucket = new Map();
    rest.forEach(n => {
      const lead = leadClause((n.from && n.from.cites) || []);
      const ch = lead ? parseInt(lead.split(".")[0], 10) : 0;
      const lab = ch && CHAPTERS[ch] ? `${ch} · ${CHAPTERS[ch]}` : "未归档 · 手写";
      if (!bucket.has(lab)) bucket.set(lab, []);
      bucket.get(lab).push(n);
    });
    /* 章节小的在上（第 1 章比第 20 章更"前"），未归档沉底。 */
    Array.from(bucket.keys()).sort((a, b) => {
      const ca = parseInt(a, 10) || 99, cb = parseInt(b, 10) || 99;
      return ca - cb;
    }).forEach(k => out.push([k, bucket.get(k)]));
    return out;
  }
  rest.sort((a, b) => (b.createdAt || b.ts || 0) - (a.createdAt || a.ts || 0));
  let last = null, cur = null;
  rest.forEach(n => {
    const lab = dayLabel(n.createdAt || n.ts || Date.now());
    if (lab !== last) { cur = [lab, []]; out.push(cur); last = lab; }
    cur[1].push(n);
  });
  return out;
}

/* 把一组笔记渲染进容器。**右栏面板和整页「我的笔记」共用这一个** ——
   两份渲染必然会有一天不一致（这条教训在本文件里出现过很多次）。

   结构 =「一二三四」的条理（owner 2026-10-06 的原话）：
     重点 → 今天 → 昨天 → …，每条前面一个**连续编号**。
   ⚠️ 编号是**显示位置**，不是稳定 ID —— 新写的排最前，编号会整体重排。
      这正是备忘录该有的样子（"我本子上第 3 条"），别改成持久化的自增 ID，
      那会得到"第 1 条、第 7 条、第 23 条"这种读不出顺序的东西。 */
function renderNoteList(container, items, refresh, byChapter) {
  const list = el("div", "nt-list");
  let no = 0;
  noteGroups(items, byChapter).forEach(([label, arr]) => {
    if (!arr.length) return;
    const g = el("div", "nt-grp"); g.textContent = label; list.appendChild(g);
    arr.forEach(n => { no++; list.appendChild(noteNode(n, refresh, no)); });
  });
  container.appendChild(list);
  /* ⚠️ 这里原来有一行 `.nt-b.forEach(growNote)`。growNote 是给 textarea
     自动撑高用的（靠 scrollHeight 反推），换成 contenteditable 后
     它连同函数一起删了 —— 但**调用点漏了**，症状是打开笔记页立刻
     ReferenceError，整页空白。删函数时必须同步删调用点。
     contenteditable 天然随内容长高，撑高交给 CSS 的 min-height 即可。 */
}

function paintNotes(body) {
  const live = liveNotes();
  if ($("railCnt")) $("railCnt").textContent = String(live.length);
  if (NT.broken) {
    const warn = el("div", "nt-empty");
    warn.textContent = "笔记数据的结构无法识别。为免覆盖原件，已停止写入。请先导出原始数据保存，再联系维护者恢复。";
    body.appendChild(warn);
    const dump = el("button", "nt-add");
    dump.textContent = "导出损坏的原始数据";
    dump.addEventListener("click", () => downloadText(NT.recovery, "frisbee-notes-recovery.txt"));
    body.appendChild(dump);
    return;
  }
  const add = el("button", "nt-add");
  add.textContent = "＋ 新建一条";
  add.addEventListener("click", () => { NT.items.unshift(newNote({ body: "" })); saveNotes(); paintRail(); });
  body.appendChild(add);

  if (!live.length) {
    const h = el("div", "nt-empty");
    h.textContent = "还没有笔记。随手记下你的理解、队里的约定、上次争论的结论 —— 只存在你这台设备上。";
    body.appendChild(h);
    return;
  }
  renderNoteList(body, live, paintRail);
}

/* 单条笔记的节点。**右栏面板和整页「我的笔记」共用这一个** ——
   两份渲染必然会有一天不一致（这条教训在本文件里出现过很多次）。
   refresh：删掉之后该重画谁。默认是右栏；整页视图传它自己的重画函数进来。
   no：这条在**当前显示列表**里的序号（见 renderNoteList 的注释）。 */
function noteNode(n, refresh, no) {
  const redraw = refresh || paintRail;
  const box = el("div", "nt-item" + (n.star ? " star" : ""));
  const h = el("div", "nt-h");
  if (no) { const num = el("span", "nt-no"); num.textContent = String(no); h.appendChild(num); }

  const t = el("input", "nt-t"); t.value = n.title || ""; t.placeholder = "标题（可留空）";

  // 星标 = 「这条要留着，别淹在下面」。置顶分组就靠它。
  const star = el("button", "nt-star" + (n.star ? " on" : ""));
  star.textContent = n.star ? "★" : "☆";
  star.title = n.star ? "取消重点" : "标为重点";
  star.addEventListener("click", () => { n.star = !n.star; saveNotes(); redraw(); });

  const del = el("button", "nt-del"); del.textContent = "×"; del.title = "删除这条笔记";
  /* ⚠️ 不再 confirm()，改为"删了能撤回"。理由见 trashNote 的注释：
     一个没有服务器备份的本子里，误删 = 永久丢失，而 confirm 会让人
     不敢随手清理 —— 于是笔记越攒越多、越来越不敢碰。 */
  del.addEventListener("click", () => {
    trashNote(n); redraw();
    undoToast(`已删除「${(n.title || (n.from && n.from.q) || "这条笔记").slice(0, 16)}」`, "撤回", () => {
      restoreNote(n); redraw();
    });
  });
  h.appendChild(t); h.appendChild(star); h.appendChild(del); box.appendChild(h);

  /* ---- 正文：contenteditable（2026-10-07 从 textarea 换过来）----
     换的理由：纯文本记不了结构。「3 条规则 + 我自己的看法」这种内容，
     没有列表和加粗就只能靠换行硬凑，写完自己都不想看第二眼。

     ⚠️ contenteditable 有四个坑，全在这里处理掉了，别删：
       ① paste —— 默认会把来源页面的样式和 <script> 一起带进来。
          必须 preventDefault + 只取 text/plain 插入（见下）。
       ② 工具栏按钮 —— mousedown 会把焦点从编辑区抢走，选区随之消失，
          点了按钮再点回来，加粗就没作用在选中的字上。所以每个按钮
          都 mousedown→preventDefault（见 noteToolbar）。
       ③ placeholder —— contenteditable 没有原生 placeholder，
          靠 CSS :empty::before + data-ph。
       ④ 自动增高 —— textarea 靠 scrollHeight 撑，contenteditable 天然
          随内容长高，只需 min-height。原来那个 growNote 已随之删除。 */
  const b = el("div", "nt-b");
  /*⚠️ contenteditable="true"，**不能写 "plaintext-only"**（2026-10-07 真 bug）。
     浏览器探针里查到：plaintext-only 下 document.execCommand("bold")
     **静默无效** —— 不报错，就是什么都不发生。加粗按钮点了没反应，
     而检查"代码里有 execCommand('bold')"一律通过 ⇒ 典型假绿。
     根因：plaintext-only 的定义就是"只允许纯文本，格式命令一律忽略"。
     代价是粘贴必须自己管（见下面的 paste 处理），
     而富文本必须用 true。 */
  b.setAttribute("contenteditable", "true");
  b.setAttribute("role", "textbox");
  b.setAttribute("aria-multiline", "true");
  b.setAttribute("aria-label", "笔记正文");
  b.setAttribute("data-ph", "写下你的理解…（自动保存）");
  /* 用 fmt 字段区分新旧：老的 body 是纯文本，直接塞进去会被 contenteditable
     当 HTML 解析（"a < b" 就会吃掉后面一段）。 */
  const rich = n.fmt === "html" ? (n.rich || "") : noteFromText(n.body || "");
  b.innerHTML = noteSanitize(rich);
  applyTodoState(b);
  box.appendChild(b);

  box.appendChild(noteToolbar(b, n));

  // ⚠️ 输入时**只更新状态、不重绘**：重绘会把正在打字的那个元素换掉，
  // 光标位置和输入法组合态全丢 —— 表现是"打字打一半跳走了"。
  let timer = null;
  const touch = () => {
    n.title = t.value;
    /* 存之前先过一遍白名单。**不是为了防用户自己**（他写什么都是他的），
       而是为了防 body 里混进别的东西 —— 它在 localStorage 里，
       任何能写这个源的脚本都能塞东西进来，而正文是要渲染成 HTML 的。 */
    syncTodoAttrs(b);
    n.rich = noteSanitize(b.innerHTML);
    n.fmt = "html";
    n.body = noteText(n.rich);                 // 纯文本镜像：搜索/导出/复习用
    n.ts = Date.now();
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { if (saveNotes()) flashSaved(); }, 400);
  };
  t.addEventListener("input", touch);
  // 2026-10-24：输入后 400ms 才落盘；用户"打完立刻关窗"时最后一次输入会丢。
  // pagehide 是移动端/关闭窗口都能触发的最后时机（beforeunload 在部分环境不可靠）。
  try {
    window.addEventListener("pagehide", () => {
      if (timer) { clearTimeout(timer); timer = null; saveNotes(); }
    }, { once: true });
  } catch (e) { /* 老浏览器 */ }

  /* 粘贴：只收纯文本。这是唯一一道"外来内容进门"的关口，
     过了这里的内容会一路走到 noteSanitize，但少一层过滤少一层风险。 */
  b.addEventListener("paste", (e) => {
    e.preventDefault();
    const t0 = (e.clipboardData || window.clipboardData);
    const s = t0 && t0.getData ? t0.getData("text/plain") : "";
    if (!s) return;
    /* insertText 而不是 innerHTML +=：前者保留光标位置和撤销栈。
       ⚠️⚠️ 没有选区时**绝不能**写 `b.textContent += s`（2026-10-09 真 bug，
       浏览器探针抓到）：`textContent` 赋值会**替换掉全部子节点** ——
       整篇笔记的加粗、小标题、列表、待办会被一次性抹平成纯文本，
       而用户只是粘了一段话。实测：粘之前 <b>粗体字</b> 在，粘之后没了。
       而且原写法 `if (...) { b.textContent += s; }` **后面没有 return**，
       会接着再跑一次 execCommand("insertText") ⇒ 内容插两次。
       正确做法：没有选区就自己造一个「光标在末尾」的 Range，
       让 insertText 走同一条路（它只动光标那一处，不碰别的节点）。 */
    const sel = window.getSelection();
    if (sel && sel.rangeCount) {
      if (!sel.isCollapsed) sel.deleteFromDocument();
    } else {
      const r = document.createRange();
      r.selectNodeContents(b);
      r.collapse(false);
      if (sel) { sel.removeAllRanges(); sel.addRange(r); }
    }
    document.execCommand("insertText", false, s);
    touch();
  });

  /* 键盘：Enter 出新 div 而不是塞 <br>，Shift+Enter 才是软换行 ——
     这是 contenteditable 的默认行为，但它会在每行末尾留一个空 div，
     所以监听 input 后把"尾部空 div"收掉，否则笔记末尾凭空多一行。 */
  b.addEventListener("input", touch);
  /* ⭐ 待办的勾选要单独监听 change（2026-10-07 补）。
     input 事件覆盖的是正文文本变化，**点复选框不触发 input** ——
     不挂这个监听，用户打勾后界面变了、刷新回去又变回没勾，
     看起来就是"打了没用"。这也是上一条那个 bug 的另一半：
     加了 disabled 点不动、所以永远不保存；去掉 disabled 能点了，
     不监听 change 就变成"点了不存"。两头都得管。 */
  b.addEventListener("change", (e) => {
    const t = e.target;
    if (t && t.tagName === "INPUT") touch();
  });
  b.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      /* 交给默认行为生成 <div>，然后统一收拾。setTimeout 0 是为了等
         浏览器插入完 —— 同步时机里 DOM 还没变。 */
      setTimeout(() => {
        const kids = b.childNodes;
        while (kids.length && kids[kids.length - 1].nodeType === 1
               && !kids[kids.length - 1].textContent.trim()
               && !kids[kids.length - 1].querySelector("img,input")) {
          kids[kids.length - 1].remove();
        }
        touch();
      }, 0);
    }
  });

  // 页脚：来源（可点）+ 时间。
  // ⚠️ 这一行原来是**死文本**（`摘录自提问「…」· 依据 2.1`）—— 用户看见 "2.1"
  //    却点不动，等于把"回去看一眼原文"这条路修到一半就断了。
  //    现在两项都可点：问题 → 回到那次对话；条款号 → 直接看那条原文。
  const m = el("div", "nt-m");
  if (n.from) {
    if (n.from.q) {
      const q = n.from.q;
      const qb = el("button", "nt-src");
      qb.textContent = `摘录自「${q.length > 22 ? q.slice(0, 22) + "…" : q}」`;
      qb.title = `回到那次对话：${q}`;
      qb.addEventListener("click", () => gotoNoteFrom(n, null));
      m.appendChild(qb);
    }
    const cites = n.from.cites || [];
    if (cites.length) {
      const by = el("span", "nt-by"); by.textContent = "· 依据"; m.appendChild(by);
      cites.forEach(cid => {
        const cb = el("button", "nt-cite"); cb.textContent = cid;
        cb.title = `看第 ${cid} 条的原文`;
        cb.addEventListener("click", () => gotoNoteFrom(n, cid));
        m.appendChild(cb);
      });
    }
  }
  const tm = el("span", "nt-time");
  const d = new Date(n.ts || Date.now());
  tm.textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  m.appendChild(tm);
  box.appendChild(m);
  return box;
}

/* 从笔记回到"它从哪儿来"。这是笔记真正的价值所在 ——
   摘下来的东西不是死文本，是**一条回去的路**。
   cid 为空 = 只回那次对话；有 cid = 顺手把那条原文在右栏选中并滚出来。 */
function gotoNoteFrom(n, cid) {
  const q = (n.from && n.from.q) || "";
  const s = q
    ? CH.sessions.find(x => (x.msgs || []).some(m => m.role === "user" && m.content === q))
    : null;
  if (!s) { toast("那次对话已经不在了"); return; }
  CH.current = s.id;
  CH.active = cid || null;
  if (cid) { NT.view = "src"; NT.open = true; }
  saveSessions();
  // hashchange 是异步的：不在对话页时先切过去，等它渲染完再滚。
  if (parseHash().main !== "chat") go("chat"); else paintChat();
  if (cid) setTimeout(() => {
    const card = $("railBody") && $("railBody").querySelector(`.rcard[data-cid="${CSS.escape(cid)}"]`);
    if (card) { card.classList.add("open"); card.scrollIntoView({ behavior: "smooth", block: "center" }); }
  }, 80);
}

/* 正文工具栏。**抄的是微信笔记那条工具栏的能力，不是它的形态** ——
   微信那排是给单文档编辑器用的，我们这是笔记本，一条笔记不该顶着一排按钮。

   所以只留 5 个真正会用的：加粗 / 项目符号 / 待办 / 小标题 / 撤销。
   - 不做斜体下划线：飞盘笔记是给自己看的备忘，不是排版作品，
     多两个按钮就多两处「我会不会用错了」的心思。
   - 不做图片、语音：笔记只存在本机（localStorage），放图片会迅速吃光配额，
     而 storeSet 的失败提示是"建议先导出备份"—— 那等于变相劝退。
   - 不做标题层级选择器：只有一个"小标题"按钮，
     因为笔记真正需要的分块就是「规则清单」和「我的看法」两层。

   ⚠️ 位置在正文**下方**（不是微信那样浮在整个页面顶部）：微信那条永远占着
     固定屏幕高度，我们这页要能一屏看完好几条笔记。
     放正文下方还有一个实际好处：选完字往下一点就够得着，
     而放上方时选区在下方、手要往回抬。
     （原注释写的是"上方"，与实际渲染不符 —— 2026-10-07 截图时发现后改正。
      改注释而不是改位置，因为下方那个位置实测更好用。） */
const NOTE_TOOLS = [
  { k: "bold", label: "加粗", html: "<b>B</b>", cmd: "bold",
    tip: "加粗（Ctrl+B）" },
  { k: "ul", label: "项目符号", html: "&#8226;&#8226;", cmd: "insertUnorderedList",
    tip: "列成清单（Ctrl+Shift+8）" },
  { k: "todo", label: "待办", html: "&#9745;", cmd: null,
    tip: "待办事项，可以打勾" },
  { k: "h", label: "小标题", html: "T", cmd: null,
    tip: "这一段是标题" },
  { k: "undo", label: "撤销", html: "&#8630;", cmd: "undo",
    tip: "撤销（Ctrl+Z）" },
];

function noteToolbar(b, n) {
  const bar = el("div", "nt-bar");
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "笔记排版");

  const save = () => {
    syncTodoAttrs(b);
    n.rich = noteSanitize(b.innerHTML);
    n.fmt = "html";
    n.body = noteText(n.rich);
    n.ts = Date.now();
    if (saveNotes()) flashSaved();
  };

  NOTE_TOOLS.forEach(tt => {
    const btn = el("button", "nt-tb");
    btn.type = "button";
    btn.title = tt.tip;
    btn.setAttribute("aria-label", tt.label);
    btn.innerHTML = tt.html;
    /* ⚠️ 这里是整条工具栏最关键的一行。
       mousedown 必须 preventDefault：让焦点**留在编辑区**。
       默认行为是按钮按下瞬间抢走焦点 ⇒ 选区随之消失 ⇒ 再点回来时
       加粗作用在光标处，而不是你刚才选中的那串字。症状是"点了没反应"，
       极易被误认为是功能坏了。这是 contenteditable 工具栏的头号坑。 */
    btn.addEventListener("mousedown", (e) => e.preventDefault());
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      b.focus();
      if (tt.k === "todo") {
        /* 待办没有 execCommand 可用 —— 自己在光标处插一个 checkbox。
           用 Range 而不是 innerHTML += ：那会把用户光标弹回开头。 */
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount) { toast("先把光标放到要打勾的位置"); return; }
        const r = sel.getRangeAt(0);
        const box = document.createElement("input");
        box.type = "checkbox";
        let node = r.startContainer;
        const frag = document.createDocumentFragment();
        frag.appendChild(box);
        frag.appendChild(document.createTextNode(" "));
        if (node.nodeType === 3 && node.parentNode) {
          const mid = node.splitText(r.startOffset);
          mid.parentNode.insertBefore(frag, mid);
        } else if (node.parentNode) {
          node.parentNode.insertBefore(frag, node);
        } else { b.appendChild(frag); }
        sel.collapseToEnd();
      } else if (tt.k === "h") {
        /* 在"正文 / 小标题"之间切换。重复点同一个标题块就取消。
           判据是光标所在块是不是 h2 —— 而不是查有没有 h2，
           否则笔记里有三个标题时点哪都变成 h2，没法改单个。 */
        const sel = window.getSelection();
        let cur = sel && sel.anchorNode;
        let isH = false;
        while (cur && cur !== b) {
          if (cur.nodeType === 1 && /^H[12]$/.test(cur.tagName)) { isH = true; break; }
          cur = cur.parentNode;
        }
        if (isH && cur) {
          const t = noteText(cur.innerHTML);
          cur.outerHTML = `<div>${esc(t)}</div>`;
        } else {
          try { document.execCommand("formatBlock", false, "h2"); }
          catch (err) { toast("这个位置暂时不能变成标题"); return; }
        }
      } else {
        /* ⭐ execCommand 是废弃 API，但它**是唯一不需要依赖的富文本命令入口**。
           本项目红线之一是"不新增第三方依赖"，而现代替代方案
           （CSS Editing / Selection API 手搓）在这里要多写 200 行、
           且各端行为差异更大。Chromium 上它稳定可用，先用它。 */
        try { document.execCommand(tt.cmd, false, null); }
        catch (err) { toast("这个操作浏览器不支持"); return; }
      }
      save();
    });
    bar.appendChild(btn);
  });
  return bar;
}


function viewChat() {
  const pbody = $("pbody");
  // 只有「消息流 + 输入框 + 右栏」。历史对话与助手选择已上移到唯一侧栏，
  // 这里不再自带第二条竖栏。
  // 右栏是**一个槽位两个视图**：引用（这次回答的依据）/ 笔记（我自己的本子），
  // 顶部标签切换。不再并排开两栏 —— 对话区会没地方写字。
  pbody.innerHTML = `
    <div class="chat">
      <button class="c-burger" id="cBurger" title="菜单">☰</button>
      <button class="c-note" id="cNote" title="我的笔记">我的笔记</button>
      <div class="chat-mid">
        <div class="stream" id="stream"><div class="inner" id="inner"></div></div>
        <div class="composer">
          <div class="chat-trust">只依据 WFDF 规则原文；不确定会直说。</div>
          <div class="cbox"><div class="field">
          <label class="composer-label" for="q"><span>描述场上情况</span><span aria-hidden="true">轮到你了</span></label>
          <div class="input-row">
            <textarea id="q" rows="1" aria-label="描述场上情况" placeholder="用大白话说场上的情况"></textarea>
            <button class="send" id="send">发送</button>
          </div>
          <div class="composer-meta">
            <span class="mode"><i aria-hidden="true"></i> 基于 WFDF 规则原文</span>
            <span class="hint" id="hint">Enter 发送 · Shift + Enter 换行</span>
          </div>
        </div></div></div>
      </div>
      <aside class="rail" id="rail">
        <div class="rail-in">
          <div class="rail-head">
            <div class="rtabs">
              <button class="rtab" id="tabSrc" data-v="src">来源</button>
              <button class="rtab" id="tabNote" data-v="note">笔记</button>
            </div>
            <span class="cnt" id="railCnt">0</span>
            <button class="x" id="railClose" title="收起">›</button>
          </div>
          <div class="rail-body" id="railBody"></div>
        </div>
      </aside>
    </div>`;

  // 顶栏在对话页是隐藏的，而窄屏的汉堡按钮原本住在顶栏里 ——
  // 不补这一个，窄屏用户就再也打不开侧栏（新对话 / 历史 / 设置全在里面）。
  pbody.querySelector("#cBurger").addEventListener("click", () => {
    $("nav").classList.add("open"); $("scrim").style.display = "block";
  });

  pbody.querySelector("#cNote").addEventListener("click", () => { NT.open = true; NT.view = "note"; paintRail(); syncRail(); });
  pbody.querySelector("#railClose").addEventListener("click", () => openPanel(null, false));
  pbody.querySelectorAll(".rtab").forEach(b => b.addEventListener("click", () => {
    NT.view = b.dataset.v; paintRail();
  }));

  const ta = pbody.querySelector("#q");
  ta.addEventListener("input", () => grow(ta));
  ta.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(ta.value); }
  });
  pbody.querySelector("#send").addEventListener("click", () => { CH.busy ? stopAsk() : ask(ta.value); });

  const st = pbody.querySelector("#stream");
  st.addEventListener("scroll", () => { CH.stick = (st.scrollHeight - st.scrollTop - st.clientHeight) < 60; });

  paintChat();
  // 输入框自动聚焦：桌面进来就能打字，不用先点一下。
  // ⚠️ 2026-10-24 R1.1：**触屏设备不自动聚焦** —— 程序化 focus 会把软键盘
  //    顶上来，遮住首屏构图。点示例时的聚焦行为不变（用户主动点了才聚焦）。
  const coarsePointer = window.matchMedia && window.matchMedia("(pointer:coarse)").matches;
  if (!coarsePointer) setTimeout(() => { const t = $("q"); if (t) t.focus(); }, 30);
}

/* ==========================================================================
   主动回忆（自我测试）
   --------------------------------------------------------------------------
   为什么做这个：**"Retention is not understanding" —— 记住定义不等于理解。**
   而飞盘规则是**考试型知识**：场上一句"这算犯规吗"，要不背出条号才算真的会。
   于是笔记最有用的用法不是"翻出来看"，而是**盖住答案，逼自己先说出来**
   （active recall）。搜索结果里"笔记 = 存储"那一层都停在"能翻到"，
   真正做复习的只有 Readwise 的 Daily Review —— 那是给读书人的，不是给盘友的。

   ⭐ 我们的优势是**别的软件没有的东西**：每条笔记都挂着 `from.cites`
   （摘录时自动记下的条款号）。所以我们可以做一件别的软件做不了的事 ——
   **拿自己的笔记当场出题**：把笔记盖住，只留问题，让用户答，
   答完一键展开他自己的原文 + 当时的规则依据。判定"你记对了没有"是
   他自己的事，我们只负责**把答案挡住**。

   调度：1 天 → 3 天 → 7 天 → 15 天 → 30 天（记熟了就往后推）。
   一次 30 秒，不用像 Anki 那样每天打卡 —— 每天打开这个页面的成本必须
   低于零，否则它就是个任务而不是习惯。 */
const REVIEW_STEPS = [1, 3, 7, 15, 30];   // 单位：天
const REVIEW_MAX = REVIEW_STEPS[REVIEW_STEPS.length - 1];


/* ==========================================================================
   练规则（需求侧机制 ④「练习 + 反馈」）
   --------------------------------------------------------------------------
   为什么做：产品要降低规则的理解门槛，让盘友看懂、记住、愿意再用；
   而此前全部能力都是"你问我答" —— 看懂一次不等于记住。这里做**主动回忆**。

   ⚠️ 三条不可破坏的设计（都对应一个具体的坏结果）：
   1. **答案不在前端**。判分走 `POST /api/practice`，前端手里从来没有正确答案。
      若改成前端判分，答案就得随题面下发，开开发者工具就能看见，练习失去意义。
   2. **错了必须告诉"去哪看"**。每题都有 `clauses`，判错后渲染依据条款
      —— 这是"练习"和"考试"的区别：考试给分数，练习给下一步。
   3. **选项只渲染 `text`，`key` 只用于提交**。
      早期版本两个都渲染，显示成「18.1.1.1.5  18.1.1.1.5」，看着像 bug。

   ⚠️ 服务端不存状态（题 id 确定性），所以前端也不需要"会话"概念：
      拉一批题 → 逐题提交 → 全部答完给个成绩。刷新就重来，不存在错位。
   ========================================================================== */
const PRAC = { items: [], idx: 0, right: 0, total: 0, busy: false, picked: null };

function viewPractice() {
  // ⚠️ 必须走 `mount(wrap)`，**不能**自己 `$("pbody").appendChild()`：
  //   mount() 会先 `pbody.innerHTML = ""`（那是它的职责：清掉上一页），
  //   自己先 append 再 mount ⇒ **自己刚加的东西被自己清掉**，页面一片空白。
  //   守卫 `test_views_go_through_mount` 抓的正是这个（本轮实测报红）。
  const wrap = el("div", "wrap");
  const host = el("div", "sec");
  host.id = "pracHost";
  wrap.appendChild(host);
  mount(wrap);
  pracStart();
}

/** 取题目宿主。用 id 而不是 DOM 遍历 —— 宿主是固定的，遍历会随结构变化而失效。 */
function pracHost() { return $("pracHost"); }

async function pracStart() {
  const host = pracHost();
  if (!host) return;
  host.innerHTML = '<div class="note">正在出题…</div>';
  try {
    const d = await api("/api/practice?n=" + (PREF.practiceN || 10));
    PRAC.items = d.items || [];
    PRAC.idx = 0; PRAC.right = 0; PRAC.total = PRAC.items.length;
    PRAC.picked = null; PRAC.verdict = null;
    if (!PRAC.total) {
      host.innerHTML = '<div class="note">题库暂时取不到，稍后再试。</div>';
      return;
    }
    pracDraw();
  } catch (e) {
    host.innerHTML = "";
    const n = el("div", "note");
    n.textContent = "出题失败：" + (e && e.message ? e.message : "请稍后再试");
    host.appendChild(n);
  }
}

function pracDraw() {
  const host = pracHost();
  if (!host) return;
  const q = PRAC.items[PRAC.idx];
  host.innerHTML = "";
  if (!q) { pracResult(); return; }

  const bar = el("div", "prac-bar");
  bar.innerHTML = '<span>第 <b>' + (PRAC.idx + 1) + '</b> / ' + PRAC.total + ' 题</span>'
    + '<span class="prac-score">已答对 <b>' + PRAC.right + '</b></span>';
  host.appendChild(bar);

  const prompt = el("div", "prac-prompt");
  prompt.textContent = q.prompt || "";
  host.appendChild(prompt);

  const list = el("div", "prac-opts");
  (q.options || []).forEach((o) => {
    const b = el("button", "prac-opt");
    b.type = "button";
    b.textContent = o.text || o.key;      // ⚠️ 只渲染 text（见函数头第 3 条）
    b.dataset.key = o.key || "";
    if (PRAC.picked) {
      b.disabled = true;
      // 已作答：把对/错标出来（真值来自服务端 verdict，不是自己算的）
      if (PRAC.verdict && o.key === PRAC.verdict.correct) b.classList.add("ok");
      if (PRAC.verdict && o.key === PRAC.picked && !PRAC.verdict.ok) b.classList.add("no");
    } else {
      b.addEventListener("click", () => pracPick(q, o.key));
    }
    list.appendChild(b);
  });
  host.appendChild(list);

  if (PRAC.verdict) host.appendChild(pracFeedback(PRAC.verdict));
}

function pracFeedback(v) {
  const box = el("div", "prac-fb" + (v.ok ? " ok" : " no"));
  const head = el("div", "prac-fb-h");
  head.textContent = v.ok ? "答对了" : "不对 —— 正确答案是「" + (v.correct_text || v.correct) + "」";
  box.appendChild(head);

  // ⚠️⚠️ 2026-10-15：**对错都要显示"为什么"**（原来是只在答错时显示依据）。
  //
  // 为什么改：题库从"背编号"换成**场景题**之后，题目变成**二选一（是/否）**
  //   ⇒ **蒙对的概率是 50%** ⇒ "答对了"这件事本身几乎不携带信息。
  //   真正的学习发生在**看完那两句解释**的那一刻。
  //   只奖励"猜对"而不给解释，等于把这 50% 的运气当成掌握。
  if (v.explain) {
    const e = el("div", "prac-explain");
    e.textContent = v.explain;
    box.appendChild(e);
  }
  if (v.why) {
    const w = el("div", "prac-why");
    w.textContent = v.why;
    box.appendChild(w);
  }
  // ⚠️ 哪句原文支持这个答案 —— 这是**校验器从规则原文里逐字指出来的**，
  //    不是另编的。用户想核对时，它就是"凭什么"。
  if (v.evidence) {
    const ev = el("div", "prac-evidence");
    ev.textContent = "原文依据：" + v.evidence;
    box.appendChild(ev);
  }

  // ⚠️ 判错时必须给"去哪看"。这是"练习"和"考试"的区别。
  //    答对时**仍然给条款入口**（场景题答对可能只是猜对，让他能去核一眼），
  //    但措辞不教育人 —— 不再重复"你错了"。
  if ((v.clauses || []).length) {
    const t = el("div", "prac-fb-t");
    t.textContent = v.ok ? "想核对原文的话：" : "去这几条看看：";
    box.appendChild(t);
    const cl = el("div", "prac-clauses");
    (v.citations || []).forEach((c) => {
      const a = el("button", "prac-clause");
      a.type = "button";
      a.innerHTML = '<b>第 ' + esc(c.id) + ' 条</b> · ' + esc(c.chapter_title || "");
      // 点进「查规则」一级页（头像第一项），用户自己去翻原文
      a.addEventListener("click", () => go("rules"));
      cl.appendChild(a);
    });
    if (!(v.citations || []).length) {
      // 引用取不到时退化成纯条号列表（不能让"去哪看"这条线索断掉）
      (v.clauses || []).forEach((cid) => {
        const s = el("span", "prac-clause");
        s.textContent = "第 " + cid + " 条";
        cl.appendChild(s);
      });
    }
    box.appendChild(cl);
  }

  const next = el("button", "btn");
  next.textContent = (PRAC.idx + 1 >= PRAC.total) ? "看成绩" : "下一题 →";
  next.addEventListener("click", () => {
    PRAC.idx++;
    PRAC.picked = null;
    PRAC.verdict = null;
    pracDraw();
  });
  box.appendChild(next);
  return box;
}

async function pracPick(q, key) {
  if (PRAC.busy || PRAC.picked) return;
  PRAC.busy = true;
  PRAC.picked = key;
  const host = pracHost();
  const btns = host ? host.querySelectorAll(".prac-opt") : [];
  btns.forEach((b) => { b.disabled = true; });
  try {
    const v = await post("/api/practice", { id: q.id, choice: key });
    PRAC.verdict = v;
    if (v.ok) PRAC.right++;
  } catch (e) {
    // ⚠️ 提交失败要把已禁用的按钮放开，否则用户卡死在这一题（比报错更糟）
    PRAC.picked = null;
    btns.forEach((b) => { b.disabled = false; });
    toast("提交失败，再试一次：" + (e && e.message ? e.message : ""));
  } finally {
    PRAC.busy = false;
  }
  pracDraw();
}

function pracResult() {
  const host = pracHost();
  if (!host) return;
  host.innerHTML = "";
  const n = PRAC.total || 1;
  const pct = Math.round((PRAC.right / n) * 100);
  const box = el("div", "prac-done");
  const h = el("div", "prac-done-h");
  h.textContent = "这一轮答对 " + PRAC.right + " / " + n + "（" + pct + "%）";
  box.appendChild(h);
  const p = el("div", "note");
  // ⚠️ 不评判用户、不说"你很差"。措辞只说下一步 —— 与产品定位一致（"敢下场"）。
  p.textContent = pct === 100 ? "全对。可以换一批再练。"
    : (pct >= 60 ? "不错。错的那几题点进去看一眼原文，比再刷十道有用。"
                 : "先把错题的原文读一遍，再来一轮会更顺。");
  box.appendChild(p);
  const again = el("button", "btn");
  again.textContent = "再来一轮";
  again.addEventListener("click", () => pracStart());
  box.appendChild(again);
  host.appendChild(box);
}


function reviewDue(n) {
  /* 只考"有来源的笔记" —— 手写的那句"队里说这样打"没有唯一答案，
     拿出来当题目是刁难用户。 */
  if (!n.from || !n.from.q) return false;
  /* 满档 = 已"毕业"，不再进队列（见 reviewDone 的说明） */
  if ((n.rev || 0) >= REVIEW_STEPS.length) return false;
  /* ⚠️ 基准必须是**上次复习时间**，不是创建时间（2026-10-07 修）。
     原来用 createdAt，于是间隔退化成"创建后的固定日期"：
       · D0+10 才记的笔记，rev=0/间隔1天 ⇒ 打开就在队列里；
       · 答对后 rev=1/间隔3天，而 now-createdAt=10 天 ≥ 3 ⇒ **当场又到期**。
     `rev` 完全没起到"下一次什么时候考"的闸门作用。
     lastRev 缺省退回 createdAt —— 老笔记迁移后仍能从创建日算起，不会立刻全量涌进来。 */
  const last = n.lastRev || n.createdAt || n.ts || Date.now();
  const step = REVIEW_STEPS[Math.min(n.rev || 0, REVIEW_STEPS.length - 1)];
  return (Date.now() - last) >= step * 86400000;
}
function reviewQueue() {
  return liveNotes().filter(reviewDue).sort((a, b) => (b.ts || 0) - (a.ts || 0));
}
/* 答完一次 = 记熟了，往后推一档。
   ⚠️ 封顶这一档（rev 走完 REVIEW_STEPS）之后**不该再出现在队列里** ——
   注释原来写的是"封顶就不再出现"，但原实现基准是创建时间、步长被 min()
      钳在 30 天，而 now-createdAt 单调增长 ⇒ 一旦满档，这条笔记**永久滞留**：
      每开一次笔记页就出现一次，点「答对了」rev 被卡住不变 ⇒ 永远出不去。
      修法是让 reviewDue 在 rev 满档时直接返回 false（真正"毕业"）。 */
function reviewDone(n, ok) {
  n.rev = Math.min((n.rev || 0) + (ok ? 1 : 0), REVIEW_STEPS.length);
  if (!ok) n.rev = Math.max((n.rev || 0) - 1, 0);   // 没答对 = 退回上一档重来
  n.lastRev = Date.now();      // ★ 调度基准：上次复习的时刻
  n.ts = Date.now();            // 仅用于列表排序（最近动过的在前）
  saveNotes();
}

/* ---- 复习卡片：一屏一张，问在上、答案藏在下 ---- */
let _rv = { list: [], i: 0, show: false };
function viewReview(onDone) {
  _rv = { list: reviewQueue(), i: 0, show: false, onDone: onDone };
  paintReview();
}
function paintReview() {
  const host = $("rvBody"); if (!host) return;
  host.innerHTML = "";
  if (!_rv.list.length) {
    const h = el("div", "nt-empty");
    h.textContent = "现在没有该复习的笔记。记下的东西过一天会自己回来找你。";
    host.appendChild(h);
    if (_rv.onDone) _rv.onDone();
    return;
  }
  const n = _rv.list[Math.min(_rv.i, _rv.list.length - 1)];
  const idx = Math.min(_rv.i, _rv.list.length - 1);

  /* 进度：几个字，不是进度条 —— 别把 5 条笔记做成一个仪表盘。
     右侧挂一个「收起」：点进来之后必须出得去，否则这个功能成了陷阱。 */
  const bar = el("div", "rv-bar");
  bar.textContent = `第 ${idx + 1} / ${_rv.list.length} 条`;
  const quit = el("button", "rv-quit");
  quit.textContent = "收起";
  quit.title = "先不复习了，回到笔记列表";
  quit.addEventListener("click", () => { if (_rv.onDone) _rv.onDone(); });
  bar.appendChild(quit);
  host.appendChild(bar);

  const q = el("div", "rv-q");
  q.textContent = (n.title || "").trim() || (n.from && n.from.q) || "这条笔记";
  host.appendChild(q);

  /* 先给线索（依据的条款），再要答案 —— 这是"能答上来"的提示，
     不是提示答案。 */
  const clue = el("div", "rv-clue");
  const cites = (n.from && n.from.cites) || [];
  clue.textContent = cites.length ? `当时依据：${cites.join("、")}` : "当时没有引用条款";
  host.appendChild(clue);

  const reveal = el("button", "rv-reveal");
  reveal.textContent = _rv.show ? "收起答案" : "看我的答案";
  host.appendChild(reveal);

  const ans = el("div", "rv-a" + (_rv.show ? " on" : ""));
  if (_rv.show) {
    const body = el("div", "rv-ab");
    if ((n.fmt === "html" ? n.rich : n.body) || (n.body || "").trim()) {
      /* 渲染富文本，而不是 textContent。
         ⚠️ 但**必须先过 noteSanitize** —— 这是本文件里正文第二次变成
         innerHTML 的地方（第一次在 noteNode）。复习视图的"答案"是要
         跟用户自己说出的内容逐字对比的，用 textContent 的话加粗、列表
         全部消失，对比就失去意义。
         ⚠️ 为什么这里要重新过一遍而不是直接用 n.rich：n.rich 写入时
         已经 sanitize 过了，但它来自 localStorage —— 同一份数据可能
         是被别的脚本改过的，而这是最后一道渲染前的关口，重复过滤
         的成本可以忽略，漏掉一次的代价是任意代码执行。 */
      const hasRich = n.fmt === "html" && !!n.rich;
      body.innerHTML = hasRich ? noteSanitize(n.rich)
        : `<div>${esc(n.body || "")}</div>`;
    } else {
      body.textContent = "（当时没写正文）";
    }
    ans.appendChild(body);
    if (n.from && n.from.q) {
      const back = el("button", "rv-src");
      back.textContent = `回到那次对话：${n.from.q.slice(0, 18)}${n.from.q.length > 18 ? "…" : ""}`;
      back.addEventListener("click", () => gotoNoteFrom(n, cites[0]));
      ans.appendChild(back);
    }
    /* 判定：只问一句「刚才答对了没」，不多给按钮 ——
       判定标准由他脑子里那套规则决定，不是由我们决定。 */
    const row = el("div", "rv-act");
    [["答对了", true], ["没答对", false]].forEach(([lab, ok]) => {
      const b = el("button", "rv-btn" + (ok ? " ok" : ""));
      b.textContent = lab;
      b.addEventListener("click", () => {
        reviewDone(n, ok);
        _rv.i++; _rv.show = false;
        if (_rv.i >= _rv.list.length) { toast("复习完了"); if (_rv.onDone) _rv.onDone(); }
        paintReview();
      });
      row.appendChild(b);
    });
    ans.appendChild(row);
  }
  host.appendChild(ans);

  reveal.addEventListener("click", () => { _rv.show = !_rv.show; paintReview(); });
  if (!_rv.show) host.appendChild(el("div", "rv-tip")).textContent = "先自己说出答案，再展开对比";
}

/* ==========================================================================
   我的笔记（整页）
   ==========================================================================
   ⚠️ 与对话页右栏那个面板**是同一个本子**（同一份 NT.items / localStorage）。
      这里不是第二套笔记系统 —— 数据、节点渲染（noteNode）全是共用的，
      区别只在"什么时候打开"：
        · 对话页右栏 = 边聊边记，摘完立刻能看一眼
        · 这一页     = 回头翻本子（`#/notes`，左下角头像菜单里进）
   ⚠️ 为什么非要有这一页（owner 2026-10-06）：
      他这一轮把「笔记」当成**新功能**提出来 —— 而这功能早就存在（提交 7cdc473）。
      他不知道，因为在此之前它**只有一个入口，且只在对话页、只在够宽的窗口下才出现**。
      **一个用户找不到的功能等于不存在。**

   ---------------------------------------------------------------------
   2026-10-07：这一页从"一个搜索框"改成了**三个视图 + 复习**。
   改的理由不是"功能多更好"，而是上一版笔记有三个致命断点：
     ① **记了就找不到** —— 只有时间排序 ⇒「我关于掩护记过什么」翻不出来
        （⇒ 按规则章聚合：这是别的笔记软件抄不走的分类轴）
     ② **记了不记得住** —— 只进不出 ⇒（⇒ 主动回忆，用他自己的笔记出题）
     ③ **删了找不回 / 清缓存就没了** ⇒（⇒ 回收站 + 导出导入）
   三个断点各自对应"记 → 找 → 记住 → 不怕丢"，缺一个，笔记就只是收件箱。 */
const NPQ = { q: "", mode: "time" };   // mode: time | chapter | trash

function viewNotes() {
  sweepTrash();                             // 进来先清 30 天前的
  const wrap = el("div", "wrap notes-page");
  wrap.innerHTML = `
    <div class="np-modes" role="tablist" aria-label="笔记视图">
      <button class="np-mode" data-m="time" role="tab">按时间</button>
      <button class="np-mode" data-m="chapter" role="tab">按规则章</button>
      <button class="np-mode" data-m="trash" role="tab">回收站</button>
      <!-- ⚠️ 必须是 button 而不是 span（2026-10-07 修）：这是「主动回忆」复习的
           **唯一入口**，而 span 不可聚焦 ⇒ 纯键盘用户永远进不去。
           app.css 里早有 .np-rev:focus-visible 样式，说明本来就打算让它可聚焦，
           只是元素上少了语义。放在 tablist 里也只有它不可 Tab，算半个例外。 -->
      <button class="np-rev" id="ntRev" hidden></button>
    </div>
    <div class="np-bar">
      <input id="ntQ" class="np-q" type="search" placeholder="搜笔记" autocomplete="off"
             aria-label="搜索笔记">
      <span class="np-count" id="ntCount"></span>
      <span class="np-saved" id="ntSaved">已保存</span>
      <button class="btn sm" id="ntNew">＋ 新建一条</button>
      <span class="np-io">
        <button class="btn sm ghost" id="ntExp" title="导出一个文本文件，自己留着">导出</button>
        <button class="btn sm ghost" id="ntImp" title="从之前导出的文件恢复">导入</button>
        <input type="file" id="ntFile" accept=".txt,.json,text/plain,application/json" hidden>
      </span>
    </div>
    <div id="rvBody" class="rv-body" hidden></div>
    <div id="notesBody"></div>`;
  mount(wrap);

  const q = wrap.querySelector("#ntQ");
  q.value = NPQ.q;
  q.addEventListener("input", () => { NPQ.q = q.value.trim(); paintNotesPage(); });
  wrap.querySelector("#ntNew").addEventListener("click", () => {
    NT.items.unshift(newNote({ body: "" }));
    saveNotes();
    NPQ.mode = "time";                        // 新建后切回来，否则在回收站里新建很怪
    paintNotesPage();
    /* 重绘后 DOM 是新的，得重新查一次 —— 而且要用 focusNoteBody，
       旧的 setSelectionRange 是 textarea 专有的（见该函数注释）。 */
    const body2 = $("notesBody");
    if (body2) focusNoteBody(body2);
  });
  wrap.querySelector("#ntExp").addEventListener("click", exportNotes);
  wrap.querySelector("#ntImp").addEventListener("click", () => wrap.querySelector("#ntFile").click());
  wrap.querySelector("#ntFile").addEventListener("change", e => {
    importNotes(e.target.files && e.target.files[0]);
    e.target.value = "";                     // 允许连续导入同一个文件
  });
  wrap.querySelectorAll(".np-mode").forEach(b => b.addEventListener("click", () => {
    NPQ.mode = b.dataset.m;
    paintNotesPage();
  }));
  paintNotesPage();
}

function paintNotesPage() {
  const body = $("notesBody"); if (!body) return;
  const rvb = $("rvBody");
  if (rvb) rvb.hidden = true;
  body.innerHTML = "";

  if (NT.broken) {
    const warn = el("div", "nt-empty");
    warn.textContent = "笔记数据的结构无法识别。为免覆盖原件，已停止写入。请先导出原始数据保存，再联系维护者恢复。";
    body.appendChild(warn);
    const dump = el("button", "btn sm nt-add");
    dump.textContent = "导出损坏的原始数据";
    dump.addEventListener("click", () => downloadText(NT.recovery, "frisbee-notes-recovery.txt"));
    body.appendChild(dump);
    return;
  }

  document.querySelectorAll(".np-mode").forEach(b => {
    const on = b.dataset.m === NPQ.mode;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });

  const q = (NPQ.q || "").toLowerCase();
  const live = liveNotes();

  /* ---- 复习入口：只在"确实有该复习的"时候才出现 ----
     一个永远亮着、点进去却是空的按钮，比没有这个按钮更糟。 */
  const due = reviewQueue().length;
  const rev = $("ntRev");
  if (rev) {
    rev.hidden = !due || NPQ.mode === "trash";
    rev.textContent = due ? `${due} 条该复习` : "";
    rev.onclick = () => {
      if (rvb) rvb.hidden = false;
      body.innerHTML = "";
      viewReview(() => { if (rvb) rvb.hidden = true; paintNotesPage(); });
      rvb.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  }

  /* ---- 回收站：单独一档，不与正常笔记混排 ---- */
  if (NPQ.mode === "trash") {
    const tr = trashedNotes();
    const cnt0 = $("ntCount");
    if (cnt0) cnt0.textContent = `回收站 ${tr.length} 条 · ${TRASH_DAYS} 天后自动清空`;
    if (!tr.length) {
      body.appendChild(el("div", "nt-empty")).textContent =
        "回收站是空的。删掉的笔记会先放到这儿，30 天后自动清掉。";
      return;
    }
    const tl = el("div", "trash-list");
    tr.forEach(n => {
      const row = el("div", "tr-item");
      const info = el("div", "tr-info");
      info.textContent = (n.title || (n.from && n.from.q) || "（无标题）");
      const ago = Math.max(1, Math.round((Date.now() - n.del) / 86400000));
      const sub = el("div", "tr-sub");
      sub.textContent = `${ago} 天前删除`;
      info.appendChild(sub);
      const back = el("button", "btn sm ghost");
      back.textContent = "恢复";
      back.addEventListener("click", () => { restoreNote(n); toast("已恢复"); paintNotesPage(); });
      const gone = el("button", "btn sm ghost danger");
      gone.textContent = "彻底删除";
      gone.addEventListener("click", () => {
        undoToast("已彻底删除，这条救不回来了", "知道了", () => {});
        purgeNote(n); paintNotesPage();
      });
      row.appendChild(info); row.appendChild(back); row.appendChild(gone);
      tl.appendChild(row);
    });
    body.appendChild(tl);
    return;
  }

  /* 搜索要能搜到"来源" —— 用户记笔记时记得的是"当时问的那句"，
     不是自己打的标题。 */
  const items = q
    ? live.filter(n => ((n.title || "") + noteSearchable(n) + ((n.from && n.from.q) || "")
        + ((n.from && n.from.cites) || []).join(" ")).toLowerCase().includes(q))
    : live;

  const cnt = $("ntCount");
  if (cnt) {
    const star = live.filter(n => n.star).length;
    const trN = NT.items.length - live.length;
    cnt.textContent = q
      ? `${items.length} / ${live.length} 条`
      : `共 ${live.length} 条${star ? ` · 重点 ${star}` : ""}`
        + (trN ? ` · 回收站 ${trN}` : "") + " · 只存在这台设备上";
  }

  if (!items.length) {
    const h = el("div", "nt-empty");
    h.textContent = q
      ? "没有匹配的笔记。"
      : "还没有笔记。在对话里点回答下面的「记进笔记」，或者点右上角「＋ 新建一条」—— "
        + "随手记下你的理解、队里的约定、上次争论的结论。都只存在这台设备上。";
    body.appendChild(h);
    return;
  }
  renderNoteList(body, items, paintNotesPage, NPQ.mode === "chapter");
}

function dayLabel(ts) {
  const d = new Date(ts), now = new Date();
  const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (same(d, now)) return "今天";
  if (same(d, new Date(now.getTime() - 86400000))) return "昨天";
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}
function paintHist() {
  const box = $("hist"); if (!box) return;
  box.innerHTML = "";
  // 「历史对话」那一行右侧的条数。**统计口径必须和列表一致**
  // （空会话不算 —— 它们本来就不渲染）。
  const allWithMsgs = CH.sessions.filter(s => s.msgs.length);
  const wc = $("wsCnt");
  if (wc) wc.textContent = allWithMsgs.length ? String(allWithMsgs.length) : "";
  const q = (CH.histQ || "").toLowerCase();
  let items = allWithMsgs;
  if (CH.sortAsc) items = items.slice().reverse();   // 最新优先 → 最早优先
  if (q) {
    items = items.filter(s => {
      const t = (s.title || "").toLowerCase();
      if (t.includes(q)) return true;
      // 只搜标题会漏掉「后来补了一句关键词」的情况，这里连正文一起搜
      return (s.msgs || []).some(m => (m.content || "").toLowerCase().includes(q)
        || ((m.payload && m.payload.rewritten) || "").toLowerCase().includes(q));
    });
  }
  const hq = $("histQ");
  if (hq && hq.value !== (CH.histQ || "")) hq.value = CH.histQ || "";
  if (!items.length) {
    // 空态不写任何说明：列表空着就是空着，写一句"问完会留在这里"是自说自话
    box.innerHTML = q ? `<div class="hist-empty">没有匹配的对话</div>` : "";
    return;
  }
  let last = "";
  items.forEach(s => {
    const lab = dayLabel(s.ts);
    if (lab !== last) { const g = el("div", "grp"); g.textContent = lab; box.appendChild(g); last = lab; }
    const row = el("div", "hitem" + (s.id === CH.current ? " on" : ""));
    const tx = el("div", "tx"); tx.textContent = s.title; row.appendChild(tx);
    const del = el("button", "del"); del.textContent = "×"; del.title = "删除";
    del.addEventListener("click", (e) => { e.stopPropagation(); removeSession(s.id); });
    row.appendChild(del);
    // 历史条目在侧栏，任何页面点它都要回到对话页
    row.addEventListener("click", () => {
      CH.current = s.id; CH.active = null; saveSessions();
      if (parseHash().main !== "chat") go("chat"); else paintChat();
      closeNav();
    });
    box.appendChild(row);
  });
}
function removeSession(id) {
  const i = CH.sessions.findIndex(s => s.id === id);
  if (i < 0) return;
  CH.sessions.splice(i, 1);
  if (!CH.sessions.length) CH.sessions = [newSession()];
  if (CH.current === id) { CH.current = CH.sessions[0].id; CH.active = null; }
  saveSessions(); paintHist();
  if (parseHash().main === "chat") paintChat();
}
/* 侧栏「＋ 新对话」：任何页面点它都回对话页并开新会话 */
function startNewChat() {
  CH.sessions.unshift(newSession());
  CH.current = CH.sessions[0].id; CH.active = null; CH.stick = true;
  // 新对话回到"跟随内容"的默认开合状态 —— 上一个会话里手动收起过右栏，
  // 不该把那个选择带到全新的对话里（那时还没有任何引用可看）。
  NT.open = null;
  saveSessions();
  if (parseHash().main !== "chat") go("chat"); else paintChat();
  closeNav();
}
function setTitle(s, q) {
  if (s.title === "新对话") { s.title = q.length > 18 ? q.slice(0, 18) + "…" : q; s.ts = Date.now(); }
}

/* 空态（2026-10-15 重做；2026-10-20 删过；2026-10-22 owner 要求恢复介绍）。

   ⚠️ 这段历史别删，它解释"为什么加回来、又为什么不能乱加"：
      · 2026-10-15：加过 12 条示例 → 收敛成 3 条，因为示例在替用户做决定；
      · 2026-10-20：owner 要求全删（"用户带着问题来"）→ 只留一行信任说明；
      · 2026-10-22：owner 看预览说"点进去只是个大白板，太难看了"
        ⇒ 恢复标题 + 说明 + 3 条入口示例。**这次是 owner 明确要的介绍，不是招商页。**

   ⚠️ 两条 2026-10-20 的约束仍然有效，别破：
      ① 情绪那条（"被打爆了"）不许回来 —— 那是替用户决定情绪；
      ② 示例只是入口：点了**填入输入框 + 聚焦，不直接发送**。
         直接发出去等于替他决定了，而且他没机会改。

   ⚠️ 副标题必须逐段构造 DOM，不能 innerHTML 多行拼接：
      源码里的换行/缩进会变成真实空格，560px 下把标点甩到行首（2026-10-15 踩过）。
      另外 `.empty b` 会把 <b> 变 block，`.hero-sub b` 已显式 inline 压回去。 */
/* 首页三问：每天 00:00（本地）换一组，同一天内不变。

   ⚠️ 为什么是"按天"，不是"每次登录 / 刷新"：
      · 刷新就换 = 用户想回头点刚才没点的那条，找不到了；页面像老虎机。
      · 按天换 = 同一天稳定，第二天来是新的；不用定时器，下次打开自然生效。
      · 窗口一直开着跨过零点也不换 —— 不打断正在读/正在打字的用户，下次加载再换。

   ⚠️ 为什么是 8 组（24 条）：
      这个产品的用户大多周末打开。7 组正好和星期对齐 —— 每周六看到同一组。
      8 与 7 互质，周末那组每周往后错一组，8 周才轮一遍；
      每天打开的人 8 天一轮。（6 组也可以；8 组只是把周期拉长一点。）

   ⚠️ 同一天所有人看到同一组（不按人随机）：
      "今天首页是什么"可复现、可排查；按人随机要存种子，收益不值。
      换的边界是**本地日历日**（Date.UTC(本地年月日)），不是 UTC ——
      否则中国用户会在早上 8 点看到换题。 */
const HERO_EPOCH = Date.UTC(2026, 9, 22);   // 上线日 = 第 0 组
const HERO_SETS = [
  [
    "脚踩得分线接住盘，算得分吗？",
    "盘出界了，到底该从哪儿掷进来？",
    "我叫了暂停，但队友还没站稳，算成立吗？",
  ],
  [
    "读秒数到几算计满？",
    "轴心点确立后，脚还能挪吗？",
    "接盘时踩到边线，算出界吗？",
  ],
  [
    "防守时挡住跑位，算犯规吗？",
    "两个人同时接住盘，算谁的？",
    "得分后能换人吗？下一分谁开盘？",
  ],
  [
    "盘落地前我接住了，还算落地吗？",
    "我叫了犯规，但盘已经传出去了，怎么办？",
    "对方数秒太快，我能叫什么？",
  ],
  [
    "最后时刻故意犯规拖时间，划算吗？",
    "盘碰到场外的东西，算出界吗？",
    "开盘没飞过中线，从哪儿开始？",
  ],
  [
    "受伤了，比赛怎么继续？",
    "精神暂停是什么，什么时候能叫？",
    "得分后全队击掌庆祝，违规吗？",
  ],
  [
    "我在界外把盘救回来，算我们拿到盘吗？",
    "传盘被断了，下一回合从哪儿开始？",
    "比赛开始前，怎么决定谁先开盘？",
  ],
  [
    "防守队员站在我面前挥手，算干扰吗？",
    "我接盘后跑了两步才停，算走步吗？",
    "开盘我可以直接接住吗？",
  ],
];

function heroSetFor(now) {
  const d = now || new Date();
  const day = Math.round(
    (Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - HERO_EPOCH) / 86400000
  );
  const i = ((day % HERO_SETS.length) + HERO_SETS.length) % HERO_SETS.length;
  return HERO_SETS[i];
}

/* ==================== 首页 hero（DS-HOME-R11 / R1.1，2026-10-24） ====================
   构图与飞盘动效移植自 GPT 的 R1.1 可运行参考稿（reference.js 的 playFlight）。

   ⚠️ 三条边界（改之前先看任务文件 DeepSeek-接入任务.md）：
     1. **路径是飞盘轨迹的唯一几何来源**：getPointAtLength 一次取样生成 WAAPI 帧，
        不要另写逐帧 JS，也不要手算第二套 translate。
     2. **只演一次**：沿用生产原有的会话级 key（shultimate.heroIntroSeen）；
        点示例只填入输入框、不重播；只有明确的「重播」才重来。
     3. **所有监听都从 root 上找、在 cleanup 里拆**：切路由 / 新对话 / 重画 hero
        时调用 disposeHero()，否则 matchMedia 和 rAF 会越积越多。

   ⚠️ v2 的 bindHeroParallax / replayTrace 已删除，别加回来：
      参考稿明确不要鼠标视差，也不要示例点击重播。 */

let heroFlightCleanup = null;

function disposeHero() {
  if (!heroFlightCleanup) return;
  try { heroFlightCleanup(); } catch (e) { /* 清理失败不能挡住换页 */ }
  heroFlightCleanup = null;
}

/* 把飞行逻辑封装在 hero 根节点内：只查 root 下的 SVG / 重播按钮，
   避免全局 #flightPath、#replay 撞上旧 hero 或其它页面。 */
function bindHeroFlight(root) {
  const home = root;
  const art = root.querySelector(".flight-art");
  const path = root.querySelector(".flight-path");
  const disc = root.querySelector(".flight-disc");
  const replay = root.querySelector(".flight-replay");
  const reduceQ = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;
  const FLIGHT = { duration: 1200, delay: 100, easing: "cubic-bezier(.23,1,.32,1)" };
  let animations = [];
  let disposed = false;

  const cancel = () => {
    animations.forEach(a => { try { a.cancel(); } catch (e) { /* 已结束 */ } });
    animations = [];
  };
  // 减少动态效果：停在路径终点（参考稿的降级含义），不做飞行/描边。
  const settle = () => {
    if (!path || !disc) return;
    const end = path.getPointAtLength(path.getTotalLength());
    path.style.clipPath = "none";
    path.style.opacity = "1";
    disc.style.transform = `translate(${end.x}px,${end.y}px)`;
  };

  function play(restart) {
    if (disposed || !path || !disc || !art || !disc.animate) return;
    cancel();
    if (restart) {
      // 两个状态互斥：重播时先清掉"已静置"，否则 class 会同时存在。
      home.classList.remove("is-intro", "is-settled");
      void home.offsetWidth;              // 让同名 CSS 动画能重新触发
      home.classList.add("is-intro");
    }
    if (reduceQ && reduceQ.matches) {
      settle();
      animations.push(art.animate([{ opacity: .35 }, { opacity: 1 }], { duration: 160 }));
      return;
    }
    const length = path.getTotalLength();
    const end = path.getPointAtLength(length);
    const keyframes = Array.from({ length: 61 }, (_, index) => {
      const t = index / 60;
      const point = path.getPointAtLength(length * t);
      return {
        offset: t,
        transform: `translate(${point.x}px,${point.y}px) scale(${.62 + .38 * t})`,
        opacity: Math.min(1, t * 10),
      };
    });
    disc.style.transform = `translate(${end.x}px,${end.y}px)`;
    animations.push(disc.animate(keyframes, { ...FLIGHT, fill: "backwards" }));
    animations.push(path.animate([
      { clipPath: "inset(0 100% 0 0)", opacity: .25 },
      { clipPath: "inset(0 0 0 0)", opacity: 1 },
    ], { ...FLIGHT, delay: 160, fill: "backwards" }));
  }

  const onReplay = () => play(true);
  const onReduceChange = () => play(true);
  if (replay) replay.addEventListener("click", onReplay);
  if (reduceQ && reduceQ.addEventListener) reduceQ.addEventListener("change", onReduceChange);

  // 首屏：is-intro 的 CSS 动画已由类名启动，这里只负责飞盘；
  // is-settled 则直接把飞盘钉在路径终点（刷新/二次进入不重播）。
  if (home.classList.contains("is-intro")) requestAnimationFrame(() => play(false));
  else settle();

  return function cleanup() {
    disposed = true;
    cancel();
    if (replay) replay.removeEventListener("click", onReplay);
    if (reduceQ && reduceQ.removeEventListener) reduceQ.removeEventListener("change", onReduceChange);
  };
}

function heroNode() {
  const wrap = el("div", "hero");

  const introKey = "shultimate.heroIntroSeen";
  let playIntro = true;
  try {
    playIntro = sessionStorage.getItem(introKey) !== "1";
    if (playIntro) sessionStorage.setItem(introKey, "1");
  } catch (e) { /* 隐私模式禁用存储时，保留完整首屏体验 */ }
  wrap.classList.add(playIntro ? "is-intro" : "is-settled");

  const grid = el("div", "hero-grid");
  const copy = el("div", "hero-copy");

  const eyebrow = el("p", "hero-eyebrow");
  eyebrow.innerHTML = `<span aria-hidden="true"></span>FROM THE FIELD. FOR THE FIELD.`;
  copy.appendChild(eyebrow);

  // 标题：视觉仍是两行（参考稿的 <br>），但源码不拆散使命句 ——
  // aria-label 保留完整一句（产品守卫按整串找它）。
  const h1 = el("h1", "hero-h");
  h1.innerHTML = `<span class="hl"><span>飞盘规则，</span></span>`
    + `<span class="hl"><span>直接<span class="title-emphasis">问明白。</span></span></span>`;
  h1.setAttribute("aria-label", "飞盘规则，直接问明白。");
  copy.appendChild(h1);

  // 描述用参考稿原文；"只依据 WFDF 规则原文" 仍在输入区 meta 里。
  const sub = el("p", "hero-sub");
  sub.innerHTML = `把场上那一刻，用自己的话说出来。<br>从规则原文里，找到说得清的依据。`;
  copy.appendChild(sub);

  const proof = el("div", "hero-proof");
  proof.innerHTML = `<span class="hero-proof-mark" aria-hidden="true">↳</span><span>依据可核对，不确定会直说。</span>`;
  copy.appendChild(proof);
  grid.appendChild(copy);

  // 飞盘与场地图：几何逐项来自参考稿（viewBox / matrix / 路径 d / 飞盘轮廓）。
  // ⚠️ 图形只是装饰示意，不写条款号、不呈现具体判定（参考稿的明确要求）。
  const fig = el("figure", "flight-figure");
  fig.setAttribute("aria-label", "飞盘掠过抽象场地图，沿紫色弧线停稳。装饰示意，不代表具体规则判断。");
  fig.innerHTML = `
    <div class="figure-top"><span>THE FLIGHT / 001</span><button type="button" class="flight-replay" aria-label="重播飞盘开场动画">重播 <span aria-hidden="true">↺</span></button></div>
    <svg class="flight-art" viewBox="0 0 500 410" fill="none" aria-hidden="true" focusable="false">
      <defs><pattern id="heroHatch" width="7" height="7" patternUnits="userSpaceOnUse"><path d="M0 7L7 0" stroke="#deded7" stroke-width=".7"/></pattern></defs>
      <g class="field-plane" transform="matrix(1 -.25 .52 .62 -29 241)">
        <rect x="40" y="0" width="355" height="190" fill="#f3f3ee" stroke="#bcbdb5"/>
        <rect x="40" y="0" width="64" height="190" fill="url(#heroHatch)"/>
        <rect x="331" y="0" width="64" height="190" fill="url(#heroHatch)"/>
        <path d="M104 0V190M331 0V190" stroke="#bcbdb5"/>
        <path d="M217.5 0V190M40 95H395" stroke="#d1d2c9" stroke-dasharray="3 6"/>
        <path d="M40-9V-18M395-9V-18M40-14H395" stroke="#cdcec5"/>
        <circle cx="217.5" cy="95" r="4" fill="#f9f9f6" stroke="#bcbdb5"/>
      </g>
      <g class="construction" stroke="#d5d5ce" stroke-width=".8"><path d="M350 117V310" stroke-dasharray="3 5"/><ellipse cx="350" cy="310" rx="18" ry="5"/><path d="M18 349H39M28.5 338V359M455 43H476M465.5 32V54"/></g>
      <path class="flight-echo" d="M42 318C136 309 116 109 350 117" stroke="#ddd6ef" stroke-width="1"/>
      <path class="flight-path" d="M42 303C136 294 116 94 350 117" stroke="#7B4EE6" stroke-width="1.8"/>
      <circle cx="42" cy="303" r="4" fill="#f9f9f6" stroke="#7B4EE6"/>
      <g class="flight-disc" style="transform:translate(350px,117px)">
        <g transform="rotate(-18)">
          <path d="M-82 0C-82 22-46 38 0 38S82 22 82 0" fill="#e7e0f8" stroke="#7B4EE6" stroke-width="1.8"/>
          <ellipse rx="82" ry="29" fill="#f9f9f6" stroke="#7B4EE6" stroke-width="1.8"/>
          <ellipse rx="69" ry="21" stroke="#7B4EE6" stroke-width="1"/>
          <ellipse rx="54" ry="15" stroke="#7B4EE6" stroke-width=".7"/>
          <path d="M-39-10C-10-18 20-15 39-9" stroke="#7B4EE6" stroke-width=".7"/>
          <path d="M-68 16C-35 36 32 36 67 16" stroke="#7B4EE6" stroke-width=".7"/>
        </g>
      </g>
    </svg>
    <figcaption><span class="figure-dot" aria-hidden="true"></span> 场上有争议，场边有依据。<span class="caption-end" aria-hidden="true">SHULTIMATE</span></figcaption>`;
  grid.appendChild(fig);
  wrap.appendChild(grid);

  const section = el("section", "questions");
  section.setAttribute("aria-labelledby", "heroQuestionsTitle");
  const head = el("div", "section-heading");
  head.innerHTML = `<h2 id="heroQuestionsTitle">从刚才那一回合开始</h2><span>QUICK QUESTIONS</span>`;
  section.appendChild(head);

  const list = el("div", "question-grid");
  heroSetFor(new Date()).forEach((q, i) => {
    const b = el("button", "question");
    b.type = "button";
    const index = el("span", "question-number");
    index.textContent = String(i + 1).padStart(2, "0") + " /";
    const question = el("span", "question-text");
    question.textContent = q;
    const arrow = el("span", "question-arrow");
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "↗";
    b.append(index, question, arrow);
    b.addEventListener("click", () => {
      const ta = $("q");
      if (!ta) return;
      ta.value = q;
      grow(ta);
      ta.focus();
      try { ta.setSelectionRange(q.length, q.length); } catch (e) { /* 老浏览器 */ }
      // 参考稿：示例填入**不重播**开场动画（高频动作不触发装饰动效）。
    });
    list.appendChild(b);
  });
  section.appendChild(list);

  const or = el("div", "hero-or");
  or.innerHTML = `<span>没有合适的？</span> 直接在下面描述场上的情况。`;
  section.appendChild(or);
  wrap.appendChild(section);

  return wrap;
}

function citesRow(cites) {
  if (!cites || !cites.length) return null;
  const row = el("div", "cites");
  cites.forEach(c => {
    const b = el("button", "cchip" + (c.id === CH.active ? " on" : ""));
    b.dataset.cid = c.id;
    const label = c.type === "definition" ? `术语「${c.term || ""}」`
      : c.type === "preface" ? "前言"
      : c.type === "appendix" ? (c.label || `附录 ${c.id}`) : c.id;
    const cos = (typeof c.cos === "number") ? `<span class="cos">${c.cos.toFixed(2)}</span>` : "";
    b.innerHTML = `<b>${esc(label)}</b>${cos}`;
    b.addEventListener("click", () => selectCite(c.id));
    row.appendChild(b);
  });
  return row;
}
/* 每条回答的反馈：**一行安静的问题 + 两个文字按钮。**
 *
 * ⚠️⚠️ 2026-10-15 改过一次形态，两边的理由都要留着，否则下一轮会来回翻烧饼。
 *
 * 【为什么原来是"图标 + hover 才出现"】
 *   2026-10-06 owner 报过：「提的每一个问题都写个『这东西』，感觉很烦」+
 *   「我红框勾起来的『这个回答有用吗』，我觉得要删掉」。
 *   他说得对 —— **那行文案在每一条回答下面重复一次**。
 *   ⇒ 改成图标、无文案、hover 才出现（ChatGPT / Claude 的工具条做法）。
 *
 * 【为什么现在又要改成"常驻可见"】
 *   实测：`data/feedback.jsonl` **上线至今一条都没有**。
 *   而"有没有让人弄明白"是产品最重要的信号之一（判据见 docs/product.md）。
 *   ⇒ 一个收集不到数据的测量工具，等于没有测量。
 *      按钮藏起来 = 用户不知道那儿能点 = 数据永远是 0。
 *
 * 【两边的诉求其实不冲突 —— 这是这次的解法】
 *   owner 嫌的是「**每条回答都喊一句解释性文案**」，
 *   不是「不该让人看见」。
 *   ⇒ 所以：
 *     · **问题那一行常驻**（`.fb-q`），但只有一行、字号小、颜色淡 ——
 *       它回答的是"我刚才那句有没有被听懂"，而用户**每一条都真的关心这件事**
 *       （比"复制回答"更值得占那一行）
 *     · **展开面板（选原因那张卡）仍然只在交互后出现** ——
 *       那才是"每条都摊开"会烦人的部分
 *   ⇒ 真正的差别：**常驻的是"问题"，不是"解释"。**
 *
 * 【为什么不用 👍/👎 图标了】
 *   拇指图标不携带"弄明白了吗"这层意思 —— 用户看到 👍 会想"这是让我夸它吗"。
 *   owner 的原话是「就是让他的选择是正确和错误」那一类**明确的二选一**。
 *   ⇒ 直接用文字：「懂了」/「还是没明白」。两个字，不用猜。
 *
 * 【为什么保留 up/down 这两个内部值】
 *   后端 `handle_feedback` 只认 `up/down/note`，统计口径（满意率）也建在上面。
 *   改内部值要动服务端 + `_feedback_stats` + `badcase_to_eval` ——
 *   **那是契约层，收益为零**。改的只是**显示文字**。 */
function fbNode(m, prior) {
  // ⚠️ `prior` 是**上一轮的反馈状态**（`m.fb`，会随 `saveSessions()` 进 localStorage）。
  //    传它进来是为了区分两种情况：
  //      · 首次展示这条回答 → 收起状态，问一次「弄明白了吗」
  //      · 重画历史会话（`paintChat` 重建 DOM）→ 直接进结果态，**不再问一遍**
  //    ⇒ 两个状态位别混：
  //      `saved`  = 这题**以前**投过票（来自 prior）⇒ 重画时直接进结果态
  //      `painted`= **这一次会话里**用户自己按过 ⇒ 按完立刻变结果态
  //    为什么不能只用 `saved`：新投的一票要**等网络回来**才算数，
  //      而用户按下去就该立刻看到反馈（否则他会以为没点上、再按一次）。
  const st = prior || { rating: "", reason: "", comment: "", sent: false, err: "" };
  m.fb = st;
  const wrap = el("div", "fb");
  const saved = !!st.rating;
  let painted = false;
  const settled = () => saved || painted;

  const bar = el("div", "fb-bar");
  // ⚠️ 不写"这个回答有用吗"（owner 明确删过）——
  //   也不加 `fb-hint` 类名（守卫 `test_loud_hint_text_is_gone` 钉着这两个）。
  const qlab = el("span", "fb-q");
  qlab.textContent = "这个回答让你弄明白了吗？";
  const up = el("button", "fb-t"), down = el("button", "fb-t");
  up.type = "button"; down.type = "button";
  up.textContent = "懂了";
  down.textContent = "还是没明白";
  bar.appendChild(qlab); bar.appendChild(up); bar.appendChild(down);
  wrap.appendChild(bar);

  const body = el("div", "fb-body");
  wrap.appendChild(body);

  const base = () => {
    const p = m.payload || {};
    return {
      q: m.q || "", answer: m.content || "",
      mode: p.mode, provider: p.provider, best_cos: p.best_cos,
      assistant: p.assistant || AS.assistantId,
      citations: (p.citations || []).map(c => c.id),
    };
  };
  const done = (text) => {
    body.innerHTML = "";
    const d = el("div", "fb-done"); d.textContent = text; body.appendChild(d);
  };

  const paint = () => {
    up.classList.toggle("on", st.rating === "up");
    down.classList.toggle("on", st.rating === "down");
    // 交互过之后就钉住可见 —— 否则鼠标一移开，下面那张"选原因"的卡片会凭空消失。
    // ⚠️ `.acted` 这个类名和这条规矩**必须留着**（守卫 test_revealed_on_hover_not_always_on 钉着）。
    wrap.classList.toggle("acted", settled());
    // ⚠️ 投过票之后就把那一行问题收掉：已经答过的问题不必再问一遍。
    //    这同时是"不烦人"的另一半 —— 只有**还没投票的**回答下面才有那一行。
    bar.style.display = settled() ? "none" : "";
    body.innerHTML = "";
    if (!settled()) return;
    if (st.rating === "up") {
      return done("记下了 —— 这条算「弄明白了」。");
    }
    if (st.sent) {
      return done("记下了 —— 这条会进 badcase 清单，用来定位该改哪里。");
    }
    if (st.rating !== "down") return;

    const head = el("div", "fb-head");
    head.textContent = "哪里没明白？（选一个，能帮我判断该改哪一层）";
    body.appendChild(head);

    const opts = el("div", "fb-opts");
    FB.reasons.forEach(r => {
      const b = el("button", "fb-opt" + (st.reason === r.id ? " on" : ""));
      b.type = "button"; b.textContent = r.label;
      b.addEventListener("click", () => { st.reason = r.id; saveSessions(); paint(); });
      opts.appendChild(b);
    });
    body.appendChild(opts);

    const ta = el("textarea", "fb-ta");
    ta.placeholder = "补充说明（选填）：你期望的答案是什么？或当时场上到底是什么情况";
    ta.value = st.comment || "";
    ta.addEventListener("input", () => { st.comment = ta.value; });
    body.appendChild(ta);

    const row = el("div", "fb-row");
    const sub = el("button", "fb-sub"); sub.type = "button";
    sub.textContent = "提交";
    sub.disabled = !st.reason;
    sub.addEventListener("click", send);
    const note = el("span", "fb-note");
    // ⚠️ 这句话现在是**真的**了：2026-10-15 之前 feedback 的文本字段
    //    只做 `_clip` 截断、**不脱敏**，而界面上就写着"不含身份信息"。
    //    已修：`handle_feedback` 现在复用 `querylog._scrub`
    //    （手机号 / 邮箱 / 微信号都会被换成标记）。说到就要做到。
    note.textContent = "只记录问题与回答本身，不含身份信息";
    row.appendChild(sub); row.appendChild(note);
    body.appendChild(row);
    if (st.err) { const e = el("div", "fb-err"); e.textContent = st.err; body.appendChild(e); }
  };

  async function send() {
    if (!st.reason) return;
    const sub = wrap.querySelector(".fb-sub");
    if (sub) { sub.disabled = true; sub.textContent = "提交中…"; }
    try {
      await post("/api/feedback", Object.assign(base(), {
        rating: "down", reason: st.reason, comment: st.comment,
      }));
      st.sent = true; st.err = "";
      saveSessions(); paint();
    } catch (e) {
      st.err = "提交失败：" + ((e && e.message) || "未知错误");
      paint();
    }
  }

  up.addEventListener("click", () => {
    // ⚠️ 顺序要紧：**先改状态并立刻重画**（用户马上看到反馈），再发请求。
    st.rating = "up"; st.reason = ""; st.comment = ""; st.err = "";
    painted = true;
    saveSessions(); paint();
    // ⚠️ 而且这里**先画后发**：网络慢/失败都不能让按钮看起来没反应。
    //    `.catch(() => {})` 是有意的 —— 表扬不重试，别拿它烦用户。
    post("/api/feedback", Object.assign(base(), { rating: "up" })).catch(() => {});
  });
  down.addEventListener("click", () => {
    st.rating = "down"; st.err = "";
    painted = true;
    saveSessions(); paint();
  });

  paint();
  return wrap;
}

/* 默认预设不显示名字（见 botNode 里的注释）。 */
function astName() {
  const a = ast();
  return (a.id && a.id !== "standard") ? (a.name || "") : "";
}

/* 「展开细节」—— 直答之下的一行小字。
   设计约定（三条，都别破）：
   1. **常驻可见**，不做"hover 才出现"。它是个入口 —— 藏起来就等于没这功能
      （本项目已经踩过：笔记功能早就实现了，但入口太深 = 等于没有）。
   2. **不放进 .tools** —— 那个容器是 opacity:0 的，鼠标不动就看不见。
   3. 就地展开，**不跳右栏**：用户此刻的注意力在这条回答上，视线不用挪。
      展开内容里的条款号仍然可点，点了才跳右栏看原文（右栏的职责是"条款原文"，
      这里放的是"解释"，两者不混）。
   折叠是纯前端显隐：内容早就在 DOM 里，点开是瞬时的，不会再发一次请求。 */
function moreNode(detail) {
  const wrap = el("div", "more");
  const btn = el("button", "more-btn");
  const dbox = el("div", "more-body");
  dbox.innerHTML = md(detail);
  const paint = (on) => {
    btn.innerHTML = `<span class="arw">▸</span>${on ? "收起" : "展开细节"}`;
    btn.classList.toggle("on", on);
    dbox.classList.toggle("on", on);
  };
  paint(false);
  btn.addEventListener("click", () => paint(!dbox.classList.contains("on")));
  wrap.appendChild(btn);
  wrap.appendChild(dbox);
  return wrap;
}

/* 术语白话解释块（2026-10-10 新增）。
   **默认展开**，与「展开细节」那套折叠机制相反 —— 那是模型写的解释，
   这块是官方名词定义，是给读不懂原文的新手兜底的，藏着就等于没做。
   ⚠️ 必须标注来源：它是 `data/definitions.jsonl` 里的官方名词定义，
   不是模型的话。不标来源，用户会以为这也是 AI 生成的。 */
function glossaryNode(g) {
  if (!g) return null;
  const box = el("div", "gloss");
  box.innerHTML = `<div class="gloss-h">📖 上面这几个词的意思 · 官方名词定义</div>`
    + `<div class="md">${md(g)}</div>`;
  return box;
}

function botNode(m) {
  // ⚠️ `gated` = "这条不是规则答案"（把状态点变灰，`.msg.bot.gated .who i`）。
  //    三种 mode 都是这个意思，**必须一起认**：
  //      · `not_found`     —— 规则里没找到
  //      · `out_of_scope`  —— 闸门拒答（2026-10-15 补：原来只有 not_found 会变灰，
  //                            导致拒答那条顶着紫色的"有依据"点在装规则答案）
  //      · `beyond_rules`  —— 跟飞盘有关但答不了（本轮新增）
  //    ⇒ 新增任何一种"我没有依据"的 mode，都要回来这里补上。
  const GATED_MODES = ["not_found", "out_of_scope", "beyond_rules"];
  const md_ = (m.payload && m.payload.mode) || "";
  const box = el("div", "msg bot" + (GATED_MODES.indexOf(md_) >= 0 ? " gated" : ""));
  const who = el("div", "who");
  // ⚠️ 默认预设（标准模式）**不挂模式名**：每条回答都顶一个"标准模式"是纯噪音，
  //    那不是用户在读的东西。只有切到「争议复盘」这类非默认预设时才显示 ——
  //    那时候它才是信息（"我是按复盘的口径答的"）。
  const an = astName();
  who.innerHTML = an ? `<i></i>${esc(an)}` : "<i></i>";
  box.appendChild(who);
  // 回答分两段：「直答」默认显示；`<!--more-->` 之后的细节默认折叠。
  const seg = String(m.content ?? "").split(MORE_RE);
  const detail = seg.slice(1).join("\n").trim();
  const body = el("div", "md"); body.innerHTML = md(seg[0]); box.appendChild(body);
  // 术语白话解释紧跟正文（**在折叠的 more 之前**）：它服务于"读懂上面这句"，
  // 藏到"展开细节"后面等于没有。详见 glossaryNode 注释。
  const gl = glossaryNode(m.payload && m.payload.glossary); if (gl) box.appendChild(gl);
  if (detail) box.appendChild(moreNode(detail));
  const p = m.payload;
  // ⚠️ 日常对话（mode=chat）下面**什么都不挂**。
  //    它没有依据可看、没有检索可查、也没有必要问"这个回答有用吗" ——
  //    在「你好」下面挂一排「日常对话 · 检索 0 ms · 记进笔记 · 复制回答 · 这个回答有用吗」，
  //    正是 owner 说的"给用户添乱"（他原话：「我问你一句话，你回一句就行了」）。
  //    ⇒ 一句寒暄就该只是一句寒暄。
  if (p && p.mode !== "chat") {
    const row = citesRow(p.citations); if (row) box.appendChild(row);
    const meta = el("div", "meta");
    const label = MODE_LABEL[p.mode] || p.mode || "";
    const bits = [`<span class="badge ${p.mode === "llm" ? "" : "gray"}"><i></i>${esc(label)}</span>`];
    // ⚠️ 模型名（deepseek-chat 之类）是**开发者信息**，跟着「显示细节」开关走；
    //    关掉之后普通用户看到的就只是一句"依据规则原文"。
    if (p.provider) bits.push(`<span class="tech">${esc(p.provider)}</span>`);
    // 下面这些是"给较真的人看"的打分细节，class="tech" 让它能被外观开关整体关掉。
    // 用 class 而不是删掉：删了懂行的人就没处核对，开关关了还能开回来。
    if (typeof p.elapsed_ms === "number") bits.push(`<span class="tech">检索 ${p.elapsed_ms} ms</span>`);
    if (typeof p.best_cos === "number") bits.push(`<span class="tech">最高相关度 ${p.best_cos.toFixed(3)}</span>`);
    meta.innerHTML = bits.join("") + `<span class="sp"></span>`;
    // 「记进笔记」常驻在 meta 行右侧（不是 .tools 那个 hover 才出现的容器）——
    // 记下来以后回看是目的本身，藏起来就等于没这功能。
    const nb = el("button", "nbtn"); nb.textContent = "记进笔记";
    nb.addEventListener("click", () => captureNote(m));
    meta.appendChild(nb);
    const tools = el("div", "tools");
    const cp = el("button"); cp.textContent = "复制回答";
    cp.addEventListener("click", () => {
      navigator.clipboard.writeText(m.content).then(
        () => { cp.textContent = "已复制"; setTimeout(() => { cp.textContent = "复制回答"; }, 1400); },
        () => { cp.textContent = "复制失败"; setTimeout(() => { cp.textContent = "复制回答"; }, 1400); });
    });
    tools.appendChild(cp); meta.appendChild(tools); box.appendChild(meta);
    // ⚠️ 把**上一轮的反馈状态**还回去（`m.fb`，来自 localStorage）。
    //    为什么要传：`paintChat()` 重画历史会话时会重建 DOM，
    //    如果这里不把旧状态交回去，用户翻回昨天的对话会看到
    //    「这个回答让你弄明白了吗？」又冒出来 —— 而他明明已经点过了。
    //    ⇒ fbNode 靠这个判断"是首次展示还是重新展示"。
    box.appendChild(fbNode(m, m.fb || null));
    // 人工核定标记：让人一眼看出这条答案不是当场生成的，而是人写死的 ——
    // 这直接决定了对它的信任程度。⚠️ 不用「Owner」这种内部称呼。
    if (p.mode === "curated" && p.curated) {
      const c = el("div", "cur-note");
      c.innerHTML = `<span class="tag t-own">${esc(p.curated.tag || "人工核定")}</span>
        <span class="dim">原问「${esc(p.curated.question || m.q || "")}」·
        ${esc(p.curated.by || "维护者")} 核定${typeof p.curated.match === "number" ? ` <span class="tech">· 匹配度 ${p.curated.match.toFixed(3)}</span>` : ""}</span>`;
      box.appendChild(c);
    }
    // 联网兜底结果：**独立区域**渲染，带免责标注。
    // 刻意不放进 citesRow —— 引用区代表"规则原文"，网络内容不配进那里。
    if (p.web && (p.web.snippets || []).length) {
      box.appendChild(webNode(p.web));
    }
    // 拒答 / 低置信时给 Owner 一个"我来裁定"的入口。
    // 这是本系统的核心闭环：答不出不是终点，是把问题交出去。
    if (p.mode === "not_found" || p.mode === "curated") {
      box.appendChild(ownerAskNode(m, p));
    }
    if (p.rewritten && p.rewritten.trim() && p.rewritten.trim() !== (m.q || "").trim()) {
      const rw = el("div", "rw tech");
      rw.innerHTML = `我理解成：<b>${esc(p.rewritten)}</b>`; box.appendChild(rw);
    }
  }
  return box;
}

/* 联网兜底区：顶部强制显示免责声明 + 官方链接。
   视觉上刻意做成"另起一块、带警示色"，和规则引用区明确区分。 */
function webNode(web) {
  const box = el("div", "web-box");
  const head = el("div", "web-h");
  // 2026-10-24：只允许 https 外链。今天的 url 是服务端常量，但一旦它变成
  // 动态值，`javascript:` / `data:` 就会成为可点击的执行面。
  const safeUrl = /^https:\/\//i.test(String(web.url || "")) ? String(web.url) : "";
  head.innerHTML = `<span class="web-warn">⚠️</span>
    <b>来自网络 · 非规则原文</b>
    ${safeUrl ? `<a class="web-link" href="${esc(safeUrl)}" target="_blank" rel="noopener">${esc(web.source)}</a>` : `<span>${esc(web.source)}</span>`}`;
  box.appendChild(head);
  const list = el("div", "web-list");
  (web.snippets || []).forEach(s => {
    const p = el("div", "web-snip");
    p.textContent = s;
    list.appendChild(p);
  });
  box.appendChild(list);
  const tip = el("div", "web-tip");
  tip.textContent = web.disclaimer || "";
  box.appendChild(tip);
  return box;
}

/* 「这条我不确定」——把问题一键送进疑问库表单。
   点了直接跳到疑问库并把原话预填，省去再打一遍。 */
function ownerAskNode(m, p) {
  const row = el("div", "own-ask");
  const t = el("span", "dim");
  t.textContent = p.mode === "not_found" ? "这条你也不知道？" : "这条裁定得对吗？";
  const b = el("button", "btn");
  b.textContent = p.mode === "not_found" ? "人工裁定 →" : "修正这条裁定 →";
  b.addEventListener("click", () => {
    CUR.prefill = m.q || "";
    go("settings/curated");
  });
  row.appendChild(t); row.appendChild(b);
  return row;
}
function paintChat() {
  if (!$("inner")) return;
  paintHist();
  const inner = $("inner");
  disposeHero();
  inner.innerHTML = "";
  if (CH.storageBroken) {
    const warn = el("div", "nt-empty");
    warn.textContent = "历史记录的结构无法识别。为免覆盖原件，已停止写入。请先导出原始数据保存。";
    inner.appendChild(warn);
    const dump = el("button", "nt-add");
    dump.textContent = "导出损坏的历史原始数据";
    dump.addEventListener("click", () => downloadText(CH.recovery, "frisbee-history-recovery.txt"));
    inner.appendChild(dump);
  }
  const s = curS();
  const empty = !s || !s.msgs.length;
  // empty 类做三件事：藏右栏、把输入框顶到中线、上方的流不留高度
  const chat = $("pbody").querySelector(".chat");
  if (chat) chat.classList.toggle("empty", empty);
  if (empty) {
    const hero = heroNode();
    inner.appendChild(hero);
    // 动画绑定放在 append 之后：getTotalLength 需要 path 已在文档里，
    // 清理函数挂在模块级，换页/新对话时由 disposeHero() 统一拆掉。
    heroFlightCleanup = bindHeroFlight(hero);
  }
  else {
    s.msgs.forEach(m => {
      if (m.role === "user") {
        const box = el("div", "msg user"); const b = el("div", "bub"); b.textContent = m.content;
        box.appendChild(b); inner.appendChild(box);
      } else inner.appendChild(botNode(m));
    });
    scrollDown(true);
    bindCites(inner);
  }
  paintRail();
  syncRail();
}
function bindCites(root) {
  root.querySelectorAll(".cite").forEach(n => n.addEventListener("click", () => selectCite(n.dataset.id)));
}
function scrollDown(force) {
  const st = $("stream"); if (!st) return;
  if (force || CH.stick) st.scrollTop = st.scrollHeight;
}

/* 右栏两个视图共用一个挂载点：先清空，再按 NT.view 决定画哪个。 */
function paintRail() {
  const body = $("railBody"); if (!body) return;
  const on = (id, v) => { const n = $(id); if (n) n.classList.toggle("on", NT.view === v); };
  on("tabSrc", "src"); on("tabNote", "note");
  body.innerHTML = "";
  if (NT.view === "note") paintNotes(body); else paintSources(body);
}

/* 视图一：这次回答引用了哪些条款原文 —— 外加「你点开的那一条」 */
function lastAssistantMsg() {
  const s = curS();
  return s ? [...s.msgs].reverse().find(m => m.role === "assistant") : null;
}

/* 单张条款卡。`fallback=true` ⇒ 它**不在本次 citations 里**，是用户点了
   条款号之后按条号直接取来的原文（见 ensureCite），必须标明，不能冒充"命中"。 */
function citeCard(c, fallback) {
  const card = el("div", "rcard" + (c.id === CH.active ? " on" : ""));
  card.dataset.cid = c.id;
  // 附录不是正文条款：卡片标题写「附录 A1 场地」，不写「第 A1 条」。
  const head = c.type === "definition" ? `术语「${esc(c.term || "")}」`
    : c.type === "preface" ? "前言"
    : c.type === "appendix" ? `附录 · ${esc(c.label || c.id)}`
    : esc(c.id);
  const cos = (typeof c.cos === "number") ? c.cos : null;
  const srcLine = fallback
    ? `${esc(c.source || "")} · 这条不在本次检索里，按条款号直接取原文`
    : esc(c.source || "");
  card.innerHTML = `<div class="rh"><b>${head}</b><em>${esc(c.chapter_title || "")}</em>
      ${cos !== null ? `<span class="sc tech">${cos.toFixed(3)}</span>` : ""}</div>
    <div class="rt">${esc(c.text || "")}</div>`
    + (c.parent_text ? `<div class="par">上级条款 ${esc(c.parent_id || "")}：${esc(c.parent_text)}</div>` : "")
    // 附录注解：正文把答案外包给图/附录时（2.1「尺寸见图 1」），数字在注解里。
    // 必须显示，否则用户点开 2.1 只看到"见图 1"，会以为答案的数字是编的。
    + ((c.notes || []).map(n => `<div class="fig"><b>${esc(n.label || "附录注解")}</b>${esc(n.text || "")}</div>`).join(""))
    + (cos !== null ? `<div class="meter tech"><span style="width:${Math.max(2, Math.min(100, cos * 100)).toFixed(1)}%"></span></div>` : "")
    + `<div class="src">${srcLine}</div>`;
  card.addEventListener("click", () => selectCite(c.id, true));
  return card;
}

/* 右栏兜底：用户点了**没被召回**的条款号时，按条号取原文塞进右栏。
 *
 * ⚠️ 为什么必须有这一步（2026-10-17 线上真事，owner 转来的用户反馈）：
 *    答案是模型写的，它可以在正文里引一条**检索没召回**的条款
 *    （实测：问「我们在区域防守，我是读秒的防盘人」，答案引 **17.4.1**，
 *      而这次 13 条 `citations` 里没有 17.4.1）。右栏原来只画 citations
 *    ⇒ 用户点 17.4.1 打开的是"一堆别的条款"，看起来和"右栏空了"一样。
 * ⚠️ 取的是**服务端语料原文**（`/api/rule`），不是模型生成 ⇒ 零编造风险；
 *    而且**绝不写进 citations** —— 那条的语义是"这次检索召回了什么"。
 */
async function ensureCite(cid) {
  const last = lastAssistantMsg();
  const p = last && last.payload;
  if (!p || !cid) return;
  if ((p.citations || []).some(c => c && c.id === cid)) return;
  p.__fb = p.__fb || {};
  if (Object.prototype.hasOwnProperty.call(p.__fb, cid)) return;   // 取过了（含取不到）
  p.__fb[cid] = null;                                             // 占位，防重复请求
  try {
    const r = await fetch(`/api/rule?id=${encodeURIComponent(cid)}`);
    if (!r.ok) { delete p.__fb[cid]; return; }
    const j = await r.json();
    if (j && j.item) { p.__fb[cid] = j.item; paintRail(); }
    else delete p.__fb[cid];
  } catch (e) {
    delete p.__fb[cid];        // 拿不到就别留坑：用户再点一次还能重试
  }
}

function paintSources(body) {
  const last = lastAssistantMsg();
  const p = last && last.payload;
  const cites = (p && p.citations) || [];
  // 兜底卡单独一组，**不冒充命中**
  const fb = Object.values((p && p.__fb) || {}).filter(c => c && c.id);
  if ($("railCnt")) $("railCnt").textContent = String(cites.length);
  if (!cites.length && !fb.length) {
    // 无引用时**不解释自己**。早先这里渲染一整段"这里会显示…"的说明书，
    // 实拍下来比左边真正有用的内容还抢眼 —— 没有内容时就该是空的。
    body.innerHTML = `<div class="rail-empty">这次回答没有引用条款原文。
      ${last ? "它可能是直接说了规则里没写，或者走了网络兜底。" : "问一句试试。"}</div>`;
    return;
  }
  if (fb.length) {
    const tf = el("div", "rsec"); tf.textContent = "你点开的条款"; body.appendChild(tf);
    fb.forEach(c => body.appendChild(citeCard(c, true)));
  }
  if (cites.length) {
    const t = el("div", "rsec"); t.textContent = `本次翻出 ${cites.length} 条`; body.appendChild(t);
    cites.forEach(c => body.appendChild(citeCard(c, false)));
  }
  if (p) {
    // 检索详情是纯开发者视角（召回路数 / 类目 / Token），默认折叠，且可被外观开关整体关掉。
    // 2026-10-24：这些字段来自持久化 payload（localStorage 可被同源脚本/旧数据污染），
    // 原来直接拼进 innerHTML —— 不是当前公开可注入的路径，但属于同一类 sink。
    // 数值字段统一 Number + 边界，usage 只取有限数。
    const dbg = el("details", "dbg tech");
    const routes = Array.isArray(p.routes) ? p.routes : [];
    const num = (v, d = 0) => Number.isFinite(Number(v)) ? Number(v) : d;
    const pathsN = Math.max(0, Math.min(10, Math.trunc(num(p.paths, 1))));
    const forcedN = Math.max(0, Math.min(999, Math.trunc(num(p.forced_count, 0))));
    const elapsedN = Math.max(0, Math.min(600000, Math.trunc(num(p.elapsed_ms, 0))));
    const usage = (p.usage && typeof p.usage === "object") ? p.usage : null;
    dbg.innerHTML = `<summary><span class="arw">▶</span>检索详情</summary>
      <div class="kv2">
        <div><span>助手</span><span>${esc(ast().name)}</span></div>
        <div><span>检索路径</span><span>${pathsN} 路召回（原问题 + 改写）</span></div>
        <div><span>命中类目</span><span>${routes.length} 个</span></div>
        <div><span>类目注入条款</span><span>${forcedN} 条</span></div>
        <div><span>最高相关度</span><span>${typeof p.best_cos === "number" ? p.best_cos.toFixed(4) : "—"}</span></div>
        <div><span>检索耗时</span><span>${elapsedN} ms</span></div>
        ${usage ? `<div><span>Token</span><span>${Math.max(0, Math.trunc(num(usage.prompt, 0)))} + ${Math.max(0, Math.trunc(num(usage.completion, 0)))}</span></div>` : ""}
      </div>`;
    if (routes.length) {
      const tags = el("div", "tags");
      routes.forEach(r => { const g = el("span", "tag"); g.textContent = r; tags.appendChild(g); });
      dbg.appendChild(tags);
    }
    body.appendChild(dbg);
  }
}
function selectCite(cid, fromRail) {
  CH.active = (CH.active === cid && fromRail) ? null : cid;
  // 点条款号的意思是"我要看这条原文" ⇒ 强制切回引用视图并展开右栏，
  // 不然用户在笔记视图里点条款号会什么都没发生。
  NT.view = "src";
  if (CH.active) NT.open = true;
  paintChat();
  expandCite(cid);
}
/* 展开并滚到某张卡。卡不在右栏里 ⇒ 先去按条号取原文，拿到后再展开一次。
   ⚠️ 这个兜底是 2026-10-17 加的：模型引的条款不一定在 citations 里，
      没有它，用户点条款号会"打开右栏但看不到那一条"。 */
function expandCite(cid) {
  if (!CH.active) return;
  const body = $("railBody"); if (!body) return;
  const card = body.querySelector(`.rcard[data-cid="${CSS.escape(cid)}"]`);
  if (card) { card.classList.add("open"); card.scrollIntoView({ behavior: "smooth", block: "center" }); return; }
  ensureCite(cid).then(() => {
    const b2 = $("railBody"); if (!b2) return;
    const c2 = b2.querySelector(`.rcard[data-cid="${CSS.escape(cid)}"]`);
    if (c2) { c2.classList.add("open"); c2.scrollIntoView({ behavior: "smooth", block: "center" }); }
  });
}
function closeRail() { NT.open = false; paintRail(); syncRail(); }
function closeNav() { const n = $("nav"); if (n) n.classList.remove("open"); syncRail(); }

function grow(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 180) + "px"; }

async function ask(question) {
  const q = (question || "").trim();
  if (!q || CH.busy) return;
  const s = curS() || (CH.sessions.unshift(newSession()), CH.sessions[0]);
  if (!CH.current) CH.current = s.id;
  setTitle(s, q);
  s.msgs.push({ role: "user", content: q });
  paintHist();
  // 一旦开问就不是空态了：撤掉 empty，右栏要出来、输入框要落回底部。
  // ⚠️ ask() 是直接往 inner 追加消息、不经过 paintChat() 的，
  // 所以这里必须自己撤 —— 漏了这一步，右栏会一直藏着，答完也不显示引用。
  const chatBox = $("pbody").querySelector(".chat");
  if (chatBox) chatBox.classList.remove("empty");
  syncRail();

  CH.busy = true; CH.active = null;
  const sendBtn = $("send");
  if (sendBtn) { sendBtn.textContent = "停止"; sendBtn.classList.add("stop"); }

  const inner = $("inner");
  const fresh = s.msgs.length === 1;
  // ⚠️ 记住发起时会话是"哪一条"。等待答案的 2~30 秒里用户完全可以点开
  // 另一条历史对话，而 paintChat() 只做 inner.innerHTML=""（**不重建 #inner 节点**）
  // ⇒ 光靠 `inner` 这个引用会把答案追加到**当前正看着的那条会话**末尾，
  // 而消息体其实写进了发起时那条 s。屏幕与数据不一致，且没有任何提示。
  // 判定用会话身份而不是引用：引用会跟着 DOM 走，身份不会。
  const askedSid = s.id;
  if (inner) {
    if (fresh) { disposeHero(); inner.innerHTML = ""; }
    const ubox = el("div", "msg user"); const bub = el("div", "bub"); bub.textContent = q;
    ubox.appendChild(bub); inner.appendChild(ubox);
    const thinking = el("div", "msg bot");
    thinking.innerHTML = `<div class="who"><i></i>${esc(ast().name)}</div>
      <div class="loading">正在理解你的问题<span class="bar"></span></div>`;
    inner.appendChild(thinking);
    scrollDown(true);
  }
  const ta = $("q"); if (ta) { ta.value = ""; grow(ta); }

  CH.ctrl = new AbortController();
  let payload = null, answerText = "", failed = "";

  // ⚠️ 流式回调可能在**用户已经切到别的会话之后**才触发。
  //    用对象包一层（而不是裸 bool）是因为回调里要读**当时**的值，
  //    闭包捕获裸 bool 会拿到创建时的快照 —— 那会把答案渲染到不相干的会话里。
  const stillHereRef = { value: true };

  // ⚠️⚠️ **硬超时**（2026-10-13 加，止血用）。
  //   起因：owner 报「一直卡在『正在理解你的问题』」。实测复现到
  //   **服务端 200、答案也生成了，浏览器 `fetch` 就是不 resolve**，
  //   而根因**始终没定位**（详见 tools/cdp_trace.py 的复盘：
  //   我从外面猜 DOM 猜了四次全错）。
  //   ⇒ 既然根因未知，就**不能允许无限等待** ——
  //     90 秒还不回来就放弃并告诉用户，绝不让界面永远转圈。
  //     「永远转圈」是本项目最坏的一种失败：比拒答坏得多
  //     （拒答至少告诉了他一个结果）。
  //
  // ⚠️ 用 `AbortController` + `setTimeout`，不用 `Promise.race` ——
  //   race 只是让 UI 不等了，**底层请求还挂着**（连接、服务端线程都占着）；
  //   abort 是真的取消。
  // ⚠️ 超时时长取 90 秒：规则主路实测 3~6 秒，深度检索 + 慢模型也给不出 90 秒。
  //   太短会误杀正常的慢回答，太长就等于没有。
  const HARD_TIMEOUT_MS = 90_000;
  let timedOut = false;
  const watchdog = setTimeout(() => {
    timedOut = true;
    try { CH.ctrl && CH.ctrl.abort(); } catch (e) { /* 忽略 */ }
  }, HARD_TIMEOUT_MS);

  try {
    // ⚡ 走**流式**接口（2026-10-13 上）：首字 ~2s 就开始上屏，
    //   而旧的非流式要等整段写完（实测 3.4s）才一次性出现。
    payload = await askStream({
      q, assistant: AS.assistantId,
      history: s.msgs.slice(0, -1).slice(-8).map(m => ({ role: m.role, content: m.content })),
    }, CH.ctrl.signal, (sofar) => {
      // 每收到一段就更新气泡 —— 用户看着字往外冒，而不是盯着空白。
      answerText = sofar;
      if (inner && stillHereRef.value) renderStreaming(inner, sofar);
    });
    answerText = payload.answer || answerText || "";
  } catch (err) {
    if (timedOut) failed = "回答超时了（等了 90 秒）。可能是服务忙，点「发送」重试一次。";
    else if (err && err.name === "AbortError") failed = "已停止";
    else if (err && err.message === "Failed to fetch") failed = "网络或服务异常，请稍后再试";
    else failed = (err && err.message) || "服务返回错误";
  } finally {
    clearTimeout(watchdog);
  }

  CH.busy = false; CH.ctrl = null;
  const sb = $("send"); if (sb) { sb.textContent = "发送"; sb.classList.remove("stop"); }

  // ⚠️⚠️⚠️ 2026-10-15 重写这个判断 —— 原来的写法有**用户能看见的后果**。
  //
  // 【原来】`const stillHere = (CH.current || {}).id === askedSid;`
  //   实测（真实浏览器 + CDP 打点）拿到这一串：
  //       "[ask] C 到 stillHere 前 CH.current=\"mv9k0gvivcq3u\" 类型=string"
  //       "[ask] stillHere诊断 curId=undefined ... eq=false"   ← **下一行就读不到了**
  //   两行之间没有任何赋值 ⇒ **有异步代码在这 2~3 秒里改掉了 `CH.current`**
  //   （`CH` 是全局可变状态，而 `ask()` 跨了两个 `await`）。
  //   ⇒ `stillHere` 变 false ⇒ 消息**存进了数据却没渲染**：
  //       · `s.msgs` 里有完整答案（刷新页面就能看到）
  //       · 但 DOM 永远停在流式那个**原始 markdown 气泡**上
  //         （`<!--more-->` 标记、`**` 星号、`- ` 项目符号全都原样显示）
  //       · 发送按钮还停在「停止」态
  //     ⇒ 用户看到的是"卡住了"，**而数据其实是好的** —— 最难查的一类。
  //
  // 【现在】不用可变全局，改用**捕获下来的会话对象身份**。
  //   `askedSession` 是 `ask()` 一开始就抓住的那个对象，别人改 `CH.current`
  //   影响不到它；`in` 列表检查用来确认它没被删。
  //   ⚠️ 仍然保留"节点还在文档里"的检查 —— 用户离开对话页时不该往
  //      已脱离文档的节点上追加。
  const askedSession = s;
  const sessionStillListed = Array.isArray(CH.sessions)
    && CH.sessions.indexOf(askedSession) >= 0;
  const innerUsable = !!inner && (inner.isConnected === undefined
                                  ? document.contains(inner) : inner.isConnected);
  const stillHere = sessionStillListed && innerUsable;
  if (!stillHere) {
    // 只记录，不改行为 —— 留给"下次再出这类问题"当证据。
    console.log("[ask] 不渲染最终气泡：listed=" + sessionStillListed
                + " innerUsable=" + innerUsable
                + " sessions=" + (CH.sessions || []).length);
  }
  stillHereRef.value = !!stillHere;      // ⚠️ 流式回调要用它，见上面的说明
  // ⚠️⚠️⚠️ 2026-10-15 修了一个**用户能看见的线上 bug**：
  //    这里原来只找 `.loading`，但 `renderStreaming()` **已经把 class
  //    从 `.loading` 改成了 `.streaming`**（见 L563-565）。
  //    ⇒ `querySelector(".loading")` 返回 **null** ⇒ 那个临时气泡**永远不会被删掉**。
  //    ⇒ 结果：用户看到**两个气泡叠着** ——
  //        第一个是流式阶段的**原始 markdown 纯文本**（`<!--more-->` 标记、
  //        `**加粗**` 星号、`- ` 项目符号全都原样显示），
  //        第二个才是渲染好的正式气泡。
  //    ⇒ 我是**给答案截图时**才发现的 —— 之前所有"看截图"的检查都没注意到，
  //       因为以前只截空状态、没截过"有答案"的画面。
  //
  //    ⚠️ 这已经是本项目**第三次**栽在同一个模式上：
  //       "选择器只认旧 class 名，而 class 中途被改过"
  //       （前两次：`renderStreaming` 只找 `.loading` 导致只显示第一个字；
  //         `_ask_stream` 那里也是同类）。**改 class 名的地方必须回头搜一遍谁在读它。**
  const th = stillHere && inner
    ? inner.querySelector(".loading, .streaming") : null;
  if (th) {
    // 只删这一条临时气泡所在的 .msg，别误伤别的
    const box = th.closest(".msg");
    if (box) box.remove();
  }

  // ⚠️⚠️ 流式把正文**渲染进了那个 loading 气泡本身**（见 renderStreaming）。
  //    ⇒ 上面 `th.closest(".msg").remove()` 会把已经显示出来的半截答案一起删掉。
  //    这是**刻意的**：紧接着下面 `botNode(...)` 会用最终 payload
  //    渲染出**完整、带引用芯片、带术语折叠**的正式气泡。
  //    代价是"半截答案"到"完整答案"之间有一次重绘 —— 肉眼几乎看不出，
  //    换来的是**只有一条渲染路径**（不会出现"流式版和最终版长得不一样"）。

  // ⚠️⚠️ **无条件记录一次现场**（2026-10-13 加，为了查那个"转圈不出答案"）
  //
  // 为什么必须**无条件**：兜底那段只在"`#inner` 里还有 .loading"时才上报，
  //   而实测遇到的**另一种形态**是 `#inner` 里**根本没有 loading 气泡**
  //   （`loadingInInner: 0`）却也没出答案 —— 兜底完全看不到这种。
  //   ⇒ 判据不能只覆盖"我以为的那种卡法"，要**每种情况都留证据**。
  //
  // ⚠️ 只在前端自己出错时上报：正常路径（post 返回 + 答案非空）**一次都不发**，
  //   免得把正常用户的流量变成日志。
  if (!answerText && !failed) {
    uiDiag({ kind: "empty-answer", stillHere: !!stillHere, hasTh: !!th,
             innerInDoc: !!(inner && (inner.isConnected !== undefined
                                      ? inner.isConnected : document.contains(inner))),
             innerKids: inner ? inner.children.length : -1,
             loadings: inner ? inner.querySelectorAll(".loading").length : -1,
             hasPayload: !!payload, ansLen: (answerText || "").length,
             msgs: (s.msgs || []).length });
  } else if (failed) {
    uiDiag({ kind: "ask-failed", err: String(failed).slice(0, 120),
             stillHere: !!stillHere });
  }

  // ⚠️⚠️ 2026-10-13：**兜底 + 取证**（owner 报「一直卡在正在理解你的问题」）
  //
  // 【现象】真浏览器实测：`/api/ask` 3.3~3.6 秒返回 200、`CH.busy` 已回 false，
  //   但 `#inner` 里**仍然留着转圈的 .loading**，答案没上屏，且**无 JS 异常**。
  //   `CH.busy=false`（上行）与撤 loading（上一行）**在同一段顺序代码里、中间没有 await**
  //   ⇒ 说明 `th` 是 null。
  //
  // 【已知】**不是** `stillHere` 为假：实测 `CH.current === curS().id`（同一个会话 id），
  //   会话从未切换过。**也**不是节点被重建：`paintChat()` 只做 `inner.innerHTML=""`，
  //   不换 `#inner` 节点本身。⇒ 到底为什么为 null，**我还没定位**。
  //
  // 【所以这一段做两件事，缺一不可】
  //   ① **兜底（止血）**：不管什么原因，只要 `#inner` 里还有 `.loading` 就清掉它。
  //      用户绝不该看到**永远转圈**——那是本项目最坏的一种失败
  //      （比拒答坏：拒答至少告诉了他一个结果）。
  //   ② **取证（查根因）**：把当时的现场写进 `payload.__ui`，随反馈/埋点回传。
  //      ⚠️ 不做这一步的话，我永远只能靠"从外面猜 DOM"，而那已经错过三次。
  //
  // ⚠️ 判据用"元素还在不在"（可观测），**不用**"它有没有被移除"（不可观测）——
  //    "消失"是负向事件，看不见；"存在"是正向事件，看得见。
  try {
    if (inner) {
      const strays = inner.querySelectorAll(".loading");
      if (strays.length) {
        const diag = {
          strays: strays.length,
          stillHere: !!stillHere,
          innerInDoc: !!(inner.isConnected !== undefined
                         ? inner.isConnected : document.contains(inner)),
          failed: failed || "",
          msgs: (s.msgs || []).length,
        };
        strays.forEach(n => { const m = n.closest(".msg"); if (m) m.remove(); });
        payload = Object.assign({}, payload, { __ui: diag });
        uiDiag(diag);
      }
    }
  } catch (e) { /* 兜底代码自己绝不能把主流程带崩 */ }

  if (failed) {
    if (stillHere && inner) {
      const e = el("div", "msg bot");
      e.innerHTML = `<div class="who"><i></i>${esc(ast().name)}</div><div class="err">${esc(failed)}</div>`;
      inner.appendChild(e);
    }
  } else {
    s.msgs.push({ role: "assistant", content: answerText, payload, q });
    if (stillHere && inner) {
      // ⚠️⚠️⚠️ 2026-10-15 修：**先删掉流式那个临时气泡**，再挂正式气泡。
      //
      // 【原来缺这一步,后果是用户能看见的】
      //   流式阶段 `renderStreaming()` 把正文**原地**写进了那个
      //   「正在理解你的问题」气泡（class 已从 `.loading` 变成 `.streaming`）。
      //   而这里**只做了 appendChild，从来没删它** ⇒ 结果页面上**两条一模一样的问题**：
      //     第一条 = 流式阶段的**原始 markdown 纯文本**
      //              （`<!--more-->` 标记、`**加粗**` 星号、`- ` 项目符号**全都原样显示**）
      //     第二条 = `botNode()` 渲染好的正式气泡
      //
      //   ⚠️ 上面 L4140 那段"删 loading 气泡"**并不是为这个场景写的** ——
      //      它只找 `.loading`，而 class 早就被改成 `.streaming` 了 ⇒ 返回 null。
      //      （我先把那里的选择器补成 `.loading, .streaming`，但真正缺的是**这一句**。）
      //
      //   【怎么发现的】给答案截图时看到正文里有 `<!--more-->` 字面量。
      //     之前所有检查都只截**空状态**，从没截过"有答案"的画面 ⇒ 一直没暴露。
      //     ⇒ 教训：**截图要覆盖每种界面状态**，不能只截最容易的那一种。
      const tmp = inner.querySelector(".streaming, .loading");
      if (tmp) { const dead = tmp.closest(".msg"); if (dead) dead.remove(); }
      const node = botNode(s.msgs[s.msgs.length - 1]);
      inner.appendChild(node); bindCites(node);
    }
  }
  saveSessions(); paintHist(); paintRail(); syncRail();
  if (stillHere) scrollDown(true);
}
function stopAsk() { if (CH.ctrl) CH.ctrl.abort(); }

/* ==========================================================================
   启动
   ========================================================================== */
async function loadCurated(force) {
  if (CUR.loaded && !force) return;
  try {
    const t = ownerToken();
    if (!t) { CUR.items = []; CUR.stats = null; CUR.loaded = false; return; }
    const d = await api("/api/curated", { headers: { "X-Owner-Token": t } });
    CUR.items = (d && d.items) || [];
    CUR.stats = (d && d.stats) || null;
    CUR.loaded = true;
  } catch (e) { CUR.items = []; CUR.stats = null; }
}

/* 疑问库里"试问一次"：走真实问答链路，把结果弹出来。
   目的是让 Owner 亲眼看到裁定后的实际回答长什么样，
   而不是相信"应该会命中"。 */
async function askAndToast(q) {
  try {
    // ⚠️ 必须用 post 而不是 api —— 后端 do_POST 才认 /api/ask，do_GET 没有这条路由。
    //   原写法 `api("/api/ask", { q })` 把 { q } 当成 fetch 的 init 对象传了进去：
    //   method 缺省是 GET、q 被丢弃 ⇒ 落到静态资源分支 ⇒ 404「文件不存在」。
    //   表现是「试问一次」100% 失败，且错误文案让人以为服务坏了。
    const r = await post("/api/ask", { q });
    const head = MODE_LABEL[r.mode] || r.mode;
    toast(`${head}：${(r.answer || "").slice(0, 120)}${(r.answer || "").length > 120 ? "…" : ""}`);
  } catch (e) { toast("提问失败：" + e.message); }
}

async function loadFeedback() {
  try {
    // recent（其他用户的 q/comment）只回给持令牌的 Owner；普通用户只要聚合统计。
    const t = ownerToken();
    const r = await fetch("/api/feedback", { headers: t ? { "X-Owner-Token": t } : {} });
    const d = await r.json();
    if (d && d.reasons && d.reasons.length) FB.reasons = d.reasons;
    FB.stats = (d && d.stats) || null;
  } catch (e) { FB.stats = null; }
}

async function loadData() {
  // 反馈原因选项与统计：从后端拉，保证前后端口径同源；拉不到就用本地兜底选项
  await loadFeedback();
  try { AS.stats = await api("/api/stats"); } catch (e) { AS.stats = null; }
  const s = AS.stats || {};
  // 头像副标题：只放一句人话（多少条规则），不把三个数字都摊在侧栏。
  if ($("meSub")) $("meSub").textContent =
    `${s.rules ?? "—"} 条规则` + (s.appendix ? ` · ${s.appendix} 条附录` : "");
  const ok = !!s.llm_configured;
  if ($("st")) {
    $("st").className = "st " + (ok ? "ok" : "warn");
    // ⚠️ 这个 tooltip 原来写的是「写答案的模型已就绪 · deepseek:xxx」——
    //    模型链是运维信息，队员看到只会犯嘀咕。只说结果。
    $("st").title = ok ? "服务正常" : "暂时只能给你看规则原文，稍后再试";
  }
  if (s.assistants) AS.assistants = s.assistants;
  if (s.default_assistant) AS.assistantId = s.default_assistant;
  try { const saved = localStorage.getItem("frisbee.rag.assistant"); if (saved && AS.assistants.find(a => a.id === saved)) AS.assistantId = saved; } catch (e) { }
}

/* 左下角头像 = 全部功能的唯一入口。
   选「点击弹出」而不是「悬停弹出」，原因有三：
     ① 悬停浮层在触屏上不存在，等于给一半用户没有这个功能；
     ② 悬停触发的浮层没法把鼠标移进去点第二项，得靠延时和小心机，很脆；
     ③ 悬停是"你还没决定要看"，点击才是"我要进去"—— 语义更准。
   浮层从头像上方弹出并右对齐，视觉上仍然挂在侧栏这一列上。 */
function bindMe() {
  const btn = $("navMe"), pop = $("mePop");
  if (!btn || !pop) return;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (pop.classList.contains("open")) { pop.classList.remove("open"); return; }
    renderMePop(pop);
    const r = btn.getBoundingClientRect();
    // 向上展开：头像在侧栏最底部，向下展开必然出屏。
    // 必须先让它可见才能量到高度（display:none 时 offsetHeight 为 0），
    // 所以先加 .open 再在同一帧里修正位置 —— 浏览器不会把中间态画出来。
    pop.classList.add("open");
    const h = pop.offsetHeight, w = pop.offsetWidth;
    pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 12)) + "px";
    pop.style.top = Math.max(8, r.top - h - 8) + "px";
  });
}

/* 头像浮层：五项（查规则 / 练规则 / 我的笔记 / 设置 / 退出登录）。
   ⚠️ 2026-10-05 owner 说"太散了"时把七项收敛过，所以这里有一条**目的检验**：
      只有"我要看 / 我要做"的东西能上来，配置项一律进设置目录。
      「查规则」2026-10-17 上来的理由见 ME_MENU 上方（它不是配置项）；
      「意见反馈」同一天下放进设置（它是支持入口，不是每天要用的功能）。
   ⚠️ 顺序按"用得上的程度"排：查规则 / 练规则 / 笔记，
      设置是容器，退出永远最后（最不可逆）。 */
function renderMePop(pop) {
  pop.innerHTML = "";
  ME_MENU.forEach(it => {
    const cur = location.hash === "#/" + it.id;
    const b = el("button", "pop-i" + (cur ? " on" : "") + (it.id === "__signout" ? " danger" : ""));
    b.textContent = it.name;
    b.addEventListener("click", () => {
      pop.classList.remove("open");
      closeNav();
      if (it.id === "__signout") { showSignOut(); return; }
      go(it.id);
    });
    pop.appendChild(b);
  });
}

/* 「退出登录」：本产品目前没有账号体系（问答不带用户身份），
   所以这里不能假装有登录态 —— 骗一个假的"退出"比不做更糟。
   弹窗说清两件事：现在没有账号；点了会发生什么。 */
function showSignOut() {
  // 没有账号体系，但本机可能保存了 Owner 写令牌。退出动作至少要真的清掉它，
  // 否则"退出登录"是个假动作（2026-10-24 安全审查）。
  setOwnerToken("");
  openDlg({
    kind: "signout",
    title: "退出登录",
    body: `<p>Shultimate 目前不需要登录：你的对话只存在这台电脑上，不上传，也就没有账号可退出。</p>
      <p>本机保存的 Owner 写令牌已清除（如果有）。想清掉这台电脑上的记录，去设置里恢复默认即可。</p>`,
    okText: "知道了",
  });
}

/* 侧栏的「＋ 新对话」与助手选择：绑定一次，跨页面常驻。
   这两个控件原先住在对话页自己的侧栏里，合并后上移到唯一侧栏。 */
function bindSideChat() {
  $("newChat").addEventListener("click", startNewChat);

  const pop = $("astPop");
  const pick = $("astPick");
  pick.addEventListener("click", (e) => {
    e.stopPropagation();
    const r = pick.getBoundingClientRect();
    pop.innerHTML = '<div class="pop-head">换一种讲法</div>';
    AS.assistants.forEach(a => {
      const b = el("button", "pop-i" + (a.id === AS.assistantId ? " on" : ""));
      b.innerHTML = `<div class="pn">${esc(a.name)}${a.id === AS.assistantId ? ' <span class="tag">当前</span>' : ""}</div>
        <div class="pd">${esc(a.desc)}</div>`;
      b.addEventListener("click", () => {
        AS.assistantId = a.id;
        try { localStorage.setItem("frisbee.rag.assistant", a.id); } catch (err) { }
        pop.classList.remove("open");
        $("astName").textContent = ast().name;
        if (parseHash().main === "chat") paintChat();
      });
      pop.appendChild(b);
    });
    const m = el("button", "pop-i more");
    m.innerHTML = `<div class="pn">全部助手</div><div class="pd">看四种讲法的完整说明</div>`;
    m.addEventListener("click", () => {
      pop.classList.remove("open"); $("mePop").classList.remove("open");
      go("settings/answer");
    });
    pop.appendChild(m);
    pop.style.left = r.left + "px";
    pop.style.top = (r.bottom + 5) + "px";
    pop.classList.add("open");
  });

  // 历史对话搜索：默认收起，点放大镜才展开（对标 DeepSeek，不占常态空间）
  const hq = $("histQ");
  if (hq) hq.addEventListener("input", () => { CH.histQ = hq.value.trim(); paintHist(); });

  // 工作区标题旁的图标：只剩搜索 / 排序两件事。
  // ⚠️ 原来这里还有第三个（收起历史）。它和下面那行「默认工作区」是**同一件事**，
  //    所以收敛成一个：那行现在是真按钮（见 #wsItem），点它收起/展开列表。
  // ⚠️ 只做「对话列表」的三件事，不在这里放功能入口 ——
  //    工作区是对话的容器，不是导航层（导航层只有左下角账号行）。
  const wsSearch = $("wsSearch"), wsSort = $("wsSort");
  if (wsSearch) wsSearch.addEventListener("click", () => {
    const box = $("histSearch");
    const on = box.classList.toggle("on");
    wsSearch.classList.toggle("on", on);
    if (on) { CH.histQ = ""; if (hq) hq.value = ""; paintHist(); hq.focus(); }
    else { CH.histQ = ""; if (hq) hq.value = ""; paintHist(); }
  });
  if (wsSort) wsSort.addEventListener("click", () => {
    CH.sortAsc = !CH.sortAsc;
    wsSort.classList.toggle("on", CH.sortAsc);
    wsSort.title = CH.sortAsc ? "排序：最早优先" : "排序：最新优先";
    paintHist();
  });
  // 「历史对话」这一行：对话列表的组标题 + 折叠开关。
  const wsItem = $("wsItem");
  if (wsItem) wsItem.addEventListener("click", () => {
    const folded = document.body.classList.toggle("folded");
    wsItem.title = folded ? "展开历史对话" : "收起历史对话";
  });

  // 桌面客户端标记。窗口控制（减号/方框/叉）已交给系统绘制，HTML 里不再有按钮，
  // 所以这里只负责打 class —— CSS 靠它给标题栏留出 40px 并开启拖拽区。
  // 检测放在这里而不是 HTML 里：class 是行为的一部分，不是静态结构。
  if (window.__shultWin && typeof window.__shultWin.ctl === "function") {
    document.body.classList.add("is-desktop");
  }
}

function bootUI() {
  loadPref();
  $("burger").addEventListener("click", () => { $("nav").classList.add("open"); syncRail(); });
  $("scrim").addEventListener("click", () => { closeNav(); closeRail(); });
  // 视口跨过 1280 断点时，右栏的形态会变（宽屏是常驻列 / 窄屏是抽屉），
  // 开合判断也跟着变 ⇒ 必须重算一次，否则从窄屏拖宽会留下一个半开的抽屉。
  let _rsz = null;
  window.addEventListener("resize", () => { clearTimeout(_rsz); _rsz = setTimeout(syncRail, 120); });
  document.addEventListener("click", () => {
    $("astPop").classList.remove("open");
    $("mePop").classList.remove("open");
  });
  window.addEventListener("hashchange", routeNow);
  document.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); startNewChat(); }
  });
  if (!location.hash) location.hash = "#/chat";
  loadSessions();          // 侧栏要画历史，必须早于第一次 renderNav
  loadNotes();             // 笔记是本机的，开机即读，切到对话页就有内容
  bindSideChat();
  bindMe();
  bindAppMenu();           // 标题栏「应用 / 编辑」，网页版自动跳过
  $("astName").textContent = ast().name;
  routeNow();
}

(async function main() {
  bootUI();
  await loadData();
  $("astName").textContent = ast().name;
  const r = parseHash();
  // 这些页面首次渲染依赖 stats / assistants / settings，拿到后重绘一次。
  // ⚠️ 判断条件一律用「sub」而不是旧的 main 名：规则原文/检索/评测这些
  // 都已经归并到 settings/* 下了，main 恒为 "settings"，
  // 拿旧名字判断会永远不重绘，页面停在"加载中"。
  const isSet = () => parseHash().main === "settings";
  // ⚠️ 「查规则」也依赖 /api/chapters（「按章节翻」那个 tab）——
  //    判据漏了它，页面会永远停在"章节结构加载中…"（2026-10-17 升一级页时补的）。
  const needsChapters = () => ["settings", "rules"].includes(parseHash().main);
  if (["settings", "files", "chat", "rules"].includes(r.main) && r.main !== "chat") routeNow();
  else paintHist();
  try { const d = await api("/api/chapters"); AS.chapters = d.chapters || []; if (needsChapters()) routeNow(); } catch (e) { }
  try { const d = await api("/api/files"); AS.files = d; if (parseHash().main === "files") routeNow(); } catch (e) { }
  try { const d = await api("/api/settings"); AS.settings = d; if (isSet()) routeNow(); } catch (e) { }
  watchServerBuild();
})();

// ══════════════════════════════════════════════════════════════════════
//  「服务端换了新版」检测 —— 2026-10-15 加
//
//  为什么需要：
//    静态资源是 no-cache 的，**用户刷新就能看到新 UI**。
//    但如果用户**不关窗口**（这个 Chrome --app 窗口可以开一整天），
//    里面跑的还是几小时前加载的 JS ⇒ 他一直看旧界面，
//    而**服务端这边毫无察觉**，会以为"更新已经生效了"。
//
//  怎么做：
//    /api/health 返回一个 `build`（静态文件内容的指纹）。
//    页面加载时记下它，之后每隔一段时间（以及每次窗口重新可见时）
//    问一次；不一样就弹一条提示让用户点刷新。
//
//  ⚠️ 三条自我约束（避免变成骚扰）：
//    ① **只提示，不自动刷新** —— 用户可能正在输入问题，
//       自动刷新会把他打一半的字弄丢
//    ② **拿不到 / 出错时什么都不做** —— 网络抖一下不该弹"有新版本"
//    ③ 用户点了「以后再说」就不再提（同一次会话内）
// ══════════════════════════════════════════════════════════════════════
const BUILD_POLL_MS = 5 * 60 * 1000;
let __buildLoaded = null;      // 页面加载时服务端的 build
let __buildNotified = false;   // 已经提示过了？别重复弹

async function watchServerBuild() {
  try {
    const h = await api("/api/health");
    __buildLoaded = (h && h.build) || null;
  } catch (e) { return; }                    // 约束②：拿不到就不管
  if (!__buildLoaded) return;

  const check = async () => {
    if (__buildNotified) return;
    try {
      const h = await api("/api/health");
      const now = (h && h.build) || null;
      if (now && now !== __buildLoaded) showUpdateBanner(now);
    } catch (e) { /* 约束②：忽略 */ }
  };
  setInterval(check, BUILD_POLL_MS);
  // 用户切回来的时候也查一次 —— 这是最自然的时机
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) check();
  });
}

function showUpdateBanner(newBuild) {
  if (__buildNotified) return;
  __buildNotified = true;
  const bar = document.createElement("div");
  bar.id = "updateBar";
  bar.innerHTML =
    '<span>有新版本可用</span>' +
    '<button type="button" id="updateGo">立即刷新</button>' +
    '<button type="button" id="updateLater">以后再说</button>';
  document.body.appendChild(bar);
  const go = document.getElementById("updateGo");
  const later = document.getElementById("updateLater");
  if (go) go.onclick = () => {
    // ⚠️ 普通 location.reload() 可能仍走缓存；加一个时间戳强制回源，
    //    避开"刷新了还是旧 JS"这种最难查的情况。
    const u = new URL(location.href);
    u.searchParams.set("_v", String(newBuild));
    location.replace(u.toString());
  };
  if (later) later.onclick = () => bar.remove();
}

/* ==========================================================================
   复盘（Replay）—— 重要使用场景，产品总目标见 docs/product.md
   ==========================================================================
   复盘帮助盘友回看和理解一次经历；它不是“让所有人遵守规则”的软件保证，
   也不替代自然语言规则问答这一核心任务：

     输入一句话
        → 一张判定卡（结论 / 条款 / 下次）
        → 点「摆到场上」→ 卡展开成一块场，橙点就是那一下
        → 存进复盘本，下次赛前还能翻出来

   ⚠️ 三条纪律（别在后续迭代里破）：
     ① **不主动说话**：没有问候、没有"猜你想问"、没有自动推荐。
        用户带着问题来，第一屏只给他一个输入框。
     ② **惊喜由用户触发**：场不是一进来就铺开的，是他点「摆到场上」之后
        才展开的。产品不塞给他任何东西。
     ③ **不编**：卡片上的条款来自 `/api/card` 透传的 `citations`；
        "下次"是从回答里**摘**的一句，摘不到就空着。这里不生成规则。
   ========================================================================== */

function rpToday() {
  const d = new Date();
  return String(d.getMonth() + 1).padStart(2, "0") + "." + String(d.getDate()).padStart(2, "0");
}
function rpLoad() {
  try {
    const raw = localStorage.getItem(RP_KEY);
    const parsed = JSON.parse(raw || "[]");
    // 2026-10-24 R2：根必须是数组、元素必须是对象。`{"items":[...]}`
    // 是导出文件的外壳，不是 storage 格式；不能静默当空数组再写 []。
    if (!Array.isArray(parsed)
        || parsed.some(x => !x || typeof x !== "object" || Array.isArray(x))) {
      throw new Error("replay schema");
    }
    const items = parsed.slice(0, 100);
    // ⚠️ 用**实际磁盘值**（可能为 null）做冲突基线，不能把默认 "[]" 当成
    //    已有数据；否则第一次写入会被误判成"另一个窗口改过"。
    RP.lastRaw = raw || "";
    RP.broken = false;
    // ⚠️ 旧数据可能没有 id（2026-10-20 之前的存档）—— 补一个并落盘，
    //    否则复盘本里删除会按空 id 误删多条。
    let changed = false;
    RP.store = items.map((x, i) => {
      if (x.id) return x;
      changed = true;
      return Object.assign({}, x, { id: "rp-legacy-" + i + "-" + Date.now().toString(36) });
    });
    if (changed) rpPersist();
  } catch (e) {
    // 2026-10-24：解析/结构失败时**不要**把它当成空复盘本继续写 ——
    // 下一次 rpPersist() 会用空数组覆盖磁盘上的原始数据。
    RP.broken = true;
    RP.recovery = localStorage.getItem(RP_KEY) || "";
    RP.store = [];
    console.error("[storage] 复盘解析失败，已停止写入以免覆盖原始数据", e);
  }
}
function rpPersist() {
  if (RP.broken) return false;
  try {
    const val = JSON.stringify(RP.store.slice(0, 100));
    const cur = localStorage.getItem(RP_KEY) || "";
    // 2026-10-24 R2：最低限度冲突检测 —— 另一个窗口改过就拒绝静默覆盖。
    if (RP.lastRaw !== null && cur !== RP.lastRaw) {
      RP.conflict = true;
      toast("另一个窗口也改了复盘本；本次没有覆盖它。请先导出/关闭另一个窗口。");
      return false;
    }
    localStorage.setItem(RP_KEY, val);
    RP.lastRaw = val;
    return true;
  } catch (e) {
    toast("本地存储已满，复盘可能没存上。建议先导出备份。");
    return false;
  }
}
/* 2026-10-24 安全修复：pos 来自导入文件/localStorage，不能直接拼进 SVG。
   旧实现把任意字符串塞进 cx/cy 属性 ⇒ `0"/><foreignObject><img onerror=...>`
   可以在「摆到场上」时执行脚本（已用真实 Chromium 复现）。
   唯一允许进入 SVG 的坐标必须是有限数，并夹在场地范围内。 */
function rpSafePos(p) {
  if (!p || typeof p !== "object") return null;
  const x = Number(p.x), y = Number(p.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x: Math.max(0.8, Math.min(99.2, x)), y: Math.max(0.8, Math.min(36.2, y)) };
}
function rpShort(s, n) {
  s = String(s || "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}
function rpCiteName(x) {
  if (!x) return "";
  return x.label ? "附录 " + x.label : "规则 " + (x.id || "");
}

/* ---- 第一屏：只有一个输入框 ---- */
function rpAskHTML() {
  return `
    <div class="rp-ask" id="rpAsk">
      <input id="rpQ" class="rp-input" type="text" autocomplete="off"
             placeholder="输入规则问题，或描述那一下…" aria-label="输入规则问题">
      <button class="rp-go" id="rpGo" type="button" aria-label="查询">查</button>
    </div>
    <div class="rp-recent" id="rpRecent"></div>`;
}
function rpBindAsk() {
  const q = $("rpQ"), btn = $("rpGo");
  if (!q || !btn) return;
  const submit = () => rpAsk(q.value);
  btn.addEventListener("click", submit);
  q.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
  });
  // 桌面端聚焦、移动端不弹键盘 —— 键盘一弹出来，首屏的"空"就没了。
  if (window.matchMedia("(min-width: 720px)").matches) setTimeout(() => q.focus(), 60);
}

function viewReplayPage() {
  rpLoad();
  const wrap = el("div", "rp");
  wrap.innerHTML = `
    <header class="rp-top">
      <div class="rp-brand">Shultimate</div>
      <div class="rp-links">
        <button class="rp-link" id="rpBook" type="button">复盘本</button>
        <button class="rp-link" id="rpChat" type="button">问一句</button>
      </div>
    </header>
    <main class="rp-stage" id="rpStage">${rpAskHTML()}</main>`;
  mount(wrap);
  const book = $("rpBook");
  if (book) book.addEventListener("click", rpOpenBook);
  const chat = $("rpChat");
  if (chat) chat.addEventListener("click", () => go("chat"));
  rpBindAsk();
  rpRenderRecent();
}

function rpReset() {
  RP.card = null;
  RP.pos = null;
  RP.br = null;
  const stage = $("rpStage");
  if (!stage) return;
  stage.classList.remove("rp-loading", "rp-fielding", "rp-has-card");
  stage.innerHTML = rpAskHTML();
  rpBindAsk();
  rpRenderRecent();
}

/* ---- 问：只调 /api/card（内部仍走 handle_ask 那一条唯一控制流） ---- */
async function rpAsk(text) {
  const q = String(text || "").trim();
  if (!q || RP.busy) return;
  RP.busy = true;
  const stage = $("rpStage"), input = $("rpQ"), btn = $("rpGo");
  if (input) input.disabled = true;
  if (btn) btn.disabled = true;
  if (stage) stage.classList.add("rp-loading");
  try {
    const card = await post("/api/card", { q });
    RP.card = card;
    RP.pos = null;
    RP.br = null;
    rpRenderCard(card);
  } catch (e) {
    toast("没连上，再试一次");
    if (stage) stage.classList.remove("rp-loading");
  } finally {
    RP.busy = false;
    if (input) input.disabled = false;
    if (btn) btn.disabled = false;
  }
}

/* ---- 判定卡 ---- */
function rpRenderCard(c) {
  const stage = $("rpStage");
  if (!stage || !c) return;
  stage.classList.remove("rp-loading", "rp-fielding");
  stage.classList.add("rp-has-card");

  const cites = c.citations || [];
  const first = cites[0] || null;
  const isChat = c.mode === "chat" || c.mode === "converse";
  const off = !!(c.scope && (c.scope.route === "off_topic" || c.scope.route === "harm"));
  // "规则没写"只给真的没有依据的规则问题；闲聊/离题不算。
  const noRule = !isChat && !off &&
    (c.mode === "not_found" || c.confident === false || (!cites.length && !c.curated));

  const verdict = c.conclusion || c.answer || "";
  const detail = c.detail || "";
  const next = c.next_step || "";
  const label = first ? rpCiteName(first) : (c.curated ? "人工核定" : "");

  if (noRule) {
    stage.innerHTML = `
      <div class="rp-card is-empty">
        <div class="rp-q">${esc(c.question || "")}</div>
        <div class="rp-verdict">规则没写。</div>
        <div class="rp-note">不是我们不知道，是规则真的没写。</div>
        <div class="rp-detail">${md(verdict)}</div>
        ${rpMoreHTML(detail)}
        <div class="rp-actions">
          <button class="rp-act" id="rpAgain" type="button">再问一个</button>
          <button class="rp-act ghost" id="rpShare" type="button">分享卡</button>
        </div>
      </div>`;
    const again = $("rpAgain"); if (again) again.addEventListener("click", rpReset);
    const share = $("rpShare"); if (share) share.addEventListener("click", rpShare);
    rpBindMore();
    return;
  }

  if (isChat || off) {
    stage.innerHTML = `
      <div class="rp-card is-plain">
        <div class="rp-q">${esc(c.question || "")}</div>
        <div class="rp-detail">${md(verdict)}</div>
        ${rpMoreHTML(detail)}
        <div class="rp-actions">
          <button class="rp-act" id="rpAgain" type="button">再问一个</button>
          <button class="rp-act ghost" id="rpShare" type="button">分享卡</button>
        </div>
      </div>`;
    const again = $("rpAgain"); if (again) again.addEventListener("click", rpReset);
    const share = $("rpShare"); if (share) share.addEventListener("click", rpShare);
    rpBindMore();
    return;
  }

  stage.innerHTML = `
    <div class="rp-card" id="rpCard">
      <div class="rp-q">${esc(c.question || "")}</div>
      <div class="rp-verdict">${md(verdict)}</div>
      ${label ? `<div class="rp-rule">${esc(label)}</div>` : ""}
      ${first && first.text ? `<blockquote class="rp-clause">${esc(first.text)}</blockquote>` : ""}
      ${next ? `<div class="rp-next">下次 <b>${esc(next)}</b></div>` : ""}
      ${rpMoreHTML(detail)}
      <div class="rp-actions">
        ${(first || c.curated) ? `<button class="rp-act" id="rpField" type="button">摆到场上 <span>→</span></button>` : ""}
        ${cites.length && c.kind !== "procedure" ? `<button class="rp-act ghost" id="rpBranches" type="button">如果当时…</button>` : ""}
        ${cites.length ? `<button class="rp-act ghost" id="rpSource" type="button">原文</button>` : ""}
        <button class="rp-act ghost" id="rpSave" type="button">存进复盘本</button>
        <button class="rp-act ghost" id="rpShare" type="button">分享卡</button>
      </div>
      <div id="rpBr"></div>
      ${cites.length ? `<div class="rp-src" id="rpSrc" hidden></div>` : ""}
      <div class="rp-foot">只依据 WFDF 规则原文</div>
    </div>`;

  const fieldBtn = $("rpField");
  if (fieldBtn) fieldBtn.addEventListener("click", rpEnterField);
  const srcBtn = $("rpSource");
  if (srcBtn) srcBtn.addEventListener("click", () => {
    const box = $("rpSrc");
    if (!box) return;
    if (box.hidden) {
      box.innerHTML = rpSourceHTML(c);
      box.hidden = false;
      srcBtn.textContent = "收起原文";
    } else {
      box.hidden = true;
      srcBtn.textContent = "原文";
    }
  });
  const saveBtn = $("rpSave");
  if (saveBtn) saveBtn.addEventListener("click", rpSaveCard);
  const shareBtn = $("rpShare");
  if (shareBtn) shareBtn.addEventListener("click", rpShare);
  const brBtn = $("rpBranches");
  if (brBtn) brBtn.addEventListener("click", rpBranches);
  if (RP.br) rpRenderBranches(RP.br);
  rpBindMore();
}

function rpMoreHTML(detail) {
  if (!detail) return "";
  return `<div class="rp-more">
      <button class="rp-more-btn" id="rpMore" type="button">展开全文</button>
      <div class="rp-more-body" id="rpMoreBody" hidden>${md(detail)}</div>
    </div>`;
}
function rpBindMore() {
  const b = $("rpMore"), body = $("rpMoreBody");
  if (!b || !body) return;
  b.addEventListener("click", () => {
    body.hidden = !body.hidden;
    b.textContent = body.hidden ? "展开全文" : "收起全文";
  });
}

function rpSourceHTML(c) {
  const list = c.citations || [];
  if (!list.length) return `<div class="rp-src-empty">没有引用条款。</div>`;
  return list.map(x => `
    <div class="rp-src-i">
      <div class="rp-src-h">${esc(rpCiteName(x))}${x.chapter_title ? " · " + esc(x.chapter_title) : ""}</div>
      <p>${esc(x.text || "")}</p>
      ${(x.notes || []).length
        ? `<div class="rp-src-n">${esc((x.notes || []).map(n => (n && n.text) || "").filter(Boolean).join(" "))}</div>`
        : ""}
    </div>`).join("");
}

/* ---- 「如果当时…」：对已有判定做一次受控推演 ----
   ⚠️ 只在用户点按钮时请求，不自动生成。产品不塞东西给他。
   ⚠️ 服务端只收 clause_ids，条款原文由服务端按 id 从语料取 ——
      前端传的 citations 文本不作为依据。 */
async function rpBranches() {
  const c = RP.card;
  const box = $("rpBr"), btn = $("rpBranches");
  if (!c || !box || !btn) return;

  if (RP.br) {
    if (box.innerHTML) {
      box.innerHTML = "";
      btn.textContent = "如果当时…";
    } else {
      rpRenderBranches(RP.br);
      btn.textContent = "收起推演";
    }
    return;
  }

  const ids = (c.citations || []).map(x => x.id).filter(Boolean);
  if (!ids.length) { toast("没有可推演的条款"); return; }

  btn.disabled = true;
  btn.textContent = "在推演…";
  box.innerHTML = `<div class="rp-br-load">正在把另一种可能摆出来…</div>`;
  try {
    const out = await post("/api/branches", {
      q: c.question || "",
      answer: c.conclusion || c.answer || "",
      clause_ids: ids,
    });
    RP.br = out;
    rpRenderBranches(out);
    btn.textContent = "收起推演";
  } catch (e) {
    box.innerHTML = `<div class="rp-br-empty">这次没推演出来。再试一次。</div>`;
    btn.textContent = "如果当时…";
  } finally {
    btn.disabled = false;
  }
}

function rpRenderBranches(out) {
  const box = $("rpBr");
  if (!box) return;
  const branches = out.branches || [];
  const need = out.need || "";
  if (!branches.length) {
    box.innerHTML = need
      ? `<div class="rp-br-need"><b>要推演得更准，还差一句</b>${esc(need)}</div>`
      : `<div class="rp-br-empty">${esc(out.note || "这一步没有足够依据做推演。")}</div>`;
    return;
  }
  const byId = {};
  (out.clauses || []).forEach(x => { if (x && x.id) byId[x.id] = x; });
  ((RP.card && RP.card.citations) || []).forEach(x => {
    if (x && x.id && !byId[x.id]) byId[x.id] = x;
  });

  box.innerHTML = `
    <div class="rp-br">
      <div class="rp-br-h">如果当时…<span>推演，不是判罚</span></div>
      ${branches.map((b, i) => {
        const cid = String(b.clause_id || "");
        const cl = byId[cid] || null;
        const label = cl ? (cl.label ? "附录 " + cl.label : "规则 " + cl.id) : "";
        return `<div class="rp-br-i">
          <div class="rp-br-a"><em>${i + 1}</em>如果当时 ${esc(b.action || "")}</div>
          <div class="rp-br-c">${esc(b.consequence || "")}</div>
          ${label ? `<button class="rp-br-tag" type="button" data-cid="${esc(cid)}">${esc(label)}</button>
            <div class="rp-br-src" data-src="${esc(cid)}" hidden>${esc((cl && cl.text) || "")}</div>` : ""}
        </div>`;
      }).join("")}
      ${need ? `<div class="rp-br-need-inline"><b>还需要确认</b>${esc(need)}</div>` : ""}
    </div>`;

  box.querySelectorAll(".rp-br-tag").forEach(btn => {
    btn.addEventListener("click", () => {
      const src = box.querySelector(`[data-src="${btn.dataset.cid}"]`);
      if (src) src.hidden = !src.hidden;
    });
  });
}

/* ---- 卡展开成场：唯一的"惊喜" ---- */
function rpEnterField() {
  const stage = $("rpStage");
  if (!stage) return;
  stage.classList.add("rp-fielding");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  setTimeout(() => rpRenderField(), reduce ? 0 : 220);
}

function rpZoneAt(x, y) {
  if (x <= 18 || x >= 82) return "得分区";
  if (y <= 1.6 || y >= 35.4) return "边线附近";
  return "中心区";
}

function rpRenderField() {
  const c = RP.card;
  const stage = $("rpStage");
  if (!c || !stage) return;
  stage.classList.remove("rp-fielding");

  const first = (c.citations || [])[0] || null;
  const label = first ? rpCiteName(first) : (c.curated ? "人工核定" : "");
  const next = c.next_step || "";
  // ⚠️ 只接受有限数并夹进场地；绝不能把导入的字符串拼进 SVG 属性。
  const pos = rpSafePos(RP.pos || c.pos);

  stage.innerHTML = `
    <div class="rp-fieldwrap">
      <div class="rp-fieldmain">
        <div class="rp-fieldtop">
          <button class="rp-back" id="rpBack" type="button">← 返回</button>
          <span class="rp-fieldtag">位置只帮你回忆，不参与判定</span>
        </div>
        <div class="rp-fieldbox">
          <svg id="rpFieldSvg" viewBox="0 0 100 37" role="img" aria-label="飞盘场地示意图">
            <rect class="rp-turf" x="0" y="0" width="100" height="37" rx="0.8"/>
            <rect class="rp-ez" x="0" y="0" width="18" height="37"/>
            <rect class="rp-ez" x="82" y="0" width="18" height="37"/>
            <line class="rp-line" x1="18" y1="0" x2="18" y2="37"/>
            <line class="rp-line" x1="82" y1="0" x2="82" y2="37"/>
            <rect class="rp-bound" x="0" y="0" width="100" height="37" rx="0.8"/>
            <circle id="rpDot" class="rp-dot" cx="${pos ? pos.x : 50}" cy="${pos ? pos.y : 18.5}" r="${pos ? 1 : 0}"/>
          </svg>
          <div class="rp-zonetag" id="rpZone">${pos ? esc(rpZoneAt(pos.x, pos.y)) : "点一下场上的位置"}</div>
        </div>
      </div>
      <aside class="rp-side">
        <div class="rp-side-q">${esc(c.question || "")}</div>
        <div class="rp-side-v">${md(c.conclusion || "")}</div>
        ${label ? `<div class="rp-rule">${esc(label)}</div>` : ""}
        ${first && first.text ? `<blockquote class="rp-clause">${esc(first.text)}</blockquote>` : ""}
        ${next ? `<div class="rp-next">下次 <b>${esc(next)}</b></div>` : ""}
        <div class="rp-actions">
          <button class="rp-act" id="rpSave2" type="button">存进复盘本</button>
          <button class="rp-act ghost" id="rpShare2" type="button">分享卡</button>
        </div>
      </aside>
    </div>`;

  rpBindField();
  const back = $("rpBack");
  if (back) back.addEventListener("click", () => rpRenderCard(c));
  const save = $("rpSave2");
  if (save) save.addEventListener("click", rpSaveCard);
  const share = $("rpShare2");
  if (share) share.addEventListener("click", rpShare);
}

function rpBindField() {
  const svg = $("rpFieldSvg"), dot = $("rpDot"), zone = $("rpZone");
  if (!svg || !dot) return;
  const place = (ev) => {
    const r = svg.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const x = Math.max(0.8, Math.min(99.2, ((ev.clientX - r.left) / r.width) * 100));
    const y = Math.max(0.8, Math.min(36.2, ((ev.clientY - r.top) / r.height) * 37));
    dot.setAttribute("cx", x.toFixed(2));
    dot.setAttribute("cy", y.toFixed(2));
    dot.setAttribute("r", "1");
    RP.pos = { x, y };
    if (zone) zone.textContent = rpZoneAt(x, y);
  };
  let dragging = false;
  svg.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    dragging = true;
    try { svg.setPointerCapture(ev.pointerId); } catch (e) { /* 忽略 */ }
    place(ev);
  });
  svg.addEventListener("pointermove", (ev) => { if (dragging) place(ev); });
  const stop = () => { dragging = false; };
  svg.addEventListener("pointerup", stop);
  svg.addEventListener("pointercancel", stop);
}

/* ---- 复盘本：只存本机，不上传 ---- */
function rpSaveCard() {
  const c = RP.card;
  if (!c) return;
  const before = RP.store.slice();
  const beforeCard = RP.card;
  const item = Object.assign({}, c, {
    id: c.id || ("rp-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)),
    date: c.date || rpToday(),
    pos: rpSafePos(RP.pos || c.pos),
  });
  const i = RP.store.findIndex(x => x.id === item.id);
  if (i >= 0) RP.store[i] = item;
  else RP.store.unshift(item);
  if (RP.store.length > 100) RP.store.length = 100;
  RP.card = item;
  if (!rpPersist()) {
    RP.store = before;
    RP.card = beforeCard;
    toast("没有存上（本地存储不可用或另一个窗口已修改），原数据未改动");
    return;
  }
  toast("已存进复盘本");
}

function rpRenderRecent() {
  const box = $("rpRecent");
  if (!box) return;
  const items = (RP.store || []).slice(0, 6);
  if (!items.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="rp-recent-h">最近复盘</div>` + items.map((c, i) => `
    <button class="rp-recent-i" data-i="${i}" type="button">
      <span class="rp-recent-d">${esc(c.date || "")}</span>
      <span class="rp-recent-q">${esc(rpShort(c.question, 22))}</span>
      <span class="rp-recent-v">${esc(rpShort(c.conclusion, 10))}</span>
    </button>`).join("");
  box.querySelectorAll(".rp-recent-i").forEach(b => {
    b.addEventListener("click", () => {
      const c = RP.store[Number(b.dataset.i)];
      if (!c) return;
      RP.card = c;
      RP.pos = c.pos || null;
      RP.br = null;          // 换了一张卡：上一张的推演不能贴到这一张上
      rpRenderCard(c);
    });
  });
}

/* ---- 复盘本：管理、搜索、导出、导入、删除 ----
   ⚠️ 只操作 localStorage，不上传；导出文件也不含任何身份信息。
   ⚠️ 入口只有一个：「复盘本」（页头右上角），不在别处再放第二个。 */
function rpBookEsc(e) {
  if (e.key === "Escape") rpCloseBook();
}

function rpOpenBook() {
  if ($("rpBookMask")) return;
  RP.bookQuery = "";
  const mask = el("div", "rp-book-mask");
  mask.id = "rpBookMask";
  mask.innerHTML = `
    <div class="rp-book" role="dialog" aria-modal="true" aria-label="复盘本">
      <div class="rp-book-h">
        <div class="rp-book-t"><b>复盘本</b><span id="rpBookCnt"></span></div>
        <button class="rp-book-x" id="rpBookX" type="button" aria-label="关闭">×</button>
      </div>
      <div class="rp-book-tools">
        <input id="rpBookQ" class="rp-book-q" type="search" placeholder="搜复盘本" autocomplete="off" aria-label="搜索复盘">
        <button class="rp-act ghost" id="rpBookExport" type="button">导出</button>
        <button class="rp-act ghost" id="rpBookImport" type="button">导入</button>
        <button class="rp-act ghost rp-book-danger" id="rpBookClear" type="button">清空</button>
        <input id="rpBookFile" type="file" accept=".json,application/json" hidden>
      </div>
      <div class="rp-book-list" id="rpBookList"></div>
    </div>`;
  document.body.appendChild(mask);

  const x = $("rpBookX"); if (x) x.addEventListener("click", rpCloseBook);
  mask.addEventListener("click", (e) => { if (e.target === mask) rpCloseBook(); });
  const q = $("rpBookQ");
  if (q) q.addEventListener("input", () => { RP.bookQuery = q.value; rpRenderBookList(); });
  const ex = $("rpBookExport"); if (ex) ex.addEventListener("click", rpExportBook);
  const im = $("rpBookImport"), fi = $("rpBookFile");
  if (im && fi) {
    im.addEventListener("click", () => fi.click());
    fi.addEventListener("change", () => {
      const f = fi.files && fi.files[0];
      fi.value = "";
      rpImportBook(f);
    });
  }
  const cl = $("rpBookClear"); if (cl) cl.addEventListener("click", rpClearBook);
  document.addEventListener("keydown", rpBookEsc);
  rpRenderBookList();
  setTimeout(() => { const qq = $("rpBookQ"); if (qq) qq.focus(); }, 50);
}

function rpCloseBook() {
  const m = $("rpBookMask");
  if (m) m.remove();
  document.removeEventListener("keydown", rpBookEsc);
  RP.bookQuery = "";
}

function rpRenderBookList() {
  const box = $("rpBookList"), cnt = $("rpBookCnt");
  if (!box) return;
  if (RP.broken) {
    if (cnt) cnt.textContent = "";
    box.innerHTML = `<div class="rp-book-empty">复盘数据的结构无法识别。为免覆盖原件，已停止写入。请先导出原始数据保存。</div>
      <button class="rp-act ghost" id="rpBookRecovery" type="button">导出损坏的原始数据</button>`;
    const b = $("rpBookRecovery");
    if (b) b.addEventListener("click", () => downloadText(RP.recovery, "frisbee-replay-recovery.txt"));
    return;
  }
  const q = (RP.bookQuery || "").trim().toLowerCase();
  const items = (RP.store || []).filter(c => !q ||
    [c.question, c.conclusion, c.date].some(x => String(x || "").toLowerCase().includes(q)));
  if (cnt) cnt.textContent = ` · ${RP.store.length} 条`;
  if (!items.length) {
    box.innerHTML = `<div class="rp-book-empty">${RP.store.length
      ? "没有匹配的复盘。"
      : "还没有存过。问一句，点「存进复盘本」就会到这里。"}</div>`;
    return;
  }
  box.innerHTML = items.map(c => `
    <div class="rp-book-i">
      <button class="rp-book-open" data-id="${esc(c.id || "")}" type="button">
        <span class="rp-book-d">${esc(c.date || "")}</span>
        <span class="rp-book-t">${esc(rpShort(c.question, 34))}</span>
        <span class="rp-book-v">${esc(rpShort(c.conclusion, 14))}</span>
      </button>
      <button class="rp-book-del" data-del="${esc(c.id || "")}" type="button" aria-label="删除这条">×</button>
    </div>`).join("");
  box.querySelectorAll(".rp-book-open").forEach(b => b.addEventListener("click", () => {
    const c = RP.store.find(x => x.id === b.dataset.id);
    if (!c) return;
    rpCloseBook();
    RP.card = c;
    RP.pos = c.pos || null;
    RP.br = null;
    rpRenderCard(c);
  }));
  box.querySelectorAll(".rp-book-del").forEach(b => b.addEventListener("click", () => {
    rpDeleteBookItem(b.dataset.del);
  }));
}

function rpDeleteBookItem(id) {
  if (!id) return;
  const before = RP.store.slice();
  RP.store = RP.store.filter(x => x.id !== id);
  if (!rpPersist()) {
    RP.store = before;
    rpRenderBookList(); rpRenderRecent();
    toast("没有删除：本地存储写入失败或另一个窗口已修改，原数据未改动");
    return;
  }
  rpRenderBookList(); rpRenderRecent();
  toast("已删除");
}

function rpExportBook() {
  if (!RP.store.length) { toast("复盘本还是空的"); return; }
  const payload = {
    app: "shultimate", kind: "review-book", version: 1,
    exported_at: new Date().toISOString(), items: RP.store,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "shultimate-review-book-" + new Date().toISOString().slice(0, 10).replace(/-/g, "") + ".json";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast("已导出");
}

function rpImportCard(x, i, seen) {
  /* 2026-10-24：导入文件是**不可信输入**。旧实现 Object.assign 原样收下，
     连 pos 都直接拼进 SVG（已在真实 Chromium 复现 XSS）。
     现在只按白名单重建卡片字段，pos 必须过 rpSafePos。 */
  if (!x || typeof x !== "object") return null;
  const q = String(x.question || "").slice(0, 1000);
  const conclusion = String(x.conclusion || "").slice(0, 4000);
  if (!q && !conclusion) return null;
  const id = String(x.id || ("rp-import-" + Date.now().toString(36) + "-" + i)).slice(0, 120);
  if (seen.has(id)) return null;
  seen.add(id);
  const cites = Array.isArray(x.citations) ? x.citations.slice(0, 20).map(c => {
    if (!c || typeof c !== "object") return null;
    return {
      id: String(c.id || "").slice(0, 40),
      text: String(c.text || "").slice(0, 2000),
      label: String(c.label || "").slice(0, 40),
      type: String(c.type || "").slice(0, 24),
    };
  }).filter(Boolean) : [];
  return {
    id,
    date: String(x.date || rpToday()).slice(0, 40),
    question: q,
    conclusion,
    next_step: String(x.next_step || "").slice(0, 500),
    kind: x.kind === "procedure" ? "procedure" : "judgment",
    mode: String(x.mode || "").slice(0, 24),
    confident: x.confident !== false,
    citations: cites,
    pos: rpSafePos(x.pos),
    scope: (x.scope && typeof x.scope === "object")
      ? { route: String(x.scope.route || "").slice(0, 24) } : null,
    curated: (x.curated && typeof x.curated === "object")
      ? { tag: String(x.curated.tag || "").slice(0, 24) } : null,
  };
}

function rpImportBook(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { toast("文件太大了（上限 2MB）"); return; }
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const obj = JSON.parse(String(reader.result || ""));
      const items = Array.isArray(obj) ? obj : (obj && Array.isArray(obj.items) ? obj.items : null);
      if (!items) throw new Error("not a book");
      if (items.length > 500) { toast("条目太多（上限 500 条）"); return; }
      const seen = new Set((RP.store || []).map(x => x.id).filter(Boolean));
      const adds = [];
      let skipped = 0;
      items.forEach((x, i) => {
        const card = rpImportCard(x, i, seen);
        if (card) adds.push(card); else skipped++;
      });
      // 只有在**解析/校验全部成功**后才动内存；写入失败要回滚。
      const before = RP.store.slice();
      RP.store = adds.concat(before).slice(0, 100);
      if (!rpPersist()) {
        RP.store = before;
        toast("本地存储写入失败，导入未生效");
        return;
      }
      rpRenderBookList(); rpRenderRecent();
      toast(`导入 ${adds.length} 条` + (skipped ? `，跳过 ${skipped} 条` : ""));
    } catch (e) {
      toast("这个文件不是复盘本");
    }
  };
  reader.readAsText(file, "utf-8");
}

let _rpClearArmed = false;
function rpClearBook() {
  const btn = $("rpBookClear");
  if (!btn) return;
  if (!_rpClearArmed) {
    _rpClearArmed = true;
    btn.textContent = "再点一次确认";
    setTimeout(() => { _rpClearArmed = false; if (btn) btn.textContent = "清空"; }, 3000);
    return;
  }
  _rpClearArmed = false;
  btn.textContent = "清空";
  const before = RP.store.slice();
  RP.store = [];
  if (!rpPersist()) {
    RP.store = before;
    rpRenderBookList(); rpRenderRecent();
    toast("没有清空：本地存储写入失败或另一个窗口已修改，原数据未改动");
    return;
  }
  rpRenderBookList(); rpRenderRecent();
  toast("已清空");
}

/* ---- 分享卡：纯 Canvas，不引任何库 ---- */
function rpShare() {
  const c = RP.card;
  if (!c) return;
  const W = 900, H = 1200;
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const g = cv.getContext("2d");
  if (!g) { toast("图片生成失败"); return; }

  const SANS = "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif";
  g.fillStyle = "#faf8f3"; g.fillRect(0, 0, W, H);
  g.strokeStyle = "#ddd6c6"; g.lineWidth = 2; g.strokeRect(36, 36, W - 72, H - 72);

  g.textBaseline = "top";
  g.fillStyle = "#7a786f"; g.font = "600 22px " + SANS;
  g.fillText("Shultimate", 72, 76);
  g.textAlign = "right"; g.fillText("复盘卡", W - 72, 76); g.textAlign = "left";

  let y = 140;
  g.fillStyle = "#7a786f"; g.font = "22px " + SANS;
  y = rpCanvasWrap(g, c.question || "", 72, y, W - 144, 34);

  y += 22;
  g.fillStyle = "#1c1b18"; g.font = "700 44px " + SANS;
  y = rpCanvasWrap(g, c.conclusion || c.answer || "", 72, y, W - 144, 62);

  const first = (c.citations || [])[0];
  if (first) {
    y += 20;
    g.fillStyle = "#2e6b4f"; g.font = "600 20px " + SANS;
    g.fillText(rpCiteName(first), 72, y);
    y += 34;
    g.fillStyle = "#4a4840"; g.font = "20px " + SANS;
    y = rpCanvasWrap(g, rpShort(first.text || "", 120), 72, y, W - 144, 34);
  }

  if (c.next_step) {
    y += 22;
    g.fillStyle = "#e4602a"; g.font = "600 20px " + SANS;
    g.fillText("下次", 72, y);
    g.fillStyle = "#1c1b18"; g.font = "600 26px " + SANS;
    y = rpCanvasWrap(g, c.next_step, 132, y, W - 204, 40);
  }

  const fy = Math.min(Math.max(y + 56, 760), 920);
  rpCanvasField(g, 72, fy, W - 144, 230, RP.pos || c.pos || null);

  g.fillStyle = "#9a978c"; g.font = "18px " + SANS;
  g.fillText("位置只帮你回忆，不参与判定", 72, H - 96);
  g.textAlign = "right"; g.fillText(rpToday(), W - 72, H - 96); g.textAlign = "left";

  cv.toBlob((blob) => {
    if (!blob) { toast("图片生成失败"); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "shultimate-" + rpToday().replace(".", "") + ".png";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast("卡片已生成");
  }, "image/png");
}

/* 中文按字换行（Canvas 没有自动换行） */
function rpCanvasWrap(g, text, x, y, maxW, lineH) {
  const chars = String(text || "").replace(/\s+/g, " ").trim().split("");
  let line = "";
  for (const ch of chars) {
    const test = line + ch;
    if (g.measureText(test).width > maxW && line) {
      g.fillText(line, x, y);
      y += lineH;
      line = ch;
    } else {
      line = test;
    }
  }
  if (line) { g.fillText(line, x, y); y += lineH; }
  return y;
}

function rpCanvasField(g, x, y, w, h, pos) {
  const sx = w / 100, sy = h / 37;
  g.fillStyle = "#f3efe4"; g.fillRect(x, y, w, h);
  g.fillStyle = "#e9e4d6";
  g.fillRect(x, y, 18 * sx, h);
  g.fillRect(x + 82 * sx, y, 18 * sx, h);
  g.strokeStyle = "#b9b3a4"; g.lineWidth = 1.5;
  g.strokeRect(x, y, w, h);
  g.beginPath();
  g.moveTo(x + 18 * sx, y); g.lineTo(x + 18 * sx, y + h);
  g.moveTo(x + 82 * sx, y); g.lineTo(x + 82 * sx, y + h);
  g.stroke();
  const px = x + Math.max(0.8, Math.min(99.2, (pos && pos.x) || 50)) * sx;
  const py = y + Math.max(0.8, Math.min(36.2, (pos && pos.y) || 18.5)) * sy;
  g.beginPath(); g.arc(px, py, 9, 0, Math.PI * 2);
  g.fillStyle = "#e4602a"; g.fill();
  g.strokeStyle = "#fffdf8"; g.lineWidth = 3; g.stroke();
}




