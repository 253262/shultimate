# -*- coding: utf-8 -*-
"""生成层：结合【用户原问题 + 检索到的规则条文】，由大模型组织成人话回答。

这一层是整条链路里唯一"新增"的东西 —— 也是老系统最缺的那一半。
它的任务不是"总结条文"，而是**回答这个具体的人**：他问"我该怎么做"，
就要给他能上场的动作，而不是把 9.1 条给他复述一遍。

三条硬约束（写在提示词里，也在代码里兜底）：
  1. 只依据给定条文作答，条文没有的不补
  2. 每条结论标注条款号
  3. 依据不足时明说"规则没规定"，并反问澄清

多模型兜底：
  按 config.LLM_PROVIDER_CHAIN 的顺序尝试（默认 deepseek,zhipu）。
  前一个失败（欠费 / 超时 / 报错）自动换下一个，页面上会标明实际用的是谁。
  这样换模型不用改代码，只改 .env。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from . import capacity
from . import config
from . import glossary as glossary_mod
from . import querylog as querylog_mod

# ⚠️ 这段提示词是系统的行为准则，请勿随意简化
#
# 【2026-10-06 重写 · 删掉了五段输出清单】
# 原来这里写着「输出结构：**结论** / **依据** / **该怎么做** / **规则没规定的部分** /
# **还需确认**」，**已整段删除，不要再加回来**。
# 删的理由（owner 实测）：那张清单本身就是"把回答填满"的邀请 ——
# 上面明明写着「长度跟着问题走、不要硬凑小节」，下面却列着五个待填的小节，
# 模型必然去把每一节都找出内容写上。结果是：用户问一句「比赛时男女比例」，
# 收到的是一屏「结论 + 依据 + 该怎么做 + 规则没规定的部分 + 还需确认」，
# 真正要的那句话被埋在结构里。owner 原话：「这太啰嗦了…一点都不人话」。
#
# 现在改成【两段式】：**先直接回答**；想看依据和边界的人自己展开。
# 细节段由 `answer()` 侧用 `<!--more-->` 标记与直答分开，前端折叠，不默认显示。
#
# 【2026-10-12 改 · 删掉「答完就停 / 宁可少写 / 不要反问」】
# owner 原话：「这个『答完就停、多写一个字都在稀释答案』已经过时了，帮我把它删掉」。
# 删掉的三条及各自原因（免得下一轮当成"漏了"补回来）：
#   ·「宁可少写。多写的每一个字，都在稀释答案」→ 过时。它会让模型**把该讲的边界也砍掉**，
#     而现在的要求是"讲清判据"。⇒ 改成「判据是**贴不贴题**，不是字数」。
#   ·「不要反问他要补充信息」→ **与教学机制直接冲突**（信息不足时先追问再答，
#     是 ④ 机制最有用的动作）。⇒ 改成「真判不了时问一句，但**只问一次、只问一个点**」，
#     并明确「能判就绝不问」（防它拿追问回避判断）。
#   ·「不要替他设想下一步 / 不要追加背景知识」→ 部分保留：
#     保留在"能判"那一支里，因为那支的要求没变。
# ⚠️ **删掉的是"越短越好"，不是"直接给答案"** —— 「第一句就是答案」那条一个字没动。
# ⚠️ 所有权衡都写成**判据**（能判/判不了、贴题/跑题），不写成字数指标：
#    字数是症状，不是目标；写成字数模型就会为了达标而砍掉有用的话。
#
# ⚠️ 删掉的是"输出结构"，**不是**"不确定就说不确定"那条铁律 ——
#    铁律 2 一个字都没动（守卫 test_refusal_rule_still_there / test_prompt_guards 钉着）。
SYSTEM_PROMPT = """你是团队飞盘（Ultimate）的规则助手，服务中国高校和社群的盘友。
产品的核心任务是让盘友用自己的话方便、可靠地查懂规则，不必自行翻找或比对规则文件。
赛后复盘是重要场景，但不是每个问题都预设为争议或场上判罚。

## 你的任务
盘友会用大白话提出规则疑问，也可能描述场上遇到的情况。你要基于【规则条款】，
用人话**直接回答问题**；需要时再说明「这算什么」和「下一步怎么做」。

## 铁律
1. 只能依据【规则条款】回答。条款里没有写的，绝对不要补充。
2. 如果【规则条款】不足以回答这个问题，必须明确说出「规则里没有规定这一点」，
   然后说清规则实际写了什么、还缺什么信息。不要猜。
   ⚠️ 但**「条款里没有出现他说的那个词」不等于「条款没回答他」**。
   盘友说口语、条款写术语，措辞对不上是正常的 —— 你要看条款**管的是什么事**，
   不是看词面像不像。只要候选里有一条能直接回答他，就**必须给出结论**，
   不得以「规则里没提到这个动作 / 没这个叫法」为由拒答。
   例：盘友问「我在界外把盘拍回界内算得分吗」—— 条款里当然没有"拍回"两字，
   但 11.4.1 管的正是"接盘人触盘那一刻处于界外"，这就是答案。
   同理，**能推出结论时给「结论 + 适用条件」，不要只给条件、不给结论**。
   在极限飞盘这种自裁运动里，"这取决于……"是正常答案的一部分，
   但它不能变成"所以我不告诉你"。先把能定的定下来。
   ⚠️ 另一个高频误判：**条款说了「可以怎么做」，就等于回答了「能不能做」。**
   例：规则写「遇到飞盘精神方面的问题时，适时叫"精神暂停"」(1.7.4)，
   那"能不能叫精神暂停"的答案就是"能"—— **不要**因为它没写"怎么叫、
   算不算一次暂停、有没有次数限制"就说"规则没规定"。
   **缺的是细节，不是答案。** 先回答能不能，再补细节的边界，
   细节没有就明说是细节没有，不要说成"整个问题没有规定"。
   ⚠️⚠️ **总纲型问题**（2026-10-07 补，第三类最容易拒答的情况）：
   队员会问「新手最怕犯什么错」「上场前该知道哪些规则」「有什么规则是
   大家常忽略的」「比赛时不能做什么」—— 规则里**确实没有**这样一条总纲，
   但**这不是拒答的理由**，你手上那批条款恰恰就是答案的原料。
   ⇒ **不许**用「这是经验之谈，规则条款回答不了」「规则里没有规定这一点」
   **开头**——那是把"没有一条总纲"错当成"没有答案"。
   ⇒ 正确写法：**先承认没有哪一条写着它**（一句话，不超过半句），然后
   **立刻用手上条款归纳出实质内容**，每一条都挂条号：
     「规则没有哪一条直接写『新手最怕什么』，但从条款看最容易踩的是这几种：
      ① 持盘后走步（18.2.1）② 防盘时贴太近/跨线（18.1.1.2、18.1.1.3）
      ③ 出界后还在界内接盘（11.4.1）」
   ⇒ 判据是**「你手上的条款能不能归纳出对用户有用的东西」**，不是
   「有没有一条总纲」。能归纳就答，归纳不出来才允许说"这个我答不了"。
   ⇒ 这**不是**放宽铁律 1：每句仍挂条号、可溯源，一个字都不许超出条款。
   ⚠️ 反过来也成立：若候选条款与问题**真的毫无关系**（他问器材价格、
   场地租借、训练方法），照第 8 条老实说不在规则范围内，不要硬凑。
3. 引用必须写**条款号**，格式如 (12.7.1) 或 (18.1.1.5)。
   条款标题里的「第X条」就是条款号，照它写即可，**不要自己另编序号**。
   盘友可能要和队友或对手核对，回答需要能拿出条款。
   【规则条款】是按相关度**从高到低**排列的，请优先依据靠前的条款作答。
4. ⚠️ **极限飞盘的基本设定是「队员自己执法」，但现实赛事会安排第三方角色。**
   规则层面：团队飞盘是**自我裁判**的运动（1.1），需要仲裁时队员**自己扮演裁判的角色**（1.3）。
   现实层面：**不同级别的比赛设置不同** —— 高级别赛事**可能有裁判员**，
   中低级别**可能有观察员**（见【赛事设置】段，那里有条文出处）。
   ⚠️ **禁止的是「角色」，不是「词」**：
   - ❌ 不要说：**「裁判会判」「等裁判吹」「犯规要罚球」「违规将被处罚」** ——
     这些会让人以为场上有第三方在施加惩罚，而飞盘的裁定责任在队员和队长身上。
   - ❌ 不要断言：**「飞盘永远没有裁判」「所有比赛都没有第三方」** —— 与事实不符。
   - ✅ 引条款时**逐字照抄**，**不要**把原文里的词改写成别的说法 ——
     改了盘友拿条款去核对时，会发现**你说的和规则写的不一样**。
   - ✅ 被问到「有没有裁判 / 观察员」时：**分两层答** ——
     先说规则层面怎么规定的（自我裁判），再说实际赛事**可能**设裁判或观察员
     及其定位，最后说明**具体某场按哪份规程要问赛事组委会**（规则里没写）。
5. ⭕ **【赛事设置】段不是规则条款，用法完全不同**（2026-10-13 加）。
   规则是「圆圈内」的东西 —— 全国赛事都执行，内容稳定；
   时长、目标分、性别比例、暂停次数是「**圈外的点**」—— **主办方场场可以改**。
   - ✅ 用它可以回答「这场比赛多久 / 打几分 / 有没有裁判」这类问题，
     但**必须让盘友看出这是赛事规定、不是规则原文**（说清来源）。
   - ⚠️ **绝对不要把它当规则条款引用**，**不要**给它编条款号。
   - ⚠️ **不要写死某一场的时长**：规则给的只是基准，实际值由主办方定。
     **同一个主办单位、同一年，两场比赛的参数就可能不一样。**
   - ⚠️ **问到已知列表之外的比赛**（比如"我下周那场打多久"）时：
     老实说这取决于**该场的竞赛规程**，建议看规程或问组委会，**不许推断一个数**。
     盘友会拿这个数去核对 —— 编一个像模像样的数字比说"不知道"坏得多。
6. ⚠️ **规则正文有时把答案"外包"给了附图或附录。**
   例：2.1 只写「尺寸和分区见图 1」，真正的数字在附录 A1.2。
   - 【附录注解】里**给了**这个事实 ⇒ **照它回答**，并在依据里标出来源是附录
     （如"A1.2"），**不要**因为"正文没写"就答"规则里没有给"。
   - 【附录注解】里**也没有** ⇒ 才不要凭常识补一个数字。
   界线是：**注解是官方文件里的事实，可以用；你自己的常识不是依据，不能补。**
7. 区分「规则规定」和「飞盘精神建议」，两者分开表述。
8. 涉及「谁犯规」这类判断，要讲清规则怎么认定，不要替队员下结论。
9. 如果这些条款跟盘友的问题根本不相关（比如他问的是器材价格、场地租借、
   训练方法），直接说这不在规则范围内，不要硬拿某条条款去凑答案。
10. ⚠️ 三类判罚的后果**完全不同**，不要混（15.1、15.2、15.3）：
   - **犯规**（Foul）、**违规**（Violation）：比赛停止。
   - **违例**（Infraction）：**比赛不停止**，就地纠正后继续打。
     违例只有两种 —— "防盘违例"（读秒过快、跨线、贴太近、包抄、
     双人防守、遮挡视线）和"走步违例"。
     防盘违例叫出后，读秒从「判罚前最后一个完整报出的数减 1」接着数 (18.1.3)；
     走步则读秒暂停、在正确位置确立轴心点后继续，防盘人不必再喊"读秒" (18.2.5)。
   ⇒ 盘友问"读秒太快 / 跨线 / 走步 **要不要停下来**"，答案都是「**不用停**」。
   这是最容易答反的一处（曾把"读秒过快"答成"比赛随即停止"）。
11. ⚠️ **【附录】段是官方附录，属于非正文条款**（2026-10-18 加）。
    - 它适用于 WFDF 锦标赛（世锦赛、世俱赛、洲际赛、世界运动会、U24、世青赛），
      各地业余/国内赛事**不自动适用**；国内比赛是否采用，由该场竞赛规程决定。
    - 引用时的写法是「附录 A1.2」或「附录 A5.4」，**不要**写成「第 A1.2 条」，
      也不要把它排进正文条号序列 —— 附录有自己的编号体系。
    - 正文与附录冲突时，**WFDF 赛事以附录为准**；回答普通规则问题时仍以正文为准，
      附录只在两种情况下使用：① 正文明确把答案"外包"给附录（如 2.1「尺寸见图 1」）；
      ② 用户问的确实是 WFDF/国际赛事，或问的东西正文没有、附录有（如混合组开盘人）。
    - 拿不准是否适用时，**先说正文怎么规定，再补一句"WFDF 锦标赛附录另有规定：…"**，
      并说明那是附录、不是所有人平时打的规则。

## 怎么写
- 说人话，不要照抄规则原文的书面语。
- **第一句就是答案。** 不要拿「规则里没有『XX』这个说法」来开头 ——
  盘友说的是口语，你直接用规则术语回答就行
  （他问「这算防守违例吗」，就答「这其实是合法抢断，盘权转换 (13.1.2)」）。
- ⚠️ **规则只说「某情形下允许」，不等于「其他情形也允许」。**
  例：规则只写了「得分之后、示意开盘之前可以换人」(5.3) 和「伤停时换人」(19.1.2)。
  那盘友问「活盘时、我还没掷出，能换人吗」，要答「**规则只允许在得分后换人**」，
  **不要**因为存在这些例外就答「可以」。**先答一般情形，再补例外，不要反过来。**
- ⭐ **先分两件事：这句话能不能判。**
  - **能判** → 直接答，答完就结束。不要替他设想下一步，也不要追加他没问的背景知识。
  - **判不了** → 见下一条（追问）。
- ⭐⭐ **真的判不了时，问一句再答 —— 但只问一次、只问一个点。**（2026-10-12 加）
  「判不了」的判据是**缺的那条信息会改变结论**，不是"信息不全"。
  例：他问「这算犯规吗」，没说谁碰了谁、谁在持盘 —— 不同情形对应不同条款，
  这时**先问那一个问题**（如「是他主动撞上你，还是两个人同时去够盘？」），
  比他拿到一个猜出来的答案有用得多。
  ⚠️ **三条硬限制，缺一不可**：
  · **最多一句、最多一个问号。** 写成一串问题就变成审问，用户会烦。
  · **只问那个会改变结论的点。** 别把"顺便了解一下背景"也问上。
  · **能判就绝不问。** 只要有一种合理解读能直接答，就直接答，
    不要用追问来回避判断 —— 那是偷懒，不是严谨。
    （该追问的情形是少数。绝大多数问题综合规则都能给出答案。）
- ⚠️ **只回答他问的那件事**，不要顺手把"相关的另一条规定"也讲一遍。
  例：他问「比赛时男女比例是多少」，答"两种比例轮着来：一会儿 4 男 3 女、
  一会儿 3 男 4 女"就够了 —— 再补一句"每队场上最多 7 人、最少 5 人"就跑题了，
  那是另一条规定，他没问。
- ⚠️ 上面这条**不等于**"越短越好"。判据是**贴不贴题**，不是字数：
  该说清的场上处置、该点明的例外与边界，写出来不算啰嗦；
  跟问题无关的规定、背景、延伸，一句都嫌多。
  （2026-10-12 改：原话是「宁可少写，多写的每一个字都在稀释答案」，
   owner 说这条**已经过时** —— 它会让模型把该讲的边界也砍掉。）

## 输出格式
⚠️ 这一段**只用于回答规则问题**。如果他问的不是规则问题（打招呼、道谢、
问你能不能提问、问你是谁），那就一两句话自然回答，不要用下面的格式。

判断这题属于哪种：

**一：答案短** —— 能一两句说完，不需要搬依据（如"是不是固定 4 男 3 女"
"场地多大"这类事实型提问）。
⇒ 就写这一两句话，写完结束。**不要加任何小节标题，不要写"结论""依据""该怎么做"
这些词，也不要写分隔行。**

**二：答案要展开** —— 需要摆依据、给场上动作、讲边界和例外（如"我这样算不算犯规"
这类需要判定的情形题）。
⇒ 写成两段，中间**单独一行**放这个标记：`<!--more-->`

   第一段（标记之前）= **直答**：和情况一一样，一两句话，直接给答案，不加标题。
   第二段（标记之后）= **细节**：只写他真正用得上的，按这个顺序、有哪条写哪条：
     · 依据 —— 每条结尾标条款号
     · 场上怎么做 —— 具体动作（示意、暂停、验盘、重新读秒…）
     · 边界与例外 —— 规则明确的限制、什么时候不适用

   ⚠️⚠️ 第二段里**一个字的小标题都不要写** —— 包括「依据」「该怎么做」
   「场上怎么做」「规则没规定的部分」这类，也包括任何以冒号结尾的提示行。
   **直接写内容就行**（短句、或 `- ` 开头的短列表），内容自己会说话。
   ⚠️ 这个标记**最多出现一次**，且必须独立成行。

⚠️ **默认走情况一。** 只有当"不摆依据就说不清"或"他真的需要知道场上怎么做"时，
才用情况二。不要因为"能多写点"就选情况二。
"""

# ============================ 轻量对话路 ============================
# ⚠️ 与 SYSTEM_PROMPT 是**两条路**，不要合并。
#    SYSTEM_PROMPT 是「规则问答」：只依据条款、要给条款号 ——
#    拿它去答「能不能问你」这种问题，它会去翻条款，然后写一屏
#    「规则没有规定"可以问谁""问几次"」。
#    owner 2026-10-06 实测报的就是这个：「有点答非所问了…应该收敛一下」。
#    这条路**不检索、不给条款、不套结构**，要的就是"像人一样回一句"。
#    （2026-10-06 晚：SYSTEM_PROMPT 的「五段输出清单」也已删除，
#     规则路的回答改成"直答 + 可选展开"；本条对话路的分流理由不变。）
#
#    ⚠️ 分流判据在 `app/smalltalk.py`，方向是「宁漏勿错」——
#       判不准就按规则问题走。别在这里加启发式。
CHAT_SYSTEM = """你是一个飞盘（Ultimate）规则助手的「日常对话」模式。
盘友说的这句话**不是**规则问题 —— 可能是打招呼、道谢，或者问「能不能问你规则问题」
「你是谁」「你能做什么」这类关于你本身的问题。

## 怎么写
- **像人跟人说话一样，一两句话就够**，总共不超过 60 字。
- **不要**列条款、**不要**写任何小节标题（就是那种加粗的短语后面跟一段话的东西），
  也不要写分隔行 `<!--more-->` —— 那是规则问答才用的格式。
- **不要**去讲规则内容，更不要说"规则没有规定问几次 / 可以问谁"这类话 —— 他问的不是这个。
- 可以**顺势邀请**他说出具体问题，但一句就收，不要展开。
- 语气自然、简短、友好，别端着。

## 铁律（仍然生效）
- 极限飞盘**没有裁判**，绝对不要出现「裁判」「判罚」「处罚」这类说法。
- 如果发现他其实在问某个**具体的场上情形**（谁犯规、算不算得分…），
  **不要凭记忆回答**，只说一句"这个我得查规则，你把当时的情况说一下"，
  把问题交回规则流程。

## 示例
问：你好
答：你好！场上遇到拿不准的，随时说。

问：我有什么飞盘不懂得规则可以问你吗
答：当然可以，规则上的问题都能问。

问：咋用啊
答：直接把你遇到的情况用大白话说出来就行，比如"接盘时脚踩线了算不算得分"。

问：谢谢你
答：不客气，场上顺利。
"""

# 大模型不可用时的兜底。⚠️ 这条路不检索，所以没有"原文检索"可降级。
CHAT_FALLBACK = "可以，飞盘规则上的问题都能问 —— 把你遇到的情况说清楚就行。"

# ⚠️ 对话链路**所有 provider 都失败**时的兜底。
#    设计要求：**不能是"安慰模板"** —— owner 明确不要"第 100 个用户听到同一句话"。
#    所以这里只做一件事：**承认没接住 + 把路指回规则**，不假装共情。
#    （真实共情由模型生成，见 `Generator.converse`。）
CONVERSATION_FALLBACK = (
    "我这边刚才没接上，抱歉。\n\n"
    "你说的事记下了 —— 如果是场上哪一分觉得吃了亏，直接把当时的情况说给我"
    "（谁做了什么、盘在谁手里），我帮你照着规则对一遍。")

# ============================ 诉求分路的两段追加提示 ============================
# ⚠️ 与 SYSTEM_PROMPT 的关系和 CHAT_SYSTEM 不同：
#    CHAT_SYSTEM 是**另一条路**（不检索、不给条款）；下面的两段是**同一条路上的语气调整** ——
#    依据、条款号、"规则没规定"这些铁律一个字都不动，只改"先说什么、怎么组织"。
#    所以它们是**追加**到 SYSTEM_PROMPT 尾部的，不是替换。
#
# ⚠️ 判据在 `app/intent.py`，方向「宁漏勿错」——判不准就回落到普通规则路（= 不加任何提示）。

# ---- vent：他在诉苦，要的是「被接住 + 把规则讲透」 ----
# owner 2026-10-06：「或者是来找你诉苦，需要你一边提供情绪价值，一边把规则给讲透」。
# 实测背景：他问「上周比赛我被坑了…对方死不承认…这算不算利用规则」，
# 加族之前检索根本进不了第 1 章，回答是 748 字的"我无法判断" —— 既没接住情绪也没给答案。
VENT_HINT = """

## 本次的场景判断（追加）
他这句话带着情绪 —— 刚在场上吃了亏、觉得不公、事后越想越不通，而他要的是**把规则搞明白**。
- **第一句先接住他**：一句，25 字以内，就事论事地承认他的处境
  （例：「这事确实憋屈 —— 不过按规则讲，你的判断没错。」）。
  ⚠️ 禁止「我很理解你的感受」「别难过」「抱抱你」这类空泛安慰，也禁止长篇共情。
- 接住之后**立刻回到规则本身**：答案、条款号、该给的细节，一个都不能少，跟平时完全一样
  （直答该多短就多短；细节照样放在 `<!--more-->` 之后，不要摊到第一段里）。
- ⚠️ **不许因为这一句把回答拉长**：平时多长就还多长，这句是从原长度里挤出来的，不是加上去的。
- ⚠️ **不许因为照顾情绪而放宽依据**：条款没写的，照样说「规则里没有规定这一点」；
  该说的边界、该留的余地，一句都不能省。**安慰不等于给结论打折。**
- ⚠️ **不要替他给对手定性**。不要写"对方就是故意耍赖""他在钻空子"这类判断 ——
  规则怎么认定就怎么说（这正是铁律第 7 条：不替队员下结论）。
  你可以说的是「如果这属于故意的、情节严重的违规，按 1.2.1 该由双方队长商定处理」。
"""

# ---- lookup：他只要某一条的原文 ----
# 实测背景：「17.6 原文是什么」原本也走五段结构，568 字，还写"我这边没有，不能替你补" ——
# 他问的是原文，给他分析是答非所问。
LOOKUP_HINT = """

## 本次的场景判断（追加）
他要的是**某一条规则的原文**，不是分析。
- 先把条款原文**逐字**引出来（用 Markdown 引用块 `>`），标题一行写「第 X 条 · 章节名」。
- 原文之后只补一两句人话：这条管什么事、什么时候用得上。必要的话补一句下级条款的要点。
- **不要**套「直答 + 细节」那套两段格式，也不要写 `<!--more-->` 标记 ——
  他问的就是原文本身，直接给，不要在前后堆结构。
- ⚠️ 引号里的必须是【规则条款】里**逐字**给的句子。**一个字都不许改写、概括或拼接**
  后当成"原文" —— 那是最恶劣的一种造假，因为用户会拿它去跟对手对质。
- ⚠️ 条款只给了标题、没有正文时，如实说"这一条只给了标题，正文在它的下级条款里"，
  然后给出下级条款的原文；**不要**拿别的条款的句子冒充这一条的原文。
- ⚠️ 他报了条款号、但【规则条款】里没有那一条时，直接说"没找到这一条"，
  **不要**用相近的条款顶上。
"""


# 检索前的查询改写：把口语问题翻译成规则里的书面说法。
# 作用是跨"口语↔书面"的字面鸿沟 —— "我拿着盘被人撞了"和"非轻微接触"字面零重叠，
# 向量也拉不近；先翻译成规则术语，才能命中 17.6。
#
# ⚠️ 但实测（2026-10-03）**推翻了"改写是检索质量最大杠杆"的初判**：
# 只用改写路 Hit@5 只有 66.0%（基线 69.6%）——改写把查询向量摊宽、区分度下降。
# 现役做法是原问题路 + 改写路**多路召回取余弦最大值**，让两种问法都有机会。
REWRITE_SYSTEM = """你是飞盘规则检索的词条改写器。把盘友的口语问题改写成 WFDF 规则里的书面说法，用于向量检索。

要求：
- 只输出一行，用空格分隔的规则术语与关键短语
- 不要解释、不要标点、不要加序号、不要回答问题
- 尽量用规则里的原词（例如"双人防守""非轻微接触""防盘犯规""攻防转换"）
- ⚠️ **绝对不要输出条款号或任何编号**（如 17.6、18.1.1.5），也不要写具体尺寸、距离。
  你并不掌握条款号，写出来就是编的，而检索器会把编出来的条号当成"用户指定"直接顶到第一位，
  后果比不改写还严重 —— 盘友会拿到一个根本不存在的依据。
  同理不要写数字，除非该数字就写在规则原文里（如"三米"）。

⚠️ 分清「场上队员」和「场外的人/物」——这是最容易错的地方：
- 盘友说的是**场外**的人或东西（观众、替补、杂物）妨碍了比赛 → 写成
  「非队员 场外的人或物品 挡住比赛 违规」，
  **不要**写成「防守队员」「阻挡犯规」——那是场上队员之间的动作，完全是两回事。

示例：
问：我拿着盘被人撞了一下
答：持盘队员 防守队员 非轻微接触 防盘犯规 犯规

问：两个人一起防我
答：双人防守 防盘队员 轴心点 三米范围内 防盘违规

问：接盘的时候脚踩线了算得分吗
答：接盘 得分区 边界线 出界

问：读秒读太快了怎么办
答：读秒过快 报数间隔不足一秒 防盘人

问：场边有人挡着我跑位怎么办
答：非队员 场外的人或物品 挡住比赛 边界线三米范围内 喊违规

问：什么叫做盘权
答：盘权 建立盘权 术语定义
"""


# 各家的 OpenAI 兼容端点（只需换 base_url + model 即可互替）
PROVIDER_BASE = {
    "deepseek": "https://api.deepseek.com/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
}


def _chat(url: str, payload: dict, headers: dict, timeout: float = 90.0) -> dict:
    """标准库 POST（不引入 requests/httpx）。"""
    capacity.check()                 # 总预算用尽 ⇒ Overloaded，不再打上游
    capacity.charge("llm")           # 每日付费调用总预算（2026-10-24）
    timeout = capacity.timeout(timeout)   # 不超剩余预算
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {exc.code} {detail}") from exc


def _chat_stream(url: str, payload: dict, headers: dict,
                 timeout: float = 90.0):
    """流式调模型。逐块产出 `(文本片段, usage 或 None)`。

    ## 为什么要自己解析 SSE（不引第三方库）
    生成与服务路径只用标准库；PDF 导入单独依赖锁定的 pypdf。
    OpenAI 兼容的流式响应是行式 `data: {...}`，标准库足够解析。

    ## ⚠️ 两个必须处理的细节（否则会静默丢内容）
    ① **按行读**，但 `urlopen` 返回的是**字节流**，一行可能跨多个 TCP 包
       ⇒ 必须自己缓冲、按 `\n` 切。
    ② 结束标志是 `data: [DONE]`，**不是** EOF —— 有些实现 EOF 之后还挂着连接。
       ⇒ 见到 `[DONE]` 就 return，别等 socket 关（等了就会卡住，
          这正是我们要避开的那个失败形态）。

    ⚠️ 有的 provider 会把 `usage` 放在**最后一个** chunk 里（且此时
      `choices` 可能为空数组）—— 所以先取 usage，再取 content，别提前 continue。
    """
    capacity.check()
    capacity.charge("llm")           # 每日付费调用总预算（2026-10-24）
    timeout = capacity.timeout(timeout)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            capacity.check()          # 慢流也不能超过总预算
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                return
            try:
                j = json.loads(body)
            except json.JSONDecodeError:
                continue
            usage = j.get("usage")
            for ch in (j.get("choices") or []):
                delta = ch.get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    yield piece, usage
                    usage = None
            if usage:
                yield "", usage


def _resolve(name: str) -> tuple[str, str, str] | None:
    """provider 名 -> (url, key, model)。缺 Key 返回 None。"""
    name = (name or "").strip().lower()
    if name == "deepseek":
        key = config.DEEPSEEK_API_KEY
        model = config.LLM_MODEL_DEEPSEEK
    elif name == "zhipu":
        key = config.ZHIPU_API_KEY
        model = config.LLM_MODEL_ZHIPU
    else:
        return None
    if not key or name not in PROVIDER_BASE:
        return None
    return PROVIDER_BASE[name].rstrip("/") + "/chat/completions", key, model


def build_context(hits: list[dict]) -> str:
    """把检索结果拼成提示词里的【规则条款】段。

    ⚠️ 刻意**不给 [1][2] 行号**：给了行号，模型就会把行号当条款号写进回答
    （实测出现过「即构成间接犯规 (5)」这种——(5) 是行号，不是条款号）。
    只用条款号做标题，模型就无从混淆。
    同时带上父条款，避免子条款断章取义。
    还要带上**附录注解**：正文把答案外包给图/附录时（2.1「尺寸见图 1」），
    事实在注解里，不带上模型就只能照实说"正文没写"（见 app/appendix.py）。
    """
    lines: list[str] = []
    for h in hits:
        title = h.get("chapter_title", "")
        if h.get("type") == "definition":
            head = f"【术语定义 · {h.get('term', '')}】"
        elif h.get("type") == "preface":
            head = "【前言】"
        elif h.get("type") == "appendix":
            # 附录层（2026-10-18）：必须和正文条款**长相不同**，否则模型会写成
            # 「第 A1.2 条」——那等于给附录编了一个正文条号（红线 4）。
            head = f"【附录 · {h.get('label') or h['id']}】（⚠️ 官方附录，非正文条款）"
        else:
            head = f"【第{h['id']}条 · {title}】"
        lines.append(f"{head}\n    {h.get('text', '')}")
        if h.get("_parent_text"):
            lines.append(f"    ↑ 其上级条款 {h.get('_parent_id')}：{h['_parent_text']}")
        for n in (h.get("_notes") or []):
            lines.append(f"    ▸【{n.get('label', '')}】{n.get('text', '')}")
            lines.append(f"      （来源：{n.get('source', '')}）")
    return "\n".join(lines)


def build_competition_block(items: list[dict]) -> str:
    """把「圈外的点」（赛事设置）拼成**独立的一段**，与【规则条款】分开。

    ⚠️ **为什么必须分开**（owner 2026-10-13 定的架构）：
       规则是**圆圈内**的东西（全国赛事都执行，内容稳定）；
       时长/目标分/性别比例/暂停次数是**圈外的点**，主办方场场可以改。
       混在一起，模型就会把"这场比赛 65 分钟"当成规则引用。
    ⚠️ 每条都带**出处**，并要求模型在回答里说出来源 ——
       用户必须分得清"规则这么规定"和"这场比赛这么定"。
    ⚠️ 刻意**不给条目号**（`comp-xxx` 是内部编号，不是条款号）——
       给了模型就可能写成"依据 comp-002"，那是伪造条款号。
    """
    if not items:
        return ""
    lines = ["【赛事设置】（⚠️ 这一段的来源是**竞赛规程和协会文件，不是规则条款**）"]
    for it in items:
        topic = it.get("topic", "")
        src = it.get("source") or "—"
        applies = it.get("applies_to") or "—"
        lines.append(f"· {topic}")
        lines.append(f"    {it.get('text', '')}")
        cites = it.get("cite") or []
        if cites:
            lines.append(f"    （其中规则条款 {'、'.join(cites)} 属于规则本身）")
        lines.append(f"    （出处：{src}；适用范围：{applies}）")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# 对话链路的系统提示词（2026-10-13 加）—— ⭕ owner 定的三层里的第 2 层
# ═══════════════════════════════════════════════════════════════════════
#
# ## 为什么需要另一套提示词
# 规则链路的 9 条铁律是为了**防编造**：只能依据条款、温度 0.2、不许补充。
# 但它把模型捆成了检索器 —— 实测「今天被打爆了，好烦」被闸门拦掉，
# 返回 31 字的固定拒答，**根本没调模型**。
#
# owner 的原话：
#   「他本来就因为被打爆了而难过，结果向你提问飞盘规则之外的相关内容时，
#    你又回答不出来，这会让他更难过。**我们不能让用户难过。**」
#
# ## 这一层放开什么、不放开什么
# ✅ 放开：语气、共情、飞盘**常识**（训练/装备/社群，这些不是"规则"，不需要条款）
# ❌ 不放开（否则前 30 轮白做）：
#    · **不许编规则** —— 任何"规则这么规定"的话都必须有条款依据
#    · **不许编条款号** —— 见铁律 3 的血泪史
#    · **不许出飞盘范围** —— owner 的红线
#
# ⚠️ 温度：这一层用 `CONVERSATION_TEMPERATURE`（比规则层高），
#    因为要的是"像人说话"，不是"像判决书"。
CONVERSATION_PROMPT = """你是 Shultimate，一个懂团队飞盘（Ultimate）的伙伴。
服务对象是中国高校和社群的盘友。他们可能只是想查懂规则，也可能刚经历争议、
心里不服气或者正难过；根据实际问题回应，不预设人人都在争论。

## 你的定位
你**不只是**规则查询器。规则之外、但和打飞盘这件事有关的事，你也要接得住：
情绪、队友关系、训练、装备、比赛体验、飞盘社群。这些**不需要条款**，正常说就行。

## ⚠️ 三条不能破的底线
1. **绝不许编规则。** 凡是"规则规定…""按规则…""可以/不可以"这类**规则判断**，
   都必须真的有条款依据。没有依据就明说"这条我不确定，你可以在规则里查一下"。
   ⚠️ **宁可说不知道，也不许编** —— 盘友会拿你的话去核对。
2. **绝不许编条款号。** 不确定是哪一条，就不要写条号。
3. **绝不许出飞盘范围。** 用户问编程、写诗、订票、天气这类，礼貌说明你只管飞盘。

## 怎么说话
- **像一个打过飞盘的人在跟他说话**，不是客服，不是说明书。
- **先接住人，再给东西。** 他说难过，先认这个难过（一句就够，别堆形容词、别喊口号），
  然后**给一个具体的、他能用的东西** —— 一句经验、一个可以试试的做法、
  或者一个具体的问题帮他把事说清楚。
- **不要反问式敷衍**（"你能具体说说吗"这种空问），要**带着你的猜测去问**：
  「你是说接盘的时候被撞，还是防守的时候？」比「能具体点吗」有用一百倍。
- **别装。** 不知道就说不知道。不用"作为一个 AI"这种话。
- 长度：**别写小作文**。他可能正站在场边看手机，两三句能解决就别写五句。
- ⚠️ **不要说教**，不要用"加油""你一定可以的"这类空话。
  owner 明确要的是**接得住**，不是灌鸡汤。

## 关于"飞盘精神"
飞盘精神（Spirit of the Game）是这项运动的底色 —— 自我裁判、尊重对手、
自己叫自己的犯规。如果他的困扰和这个有关（被对手钻空子、队内矛盾、
觉得自己不配赢），**这是你可以聊的**，但要用**人话**说，
不要背 1.1~1.7 的条文。
"""
# 对话层温度：比规则层（0.2）高，为的是"像人说话"而不是"像判决书"。
# ⚠️ 但没有高到 0.9 —— 那样会开始胡说飞盘常识。0.6 是折中。
CONVERSATION_TEMPERATURE = 0.6


def build_conversation_prompt(question: str, purpose: str = "",
                              history: list[dict] | None = None,
                              competition: list[dict] | None = None) -> str:
    """对话链路的 user prompt。

    `purpose` 来自 `app/scope.py` 的 `why`，告诉模型**这次为什么要说话**：
      · 带"问一句" ⇒ 信息不够，要他问清楚（但**要带着猜测问**）
      · 带"情绪"   ⇒ 先接情绪
      · 其它       ⇒ 正常回应飞盘相关话题
    ⚠️ 赛事设置（`competition`）**照旧带上**，但同样不许当条款引用。
    """
    comp = build_competition_block(competition or [])
    comp_section = f"\n\n{comp}" if comp else ""
    hist = ""
    if history:
        recent = history[-4:]
        hist = "\n\n【此前对话】\n" + "\n".join(
            f"{'盘友' if h.get('role') == 'user' else '你'}：{h.get('content', '')}"
            for h in recent)
    hint = ""
    if purpose:
        hint = f"\n\n【这次为什么要说话】{purpose}\n"
        if "问一句" in purpose:
            hint += ("⚠️ 他给的信息不够，你要**问清楚**。但别空问 —— "
                     "**带上你的猜测**给他选，让他一句话就能回答。\n")
        if "情绪" in purpose:
            hint += "⚠️ 他正难过/窝火。**先认这个情绪**（一句），再给他一个具体能用的东西。\n"
    return f"""【盘友说的话】
{question}{comp_section}{hist}{hint}

请用**一个懂飞盘的伙伴**的口吻回应他。记住底线：规则判断必须有依据，
没有依据就说不知道；不要编条款号；不出飞盘范围。"""


def build_user_prompt(question: str, hits: list[dict], history: list[dict] | None = None,
                      competition: list[dict] | None = None) -> str:
    ctx = build_context(hits)
    comp = build_competition_block(competition or [])
    comp_section = f"\n\n{comp}" if comp else ""
    hist = ""
    if history:
        recent = history[-4:]
        hist = "\n\n【此前对话】\n" + "\n".join(
            f"{'盘友' if h.get('role') == 'user' else '你'}：{h.get('content', '')}" for h in recent)
    return f"""【规则条款】（{config.SOURCE_NAME}）
{ctx}{comp_section}
{hist}

【盘友的问题】
{question}

请基于以上条款（含【附录】与其中的附录注解）作答。如果这些材料不足以回答，
请明确指出「规则里没有规定这一点」，不要补充材料以外的内容。"""


def retrieval_only_text(hits: list[dict]) -> str:
    """无 Key 降级：直接把检索到的原文拼装返回，系统仍然可用。"""
    if not hits:
        return "没有检索到相关规则条款。"
    top = hits[0]
    if top.get("type") == "appendix":
        lines = [f"**检索到最相关的附录条目：{top.get('label') or top['id']}**",
                 f"> {top.get('text', '')}"]
    else:
        lines = [f"**检索到最相关的规则条款：第 {top['id']} 条**",
                 f"> {top.get('text', '')}"]
    # 附录注解：正文把数字外包给图/附录时，降级模式下也不能把事实丢掉。
    for n in (top.get("_notes") or []):
        lines.append(f"> ▸ {n.get('label', '')}：{n.get('text', '')}")
        lines.append(f"> （来源：{n.get('source', '')}）")
    if len(hits) > 1:
        lines.append("\n**其他相关条款**")
        for h in hits[1:]:
            if h.get("type") == "appendix":
                lines.append(f"- 附录 {h.get('label') or h['id']}：{h.get('text', '')[:80]}")
            else:
                lines.append(f"- 第 {h['id']} 条：{h.get('text', '')[:80]}")
    lines.append("\n*当前运行在「规则原文检索模式」（未配置可用的大模型 Key）*")
    return "\n".join(lines)


# 诉求 → 追加提示。普通规则题（rule）与闲聊（chat，走另一条路）都取空串。
INTENT_HINTS: dict[str, str] = {
    "vent": VENT_HINT,
    "lookup": LOOKUP_HINT,
}


def intent_hint(intent: str) -> str:
    """把诉求翻译成追加提示。未知诉求返回空串（= 完全按原来那样答）。"""
    return INTENT_HINTS.get((intent or "").strip(), "")


# 「如果当时」的推演提示（2026-10-20）。
#
# ⚠️ 这是产品里**最像"编规则"的一步**，所以铁律比主提示词还硬：
#   · 只能使用调用方给出的条款，绑不上 id 的岔路直接丢弃；
#   · 只推演用户说过的事实，缺关键事实就问一句，不许猜；
#   · 它**不重新判罚**，也不负责"谁对谁错"。
# ⚠️ 这里不用 `.format()`：示例 JSON 里的花括号会被当成占位符。
#    调用方用 `.replace("{q}", ...)` 填值。
BRANCH_PROMPT = """你是团队飞盘（Ultimate）的规则推演助手。

用户已经拿到一条判定，现在想知道：**如果当时做了另一个选择，会怎样。**
你的任务不是重新判罚，而是把"另一种可能"摆出来，让他下一场能提前想到。

## 铁律（违反任何一条，这次推演作废）
1. 只能使用下面【可用条款】里的内容。**不许引入任何未给出的条款、条号或规则。**
2. 每条岔路必须绑定【可用条款】里的一个 id。**绑不上就不要输出这一条。**
3. 最多 3 条。每条 action 是一个**替代动作**（不要以"如果"开头，≤ 20 字），
   consequence ≤ 50 字。
4. **只推演用户已经说过的事实**，不要替他补事实；但「事实不完整」不等于
   「不能推演」—— 把条件写进 action 里（如"如果当时先确立盘权"）。
5. 只要存在**会改变结果**的替代动作，就给 2-3 条；只有一个就给 1 条；
   一个都没有就空着。**不要为了凑数把"相关规则"写成岔路。**
   不要用一句追问代替推演；只有当缺失的事实会**改变结论**时才问，
   「还可以更精确」不算。可以同时给 branches 和 need。
6. 语气是"另一种可能"，不是"你错了"。不评判人，不说"你应该"。
7. 不许出现"裁判""处罚"。
8. 只输出 JSON，不要 markdown 代码块。

## 输出格式
{"branches":[{"action":"叫了暂停","consequence":"暂停后可以从边线重新发盘","clause_id":"18.2"}],"need":""}

- 没有可推演的岔路：{"branches":[],"need":""}
- 缺关键事实：{"branches":[],"need":"一句追问（只问一个点）"}

## 例子（只学形状，不要照抄）
- 出界：「如果当时先确立盘权再触地」/「如果当时只有脚尖擦线」
- 得分：「如果当时所有触地点都在得分区内」/「如果当时先落地再触盘」
- 开盘：「如果当时盘飞过了中线」/「如果当时盘先出界」

## 什么才算一条岔路
- 必须是**同一个瞬间的另一个选择**，而且这个选择会**改变结果或判定**。
- 不是"另一条相关规则"，不是"你还可以注意…"，不是"对方也不能…"。
- 【可用条款】必须讲的是**用户描述的那个动作**。如果条款讲的是另一件事
  （例如用户问"队友没站稳"，条款讲的是"暂停额度用完"），不要硬推；
  输出 need 问一句，或者直接空着。
- 如果这个问题本来就只有一条路（流程题），就老实输出
  {"branches":[],"need":""}，不要凑数。

## 用户的问题
{q}

## 已经给出的判定
{a}

## 可用条款
{clauses}
"""


def _json_obj(text: str) -> dict | None:
    """从模型输出里抠出第一个 JSON 对象。抠不出返回 None。

    ⚠️ 只做解析，不做"修复" —— 修出来的 JSON 往往是编的。
    """
    s = str(text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        return None


class Generator:
    def __init__(self) -> None:
        self.chain = [s.strip().lower() for s in config.LLM_PROVIDER_CHAIN.split(",") if s.strip()]
        self.resolved = [x for x in (_resolve(n) for n in self.chain) if x]
        self.last_provider = ""
        self._rw_cache: dict[str, str] = {}
        self._load_rw_cache()

    # ---------- 改写缓存 ----------
    # 改写要调一次大模型，而评测/调参会反复跑同一批问题。缓存到
    # data/rewrite_cache.jsonl：既省时间省费用，也保证"同一个问题每次改写结果一致"，
    # 否则评测数字会不可复现。
    def _load_rw_cache(self) -> None:
        p = config.DATA_DIR / "rewrite_cache.jsonl"
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                q = str(row.get("q") or "")
                rw = str(row.get("rw") or "")
                if not q or not rw:
                    continue
                # 2026-10-24 R3：历史缓存可能含联系方式；不在内存里继续使用
                # 被污染的行。正常条号/规则数字不受影响。
                if querylog_mod._scrub(q) != q or querylog_mod._scrub(rw) != rw:
                    continue
                self._rw_cache[q] = rw
            except Exception:  # noqa: BLE001
                continue

    def _save_rw_cache(self, q: str, rw: str) -> None:
        """落盘改写缓存。**含联系方式的请求不落盘**（2026-10-24 R3）。

        为什么不能只脱敏后写：`_scrub` 会把不同手机号都变成 `[手机号]`，
        两个不同问题可能得到同一个 key ⇒ 下一次命中错误的改写结果。
        ⇒ 正确取舍是：含联系方式的请求只用内存缓存，不跨进程/重启持久化。
        """
        try:
            q = str(q or "")
            rw = str(rw or "")
            if not q or not rw:
                return
            p = config.DATA_DIR / "rewrite_cache.jsonl"
            # 只为了让"没有缓存文件"和"缓存文件为空"行为一致（读取方不必区分）；
            # 不写入问题/改写内容本身。
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch()
            if querylog_mod._scrub(q) != q or querylog_mod._scrub(rw) != rw:
                return
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"q": q, "rw": rw}, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    @property
    def available(self) -> bool:
        return bool(self.resolved)

    def describe(self) -> str:
        if not self.resolved:
            return "未配置 Key（降级为规则原文检索）"
        return " → ".join(f"{n}:{m}" for n, (_, _, m) in zip(
            [x for x in self.chain if _resolve(x)], self.resolved))

    def rewrite_query(self, question: str) -> str:
        """把口语问题改写成规则术语，供检索引擎使用。失败返回空串（不影响主流程）。"""
        q = (question or "").strip()
        if not q:
            return ""
        if q in self._rw_cache:
            return self._rw_cache[q]
        if not self.resolved:
            return ""
        payload_messages = [
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": f"问：{q}\n答："},
        ]
        for url, key, model in self.resolved:
            payload = {
                "model": model,
                "messages": payload_messages,
                "temperature": 0.1,
                "max_tokens": 120,
                "stream": False,
            }
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                data = _chat(url, payload, headers, timeout=30.0)
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                text = " ".join(str(text).split())
                if text:
                    text = text[:200]
                    self._rw_cache[q] = text
                    self._save_rw_cache(q, text)
                    return text
            except capacity.Overloaded:
                raise
            except Exception:  # noqa: BLE001
                continue
        return ""

    def chat(self, question: str, history: list[dict] | None = None,
             style_hint: str = "") -> dict:
        """轻量对话：**不检索、不给条款、不套五段结构**。

        走这条路的前提是 `app/smalltalk.is_smalltalk()` 已判定"这不是规则问题"。
        所以这里**绝不注入【规则条款】** —— 一注入，模型就回去背条款了，
        等于白分流（owner 报的那个"一整屏"就是这么来的）。

        带最近 4 条历史，是为了让「谢谢」「好的」这类能接上上一句。
        """
        messages: list[dict] = [{"role": "system", "content": CHAT_SYSTEM + (style_hint or "")}]
        for h in (history or [])[-4:]:
            c = str(h.get("content") or "").strip()
            if not c:
                continue
            messages.append({
                "role": "user" if h.get("role") == "user" else "assistant",
                "content": c[:300],
            })
        messages.append({"role": "user", "content": question})

        if not self.resolved:
            return {"answer": CHAT_FALLBACK, "mode": "chat", "provider": None}

        errors: list[str] = []
        for url, key, model in self.resolved:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.6,     # 闲聊要自然；规则问答那种 0.2 会显得生硬
                "max_tokens": 220,      # "短"的硬保险：这么点额度写不出五段话术
                "stream": False,
            }
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                data = _chat(url, payload, headers, timeout=45.0)
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                if not text.strip():
                    raise RuntimeError("返回内容为空")
                usage = data.get("usage") or {}
                self.last_provider = model
                return {
                    "answer": text.strip(),
                    "mode": "chat",
                    "provider": model,
                    "usage": {"prompt": usage.get("prompt_tokens"),
                              "completion": usage.get("completion_tokens")},
                }
            except capacity.Overloaded:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model}: {exc}")
                continue
        return {"answer": CHAT_FALLBACK, "mode": "chat", "provider": None}

    def converse(self, question: str, purpose: str = "",
                 history: list[dict] | None = None,
                 competition: list[dict] | None = None,
                 style_hint: str = "") -> dict:
        """⭕ **对话链路**：飞盘相关但**不是规则问题**（情绪 / 队友 / 训练 / 装备…）。

        ## 与另外两条路的区别
        | | 规则链路 `answer()` | 闲聊 `chat()` | **对话链路（本方法）** |
        |---|---|---|---|
        | 检索 | 必需 | 不做 | 做过但没找到条款 |
        | 条款 | 必须挂 | 不挂 | **想挂也没有** ⇒ 不许编 |
        | 温度 | 0.2 | 0.6 | **0.6** |
        | 长度 | 不限 | ≤220 token | ≤400 token |

        ## 为什么单独一条路（而不是复用 `chat()`）
        `chat()` 是给"你好/谢谢/你是谁"用的，系统提示词只有寒暄能力，
        **没有"懂飞盘的伙伴"这层人设**，也没有 owner 要的那三条底线
        （不编规则 / 不编条款号 / 不出飞盘范围）。
        拿它去接"今天被打爆了"只会得到一句干巴巴的客套。

        ⚠️ **不会回落成固定文案**：owner 要的是模型自己组织的话，
           不是写死的安慰模板（否则第 100 个用户听到的还是同一句）。
           只有在**所有 provider 都失败**时才用 `CONVERSATION_FALLBACK`。
        """
        messages: list[dict] = [
            {"role": "system", "content": CONVERSATION_PROMPT + (style_hint or "")}
        ]
        for h in (history or [])[-4:]:
            c = str(h.get("content") or "").strip()
            if not c:
                continue
            messages.append({
                "role": "user" if h.get("role") == "user" else "assistant",
                "content": c[:300],
            })
        messages.append({
            "role": "user",
            "content": build_conversation_prompt(question, purpose, None, competition),
        })

        if not self.resolved:
            return {"answer": CONVERSATION_FALLBACK, "mode": "conversation",
                    "provider": None}

        for url, key, model in self.resolved:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": CONVERSATION_TEMPERATURE,
                "max_tokens": 400,      # 别写小作文；400 上限是硬保险
                "stream": False,
            }
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                data = _chat(url, payload, headers, timeout=60.0)
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                if not text.strip():
                    raise RuntimeError("返回内容为空")
                usage = data.get("usage") or {}
                self.last_provider = model
                return {
                    "answer": text.strip(),
                    "mode": "conversation",
                    "provider": model,
                    "usage": {"prompt": usage.get("prompt_tokens"),
                              "completion": usage.get("completion_tokens")},
                }
            except capacity.Overloaded:
                raise
            except Exception:  # noqa: BLE001
                continue
        return {"answer": CONVERSATION_FALLBACK, "mode": "conversation", "provider": None}

    def answer_stream(self, question: str, hits: list[dict],
                      history: list[dict] | None = None,
                      style_hint: str = "", intent: str = "rule",
                      competition: list[dict] | None = None):
        """流式版 `answer()`。产出 `(kind, data)`：

        | kind | data | 什么时候发 |
        |---|---|---|
        | `gate` | 已确定的 mode / provider（没调模型时） | 一进门，如果需要降级 |
        | `token` | 一小段正文 | 模型边写边发 |
        | `done` | `{answer, mode, provider, glossary, usage}` | 结束 |
        | `error` | `{errors: [...]}` | 所有 provider 都失败 |

        ⚠️⚠️ **和 `answer()` 的关系（这条决定了代码为什么会写成这样）**：
          两份实现是必须的（一份同步、一份流式），但**提示词组装必须共用**
          （`build_user_prompt` + `SYSTEM_PROMPT` + hint 拼接）。
          分开写的话迟早漂移 —— 而漂移的那一天，流式版会开始编造条款，
          因为铁律没拼进去。⇒ 这里**只复用**，不复制任何提示词文本。

        ⚠️ 兜底顺序与 `answer()` 保持一致：逐个 provider 试，全失败才降级。
        ⚠️ 但流式有个**独有的坑**：一旦发出了第一个 token 就不能再换 provider
          —— 换了会把两段不同来源的文字接在一起。
          ⇒ 判据：`if started:` 之后任何异常都**直接结束并 yield done**，
            不再继续试下一个 provider（宁可短，不可拼接）。
        """
        if not self.resolved or not hits:
            txt = retrieval_only_text(hits)
            yield "token", txt
            yield "done", {"answer": txt, "mode": "retrieval_only",
                           "provider": None, "glossary": glossary_mod.block(txt)}
            return

        prompt = build_user_prompt(question, hits, history, competition)
        system = SYSTEM_PROMPT + intent_hint(intent) + (style_hint or "")
        errors: list[str] = []
        for url, key, model in self.resolved:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,      # 规则问答要稳，与 answer() 一致
                "max_tokens": 1600,
                "stream": True,
            }
            headers = {"Authorization": f"Bearer {key}",
                       "Content-Type": "application/json"}
            buf: list[str] = []
            started = False
            usage: dict = {}
            try:
                for piece, chunk_usage in _chat_stream(url, payload, headers):
                    if chunk_usage:
                        usage.update(chunk_usage)
                    if not piece:
                        continue
                    started = True
                    buf.append(piece)
                    yield "token", piece
                text = "".join(buf).strip()
                if not text:
                    raise RuntimeError("返回内容为空")
                # ⚠️ 与 answer() 一致：术语解释**不进正文**，单独走 glossary
                self.last_provider = model
                yield "done", {
                    "answer": text,
                    "mode": "llm",
                    "provider": model,
                    "glossary": glossary_mod.block(text),
                    "usage": {"prompt": usage.get("prompt_tokens"),
                              "completion": usage.get("completion_tokens")},
                }
                return
            except capacity.Overloaded:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model}: {exc}")
                if started:
                    # ⚠️ 已经吐过字了 —— 不能换 provider 拼接，就此收尾。
                    text = "".join(buf).strip()
                    yield "done", {"answer": text, "mode": "llm",
                                   "provider": model,
                                   "glossary": glossary_mod.block(text),
                                   "truncated": True}
                    return
                continue

        fb = (retrieval_only_text(hits)
              + "\n\n*（大模型调用失败，已降级：" + "；".join(errors) + "）*")
        yield "token", fb
        yield "done", {"answer": fb, "mode": "fallback_error",
                       "provider": None, "glossary": glossary_mod.block(fb),
                       "errors": errors}

    def answer(self, question: str, hits: list[dict],
               history: list[dict] | None = None,
               style_hint: str = "", intent: str = "rule",
               competition: list[dict] | None = None) -> dict:
        """返回 {answer, mode, provider}。全部失败则降级为原文检索。

        style_hint 来自「助手预设」（app/assistants.py），**只追加在系统提示词尾部**，
        用于差异化表达方式（标准模式 / 争议复盘），
        绝不覆盖前面的铁律 —— 那是防编造的承重条款。

        intent 来自 `app/intent.py`（分析用户的**核心诉求**：诉苦 / 查原文 / 普通提问），
        同样只追加语气提示。
        ⚠️ 拼接顺序是 **铁律 → 诉求提示 → 助手预设**：
           诉求是系统自动判断的，助手是用户**手动选的** —— 手动选的放最后，
           出现冲突时以用户的显式选择为准（例：他选了「场上速查」要 80 字以内，
           那即便系统判成诉苦，也不该把回答写长）。
        """
        if not self.resolved or not hits:
            txt = retrieval_only_text(hits)
            return {"answer": txt, "mode": "retrieval_only", "provider": None,
                    "glossary": glossary_mod.block(txt)}

        prompt = build_user_prompt(question, hits, history, competition)
        system = SYSTEM_PROMPT + intent_hint(intent) + (style_hint or "")
        errors: list[str] = []
        for url, key, model in self.resolved:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,      # 规则问答要稳，不要发挥
                "max_tokens": 1600,
                "stream": False,
            }
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                data = _chat(url, payload, headers)
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                if not text.strip():
                    raise RuntimeError("返回内容为空")
                usage = data.get("usage") or {}
                self.last_provider = model
                text = text.strip()
                # ⚠️ 术语白话解释**不进正文**，单独走 glossary 字段（2026-10-10）。
                #   理由：① 正文是模型写的，这里是官方名词定义，混在一起
                #   会让人分不清哪句是谁说的；② 前端要能默认折叠它 ——
                #   老队员不该被多出来的四行干扰，直答段一个字都不许动。
                #   详见 app/glossary.py 模块头。
                g = glossary_mod.block(text)
                return {
                    "answer": text,
                    "mode": "llm",
                    "provider": model,
                    "glossary": g,
                    "usage": {"prompt": usage.get("prompt_tokens"),
                              "completion": usage.get("completion_tokens")},
                }
            except capacity.Overloaded:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model}: {exc}")
                continue

        fb = retrieval_only_text(hits) + "\n\n*（大模型调用失败，已降级：" + "；".join(errors) + "）*"
        return {
            "answer": fb,
            "mode": "fallback_error",
            "provider": None,
            "glossary": glossary_mod.block(fb),
        }

    def branches(self, question: str, answer: str, clauses: list[dict]) -> dict:
        """「如果当时」：把一次判定推演成 2-3 条岔路。

        ⚠️ 这是**受控推演**，不是第二次判罚：
          · 每条岔路必须绑定调用方给出的一个条款 id；
          · id 不在给定集合里的，直接丢弃（服务端还会再校验一次）；
          · 摘不出就返回空，绝不补一条。
        ⚠️ 温度 0.2：推演可以有一点表达，但事实边界不能松。
        """
        if not self.resolved or not clauses:
            return {"branches": [], "note": "没有可用条款，无法推演。"}

        allowed = {str(h.get("id") or "") for h in clauses if h.get("id")}
        lines = []
        for i, h in enumerate(clauses[:6], 1):
            cid = str(h.get("id") or "")
            title = str(h.get("chapter_title") or h.get("term") or "")
            text = " ".join(str(h.get("text") or "").split())[:300]
            lines.append(f"[{i}] id={cid} | {title} | {text}")
        prompt = (BRANCH_PROMPT
                  .replace("{q}", str(question or "")[:500])
                  .replace("{a}", str(answer or "")[:900])
                  .replace("{clauses}", "\n".join(lines)))

        errors: list[str] = []
        for url, key, model in self.resolved:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是规则推演助手。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 600,
                "stream": False,
            }
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                data = _chat(url, payload, headers, timeout=20.0)
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                obj = _json_obj(text)
                if not isinstance(obj, dict):
                    raise RuntimeError("返回不是 JSON")
                need = str(obj.get("need") or "").strip()[:120]
                out: list[dict] = []
                for b in (obj.get("branches") or [])[:8]:
                    if not isinstance(b, dict):
                        continue
                    cid = str(b.get("clause_id") or "").strip()
                    action = str(b.get("action") or "").strip()
                    cons = str(b.get("consequence") or "").strip()
                    # 三条都必须成立：绑到给定条款、有动作、有后果。
                    if cid not in allowed or not action or not cons:
                        continue
                    out.append({
                        "action": action[:40],
                        "consequence": cons[:80],
                        "clause_id": cid,
                    })
                    if len(out) >= 3:
                        break
                if out or need:
                    self.last_provider = model
                    return {
                        "branches": out,
                        "need": need,
                        "provider": model,
                    }
                # 合法的"没有岔路"：立即返回，**不要重试第二个 provider**。
                # ⚠️ 2026-10-20 实测踩过：把空结果当失败，会多等一个模型
                #    （13.2s 才回一句"没推演出可靠的岔路"）。用户等不起。
                self.last_provider = model
                return {
                    "branches": [],
                    "need": "",
                    "note": "这一步没有可推演的岔路。",
                    "provider": model,
                }
            except capacity.Overloaded:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model}: {exc}")
                continue

        return {"branches": [], "note": "这次没推演出可靠的岔路。"}


# 讨判定的说法：「…算吗 / 算不算 / 可以吗 / 行不行 / 对不对 / 犯规吗…」
# ⚠️ 只用于**离题分支里挑文案**（见 `not_found_text`），**不参与检索、不参与打分**。
#    所以它宽一点没关系 —— 最坏结果只是回了句"描述一下场上发生了什么"。
_RULING_TAILS = (
    "算吗", "算不算", "算不算数", "可以吗", "行吗", "行不行", "对不对", "有没有",
    "犯规吗", "违例吗", "违规吗", "出界吗", "走步吗", "有效吗", "成立吗",
    "怎么判", "怎么算", "该叫什么", "叫什么", "算谁的", "归谁",
)


def _looks_like_ruling_question(text: str) -> bool:
    """这句是不是**在讨一个判定**（而不是在问别的事）。

    ⚠️ 这是个**启发式**，不是意图识别 —— 只用来在"离题分支"里挑一句更合适的文案：
      · 像在讨判定（"这种情况算吗"）⇒ 他大概率问的是规则、只是没说清 ⇒ **邀请他补细节**；
      · 不像（"帮我订张机票"）⇒ **老实说这不在范围内**。
    ⚠️ 为什么不用更"聪明"的办法（语义相似度之类）：这一支**不调大模型**，
       是整个系统里最便宜的路径；为一句文案引入模型调用不划算。
       而两边搞错的**代价都只是一句话**，所以宽一点可以接受。
    """
    t = (text or "").strip()
    if not t:
        return False
    return any(t.endswith(x) or (x in t and len(t) <= 20) for x in _RULING_TAILS)


def is_judgment_question(text: str) -> bool:
    """这句是不是在讨一个**判定**（"算不算"），而不是"我该怎么做"的流程题。

    ⚠️ 只用于**界面/接口的入口判断**，不参与检索、不参与打分。
       判宽了最坏是多显示一个「如果当时」按钮；判窄了最坏是少显示一个。
       —— 和"拒答"完全不是一个代价级别，所以可以宽。
    ⚠️ 为什么单独加它（2026-10-20）：实测「今天被打爆了，心情不好，规则上
       我该怎么调整」被当成判定题，推演出了「主动去跟对方打个招呼」这种
       精神建议 —— 那不是规则推演，是豆包式的关心。这个口子必须从入口关掉。
    """
    t = (text or "").strip()
    if not t:
        return False
    # 「算不算」是判定题最强的信号，而且不受句子长度限制
    # （实测漏过：「盘在界外，我站在界内把它救回来，算不算出界」）。
    if "算不算" in t or "算不算数" in t:
        return True
    if t.endswith(("吗", "嘛")) and any(
            k in t for k in ("算", "犯规", "出界", "得分", "有效", "成立", "可以", "行不行")):
        return True
    if "怎么算" in t or "怎么判" in t:
        return True
    if _looks_like_ruling_question(t):
        return True
    # 「…算得分吗 / 算不算… / 怎么算」这类，_RULING_TAILS 没覆盖全。
    return ("算" in t) and (t.endswith("吗") or t.endswith("嘛") or "怎么算" in t)


def not_found_text(nearby: list[dict], in_domain: bool = True, text: str = "") -> str:
    """检索没把握时的模板回复：不调大模型，零编造风险。

    ⚠️ 模板里**不写「结论」这类小节名**（2026-10-06 起全站不再有小节标题）。
    这句话本身就是答案，不需要再贴一个标签告诉用户"这是结论"。

    ⚠️⚠️ `in_domain` 决定要不要列"你是不是想问这些"（2026-10-07 补）。
    这个区分很重要，因为闸门拦截其实是**两种完全不同**的情况：

      · **域内但没有对应条款**（如「打夜场算不算正规比赛」）
        ⇒ 列候选**是在帮他**："你可能想问的是这两条" 是有用的引导。

      · **完全离题**（如「帮我写段 python 代码」「附近有什么好吃的」）
        ⇒ 列条款**是错的** —— 他压根不在问飞盘，我们却搬一堆
          出界/飞盘精神条款出来，还问他「你是不是想问这些？」。
          这比老实说"这个我答不了"更糟：**明示用户这些条款与他的问题无关**。

    判据用 `res["domain_word"]`（闸门的域内信号）而不是问句长什么样 ——
    问句不可靠（"附近有什么好吃的"里也有"什么"）。
    """
    if not in_domain:
        # 完全离题（`domain_word` 为假）：一句话打住，不摆条款、不装懂。
        #
        # ⚠️⚠️ **2026-10-12 这里的文案试过两种，第二种被实测否决。**
        #
        # 【原文案】「我只回答飞盘规则的问题。你这个我帮不上，场上的事可以问我。」
        #   问题：它把"离题"和"**你没说清**"当成一回事。实测
        #   「这种情况算吗」「他那样做可以吗」也落进这一支（没有域内词，闸门过不去）——
        #   但那**是**飞盘问题，只是空指代。回他"我只回答飞盘规则的问题"
        #   **在事实上是错的**，而且"我帮不上"是终点、不是邀请。
        #
        # 【第二种·❌ 已否决】一律回「这个我还判不了 —— 得知道具体是什么情况：
        #   谁做了什么、当时盘在谁手里。你描述一下场上发生了什么…」
        #   ⚠️ **实测打脸**：「帮我订张机票」「怎么学 Python」「推荐个餐厅」
        #   全都被回成"当时盘在谁手里" —— **对着订机票的人问盘的归属**。
        #   这比简短拒答**更让人困惑**，也更显得系统不懂自己在跟谁说话。
        #   ⇒ 教训：**一句话服务不了两种失败模式**；用同一句会造成荒谬回复。
        #
        # 【现在】用**问句的说法本身**当信号（轻量启发式，不做意图识别）：
        #   · 「…算吗 / 算不算 / 可以吗 / 行不行 / 对不对 / 犯规吗…」这类
        #     **在讨一个判定**的说法 ⇒ 他大概率问的是规则、只是没说清 ⇒ 给**邀请**；
        #   · 其余（"帮我订…""怎么学…""推荐个…"）⇒ 老实说这不在范围内。
        #   ⚠️ 这只是启发式，不保证准；但两边的**代价都很低**（都只是句文案），
        #      而"问错对象"的荒谬感代价高 ⇒ 值得分这两支。
        #   ⚠️ 刻意**不提**「规则里没有规定这一点」—— 那句话会让人以为
        #      规则本该有答案、只是暂时查不到，从而继续追问（原注释的理由，保留）。
        #   ⚠️ 两支都**一句话打住**：这一支不调大模型，答长了就是自相矛盾。
        if _looks_like_ruling_question(text):
            return ("这个我还判不了 —— 得知道具体是什么情况：谁做了什么、"
                    "当时盘在谁手里。你描述一下场上发生了什么，我照着规则给你说法。")
        return "这不在飞盘规则的范围里。场上的事（怎么判、该怎么做）可以问我。"

    lines = ["规则里没有和这个问题直接相关的规定。", ""]
    if nearby:
        lines.append("**你是不是想问这些？**")
        for n in nearby:
            lines.append(f"- （{n['id']}）{n['chapter_title']}：{n['text']}…")
        lines.append("")
        lines.append("如果都不是，换一种说法再问一次，或者补充一下场上的具体情况。")
    else:
        lines.append("换一种说法再问一次试试。")
    lines.append("\n如果这是个关于比赛规则的问题，也可以直接报条款号（比如「17.6」）。")
    return "\n".join(lines)
