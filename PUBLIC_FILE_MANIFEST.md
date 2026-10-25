# 公开包清单与复验

公开包包含运行代码、前端、安装脚本、自动测试、说明文档、许可文件、
WFDF 附录的项目作者译文、字体及字体许可。

公开包**不包含**国家体育总局发布的中文规则 PDF、从该 PDF 提取的
`data/rules.jsonl`、`data/definitions.jsonl`、`data/preface.jsonl`、
`data/summary.json`，也不包含此前由小程序导出的
`data/source/rules-source.json`。这些文件只在用户电脑上生成。
模型 Key、向量索引、查询日志、反馈、人工裁定库、内部评测数据和桌面启动器
同样不在公开包。

## 克隆后检查

```bash
git status --short
git ls-files data
git log --oneline
```

`git status --short` 应为空。公开的 `data/` 下应只看到附录与示例问题；
不应看到中文规则全文或任何 `.env`。安装后，用户电脑会生成被忽略的
PDF、语料和索引；这些本地文件不应出现在 `git status --short` 中。

发行版本的 HEAD 和文件数以 GitHub 当前提交为准，不在本文件写死
自引用的提交哈希。官方 PDF 校验值和本机重建标准见
[复现文档](docs/REPRODUCE.md)。

**历史提醒：**旧公开提交曾包含中文译文。新版本移除这些文件后，
已下载副本和第三方缓存不能由本仓库自动收回；不要从旧提交复制译文重新发布。
