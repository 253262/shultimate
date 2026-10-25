# Shultimate · 飞盘规则助手

用中文提问团队飞盘规则，系统检索对应条款，再用你自己配置的大模型组织回答。
这是独立的第三方项目，不是 WFDF 或国家体育总局的官方产品；遇到争议请核对
[WFDF 英文规则](https://rules.wfdf.sport/)及
[中文版官方发布页](https://www.sport.gov.cn/stzx/n5434/c28603165/content.html)。

## 下载与安装

需要 Python 3.10+、网络连接和**你自己的智谱 API Key**。DeepSeek Key 可选。
模型请求直接从你的电脑发往服务商，不经过项目作者的账号，费用计入你的账号。
首次建索引会产生模型费用；安装脚本会先询问，不会自行付费建库。

1. 下载本仓库 ZIP 并解压，或运行
   `git clone https://github.com/253262/shultimate.git`。
2. 在项目目录运行：

   | 系统 | 命令 |
   | --- | --- |
   | Windows | 双击 `setup.cmd`，或在命令提示符运行 `setup.cmd` |
   | Windows PowerShell | `.\setup.ps1` |
   | macOS / Linux | `sh setup.sh` |

3. 按终端提示输入自己的 Key、确认建索引费用。脚本会打开本机网页；
   按 `Ctrl+C` 停止。

脚本会在本机 `.venv` 安装 PDF 解析依赖，从
[体育总局社体中心官网下载中文版 PDF](https://www.sport.gov.cn/stzx/n5434/c28603165/content.html)，
校验文件指纹，**只在你的电脑上**切成检索语料，再用你的 Key 建索引。
中文版 PDF、切块结果、索引和 Key 都被 `.gitignore` 排除，公开仓库不提供这些中文译文文件。
官网文件若更新，校验会停下来提示核查，不会悄悄把不同版本当作同一版。
这一路径需要能访问官网和 Python 包仓库；失败时可从官方页面手工下载 PDF，
放到 `data/source/official-zh.pdf` 后重试。

若只想验证安装、PDF 切块和离线测试，可给相应安装命令加
`--offline-check`；该模式不读取 Key、不调用模型，也不启动服务。

AI 助手用户可复制 [AI 安装指令](docs/INSTALL_WITH_AI.md)。
不要把 API Key 粘贴到聊天、Issue、截图或命令行参数里。

## 手动安装

先创建 Python 虚拟环境并在其中安装依赖：

```bash
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -r requirements.txt
# macOS/Linux: .venv/bin/python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，填写 `ZHIPU_API_KEY`。
随后使用虚拟环境的 Python 顺序执行：

```bash
python scripts/build_corpus.py  # 官网 PDF → 本机规则语料，不调用模型
python scripts/build_index.py   # 调用你自己的智谱账号，产生费用
python app/server.py             # 默认 http://127.0.0.1:8000
```

Windows 请将上面三处 `python` 换成 `.venv\Scripts\python`，
macOS/Linux 换成 `.venv/bin/python`。改变规则 PDF、语料或向量模型后须重建索引；
安装脚本会检查语料指纹和模型设置，不会仅凭文件存在就跳过。

## 能做什么

- 根据自然语言检索规则，显示条款号与原文，并生成中文回答。
- 把“规则没有规定”“问题超出规则范围”“信息不足需澄清”分开处理。
- 本机保存反馈与人工裁定；人工裁定的查看和修改都需 Owner 令牌。
- 可浏览章节、术语定义和 24 条由项目作者翻译的 WFDF 附录。

**限制：**无智谱 Key 不能建索引或进行规则检索；没有索引时服务不会启动。
模型服务的版本与输出会变化，无法保证回答逐字相同。
公开版不含生产环境的人工裁定库、内部评测集、部署配置和桌面启动器。
真实模型端到端验收与跨平台安装结果见 [复现记录](docs/REPRODUCE.md)。

## 安全与公网部署

默认只监听 `127.0.0.1`。将 `HOST` 改为非本机地址时，服务要求
`PUBLIC_ACCESS_PASSWORD` 至少 16 位，并对所有页面和接口启用 HTTP Basic 认证
（用户名固定为 `shultimate`）。公网访问还必须由 HTTPS 反向代理保护；
不要直接通过明文 HTTP 传输密码或 Owner 令牌。
若服务器监听本机但由反向代理转发到公网，也应主动设置此密码。

`CURATED_TOKEN` 是另一把独立的 Owner 令牌，控制人工裁定的读取与写入。
不要将私人问题或答案输入可公开的服务。
付费调用预算仍有运行时限制；请同时在模型服务商账户设置消费上限，
并在反向代理设置访问限制。详见 [安全策略](SECURITY.md)。

## 许可和语料

- 代码：[PolyForm Noncommercial 1.0.0](LICENSE)，商业使用须另行取得授权。
  这是**源码可见的非商业许可**，不是 OSI 定义的开源许可。
- WFDF 英文规则：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；
  英文规则与本项目代码的许可互相独立。
- 中文规则译文由用户从[官方发布页](https://www.sport.gov.cn/stzx/n5434/c28603165/content.html)
  在本机取得。**本仓库不授予译文的再分发或商业使用权**。
  官网发布不等于本项目取得了将译文放入 GitHub 的许可。
- 字体及附录的来源、署名见 [第三方说明](THIRD_PARTY_NOTICES.md)。

报告错误、提供改进建议请使用 GitHub Issues。发现安全问题请先读
[SECURITY.md](SECURITY.md)，不要在公开 Issue 贴 Key 或私人数据。
