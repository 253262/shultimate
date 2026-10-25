# 参与贡献

## 先读这个

本项目的代码许可是 **PolyForm Noncommercial 1.0.0**（源码可见，非 OSI 开源）。
提交贡献即表示你同意你的贡献以同一许可发布。
如果你不认可这一点，请不要提交 PR。

## 可以贡献什么

**欢迎：**

- 检索未命中的规则问题（附上你认为正确的条款号，以及你问的原话）
- 规则条文的翻译勘误（对照 WFDF 官方英文原文）
- 文档错误、安装步骤失败的报告
- 界面在特定分辨率/浏览器下的显示问题
- 安全问题的私密报告（见 [SECURITY.md](SECURITY.md)）

**需要先讨论：**

- 任何新增第三方依赖（PDF 导入仅使用锁定版本的 pypdf）
- 更换向量模型或引入向量数据库
- 改变相关性闸门（"规则里没有"的判定）逻辑
- 大规模重构

**不接受：**

- 抓取、内置或再分发未获授权的第三方译文
- 提交真实用户提问、聊天记录、个人信息
- 提交密钥、内网地址、生产配置

## 核心约束：服务运行保持轻量

`app/` 的服务路径使用 Python 3.10+ 标准库。用户本机导入官网 PDF 的
`scripts/build_corpus.py` 使用锁定的 `pypdf`；安装入口会在项目虚拟环境安装。
不要将 PDF、提取后的中文语料或 Key 提交到 PR。

语料规模（四百多条 × 2048 维）下，纯 Python 全量点积约 70 ms，够用。
引入 numpy / FastAPI / 向量数据库会显著抬高陌生人的启动门槛。

如果你认为某个依赖不可避免，请在 Issue 里先说明**不引入它的代价**。

## 提交前自检

```bash
# 本机从官方 PDF 切块；需先安装 requirements.txt
python scripts/build_corpus.py

# 单元测试（unittest，不是 pytest）
python -m unittest discover -s tests

# 公开树/历史不得包含官方中文规则 PDF 或切块
python scripts/check_public_tree.py

# 服务能起来、健康检查通过
python app/server.py
curl http://127.0.0.1:8000/api/health
```

改了检索相关逻辑的，请在 PR 描述里附上：改了哪条规则、影响了哪些
固定用例（见 [docs/REPRODUCE.md](docs/REPRODUCE.md)）、有没有回归。

## 提交信息

一句话说清"改了什么、为什么"，不要写"update"、"fix bug"。
涉及取舍的改动，把被否掉的方案也写一句——后来的人需要知道你试过什么。

## 数据类贡献

规则条文的任何改动都要能对照到 WFDF 官方原文的条款号。
附录条文请标明 `zh_source`，第三方译文请附带授权凭据，
否则不会被合并。
