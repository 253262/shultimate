# 复现与验收

本项目的复现目标是：从公开仓库下载代码，用用户自己的账号构建本地中文语料和索引，
启动服务，并在固定问题上得到正确的条款引用。第三方模型的回答措辞可能变化，
不以逐字相同作为验收标准。

## 1. 全新安装

请使用全新 clone，不复制他人的 `.env`、索引或用户日志。
需要 Python 3.10+、自己的智谱 Key，以及访问官方 PDF 和模型服务商的网络。

普通用户执行 `setup.cmd`（Windows）或 `sh setup.sh`（macOS/Linux）。
脚本在 `.venv` 中安装锁定的 `pypdf`，从
[体育总局社体中心](https://www.sport.gov.cn/stzx/n5434/c28603165/content.html)
下载中文版 PDF，在本机生成语料。下载文件 SHA-256 必须为：

```text
c421b7e79ab598465db9341338892207d1724bf4487fb476d01fd1a3e0f6aad5
```

若官网下载失败，请从上述官方页面手动取得 PDF，放到
`data/source/official-zh.pdf`，再运行安装脚本。校验失败时不要绕过；
先确认官网是否发布了新版规则。

只验证安装入口、PDF 切块和离线单测，不读取 Key、不调用模型、不启动服务：

```bash
# Windows: setup.cmd --offline-check
# Windows PowerShell: .\setup.ps1 --offline-check
# macOS/Linux:
sh setup.sh --offline-check
```

`--offline-check` 仍需要能访问官网和 Python 包仓库；它不读取 Key、不调用模型、
不启动服务。若官网下载失败，请按第 1 节手工下载 PDF 后重试。

## 2. 无 Key 可验证的部分

以下命令使用虚拟环境的 Python（Windows 是 `.venv\Scripts\python`，
macOS/Linux 是 `.venv/bin/python`）：

```bash
python scripts/build_corpus.py
python -m unittest discover -s tests
```

切块验收：368 条规则、40 条名词定义、1 条简介、20 个章节；
不能有重复条号或空正文。切块脚本在写入前检查这些条件。
新生成的 PDF 和语料文件被 Git 忽略，不应出现在待提交文件清单里。

## 3. 需要本人 Key 的验收

只有本人确认建索引费用后，才执行：

```bash
python scripts/build_index.py
python scripts/build_index.py --check
python app/server.py
python scripts/verify_cases.py
```

`--check` 应退出码 0；无索引、语料或模型设置变化时应返回非零。
`/api/health` 应返回 `ready: true`。固定问答至少检查：

| 问题 | 必须看到 |
| --- | --- |
| 开盘出界怎么办？ | 引用适用的开盘条款，给出处理步骤 |
| 比赛场地多大？ | 不凭空编造规则正文没有提供的米数 |
| 飞盘多少钱？ | 说明不属于规则范围 |
| 我被撞了算犯规吗？ | 说明判定条件；信息不足时追问 |
| 你好 | 可走无检索的闲聊路径 |

验收应核对响应中的 `citations` 数组，而不是只看回答正文写了什么条号。
还应抽查引用条文与[官网 PDF](https://www.sport.gov.cn/stzx/n5434/c28603165/content.html)
是否一致。缺少 Key 时，真实问答验收不能算完成。

## 4. 当前记录

2026-10-25：在 Windows 上，从官网 PDF 构建出 368 条规则、40 条名词定义、
1 条简介；干净克隆中运行完整安装入口与 297 项离线单测通过（29 项跳过）。
跨平台 CI 会在 Windows、macOS、Linux 实际下载官网 PDF、构建本机语料并运行单测；
下载失败会按真实安装失败处理（脚本带 Cookie 与重试），不会用提交的译文绕过。

2026-10-25 真实模型验收：使用 Owner 的个人智谱 Key，在干净 clone 中构建
433 条索引（embedding-3 / 2048 维，约 15 秒），运行 5 个固定用例，**5/5 通过**：
R1 引用 7.12、R2 引用 17.6.1.1、R3 引用 9.1、R4 正确拒答且无引用/无条款号、
R5 引用 2.1 并说明正文无尺寸。验收只判结构化 `citations` 与边界，不保证回答逐字相同。

**仍受模型版本影响：**本次记录的是 2026-10-25 的智谱 `glm-4-flash` 结果；
模型版本、服务商行为与提问措辞都会影响回答，不能保证每次逐字复现。

公开版不含生产仓库中的人工裁定库、内部评测数据和桌面启动器；
由第三方模型生成的回答也会受模型版本影响。
