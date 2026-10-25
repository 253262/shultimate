# 架构（一页）

> 目录结构与运行方式见 [README](../README.md)；API 字段见 [docs/api.md](api.md)。
> 最后更新：2026-10-25

## 链路

```
用户输入
  │
  ├─ 第 0 层：人工裁定层（最高优先级；本仓库未包含该数据）
  │    命中 ⇒ 直接返回（绕过检索/生成，标「人工核定」）
  │
  ├─ 第 0.5 层：轻量对话（寒暄 / 元问题 / 问系统本身）⇒ 不检索
  │
  ├─ 第 0.6 层：诉求分路（chat / vent / lookup / rule）⇒ 只改语气
  │
  ├─ 第 0.7 层：范围与意图闸门（rule / related / off_topic / harm）
  │    off_topic / harm ⇒ 固定话术，不检索
  │
  ├─ 改写 → 混合检索（向量 + 关键词 + 情形族）→ 相关性闸门
  │    不 confident ⇒ "规则没写" + 下一步
  │
  ├─ 生成层（DeepSeek 优先，智谱兜底；温度 0.2）
  │    铁律：只用给定条款；不足就说不足
  │
  └─ 后处理：术语白话（glossary）+ 引用（citations）+ 流式帧
       │
       ├─ /api/ask（同步）/ /api/ask/stream（SSE）
       └─ /api/card（判定卡）→ /api/branches（如果当时）
```

## 数据

| 文件 | 作用 |
|---|---|
| `data/rules.jsonl` | 用户本机从官网下载 PDF 后生成的 368 条规则正文；不入库 |
| `data/definitions.jsonl` | 用户本机生成的 40 条官方术语定义；不入库 |
| `data/appendix.jsonl` | 附录层（不是正文条款） |
| `data/sample_questions.jsonl` | 复现用固定提问 |
| `data/index.json` / `embeddings.bin` | 检索索引（脚本生成，不入库） |
| `data/usage.sqlite3` | 本机付费调用预算计数；不入库 |

> 生产版本还有三类数据未随本仓库发布：`data/appendix_notes.jsonl`（附录注解）、
> `data/curated.jsonl`（人工裁定库）、`data/eval/*`（评测基线与 golden set）。
> 它们是内部资产，缺失会让部分行为与线上不同，详见
> [REPRODUCE.md](REPRODUCE.md)。

## 关键模块

| 模块 | 职责 |
|---|---|
| `app/scope.py` | 四岔口判据（rule/related/off_topic/harm） |
| `app/intent.py` | 诉求分路（只改语气） |
| `app/smalltalk.py` | 轻量对话判据 |
| `app/routes.py` | 情形 → 条款族 |
| `app/retriever.py` | 混合检索 + 打分（单一真源） |
| `app/generator.py` | 提示词 + 改写 + 多模型兜底 + branches |
| `app/server.py` | HTTP + 静态资源 + 流式 + 卡片 |
| `static/app.js` | 复盘页 / 聊天 / 查规则 / 练习 / 笔记 |

## 失败模式（按严重度）

| 失败 | 表现 | 防线 |
|---|---|---|
| **编造条款** | 引了一条不存在的规则 | 铁律 + `citations` 只放语料 + 守卫 |
| **误拒** | 明明有条款却说"没写" | 判宽勿严；golden 的 false_refusal |
| **过度回答** | 该拒的离题题硬答 | scope 闸门 + golden 的 over_answer |
| **意图错路** | 情绪/流程题走了规则链路 | scope/intent + golden 的 scope_pass |
| **检索未命中** | 条款在库里但没召回 | fast_regress + golden H@k |
| **慢/转圈** | 90 秒没结果 | 流式 + 看门狗 + done 帧兜底 |

## 决策

- 不用向量数据库：`docs/adr/0001-no-vector-db.md`
- 人工裁定优先：`docs/adr/0002-curated-first.md`
- 只做浏览器启动器：`docs/adr/0003-browser-launcher.md`
- 服务运行维持标准库、PDF 导入使用 pypdf：`docs/adr/0004-zero-runtime-deps.md`
