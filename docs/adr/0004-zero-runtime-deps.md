# ADR-0004：零运行时第三方依赖

- 状态：部分修订。服务运行仍用标准库；2026-10-25 起，用户本机导入官方 PDF 需要锁定的 pypdf。
- 日期：2026-10-13
- 相关：`requirements.txt`、`scripts/build_corpus.py`

## 背景

这是给飞盘社群用的开源项目，不是公司内部服务。
"clone 下来就能跑"是给开源用户的核心承诺；
每多一个依赖，就多一类"装不上 / 版本冲突 / 国内下载失败"。

## 决策

- 运行时只用 **Python 3.10+ 标准库**
- 前端只用 **vanilla JS + CSS**，无框架、无 CDN、无构建
- 开发工具（PyInstaller 等）可以装，但不进运行时
- **新增任何第三方依赖必须先经 owner 同意**

## 后果

- ✅ 一条 `python app/server.py` 就能跑，不需要 `pip install`
- ✅ 没有供应链风险（没有 npm/pip 传递依赖）
- ✅ 代码可读、可审计；评测与线上共用同一份实现
- ⚠️ 有些轮子要自己写：WebSocket 帧、Canvas 换行、SVG 球场、更新器
- ⚠️ 不能用 numpy/FAISS 做向量运算；474 条够用，规模上去要重新评估

## 被否决的替代方案

- **FastAPI + uvicorn**：多两个依赖，收益只是路由糖
- **React/Vue**：构建链和 CDN 都会破坏"clone 即跑"
