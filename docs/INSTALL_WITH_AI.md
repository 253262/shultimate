# 让 AI 助手帮你安装

适用于 Codex、Claude Code、Cursor、WorkBuddy 等能够读取项目文件并运行本机命令的编程助手。你仍需自己申请智谱 API Key，并承担自己账号产生的模型费用。

把下面这段文字复制给 AI 助手：

> 请帮我在自己的电脑上安装 [253262/shultimate](https://github.com/253262/shultimate)。先阅读仓库 README 和 `scripts/setup.py`，检查 Python 3.10+ 是否可用，下载代码并运行安装脚本。脚本在本机虚拟环境安装 pypdf，并直接从体育总局社体中心官网取得规则 PDF，在本机生成语料；不要上传 PDF 或生成的中文语料。脚本会生成本机 `.env`；请暂停，让我自己在本机编辑器中填入智谱 API Key。我填好后只会回复“已填好”，不要读取、显示、复制或上传 `.env`，也不要让我把 Key 发到聊天里。建索引会调用我的智谱账号并产生费用，请在这一步明确询问我是否同意；我同意后可用 `--approve-index-cost` 继续。按我的系统运行 `setup.cmd`（Windows）或 `sh setup.sh`（macOS/Linux）。安装后检查本机 `/api/health`，打开本机网页，并告诉我如何停止、再次启动。遇到错误时只报告非敏感的错误信息，不上传日志或 Key。

AI 助手可以完成下载代码、检查 Python、运行安装脚本和定位普通环境错误。如果 Python 尚未安装，助手应先说明所需版本，并由你确认系统安装步骤。AI 的终端没有交互输入时，第一次运行脚本会创建空白 `.env` 并暂停；你填好 Key 后，由 AI 再运行脚本。你明确同意本次建索引费用后，AI 可运行 `setup.cmd --approve-index-cost` 或 `sh setup.sh --approve-index-cost`。不要把 API Key 写入 AI 聊天、公开 Issue、截图或命令行参数。

脚本只把 Key 存在本机仓库根目录的 `.env`，该文件已在 `.gitignore` 中。官网 PDF、中文语料与索引也只留在本机。首次建索引需要联网并产生费用。再次运行安装脚本时会核对语料、模型和索引指纹；只有一致时才复用旧索引。
