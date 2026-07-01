# 公开仓库发布边界

本仓库只作为工程控制面和协作交接台，不作为完整资料库。

## 可以公开

- 项目目标、PRD、SOP、协作规则。
- Codex 与 MiniMax 的任务队列、审核清单、工作流说明。
- 不含原文、不含私人路径、不含密钥的脚本模板。
- 不含全文的配置样例。
- 公开资料的小样本和脱敏演示数据。
- 质量评测方法、测试问题模板和验收口径。

## 需要脱敏后才能公开

- 进度看板。
- 质量审读报告。
- OCR 质量评估摘要。
- 人物档案字段候选摘要。
- 本地路径、文件名、具体工作事项、内部口径。

脱敏后仍应保留状态标签，例如 `candidate`、`ocr_unverified`、`needs_manual_check`，避免读者把候选材料误认为定稿。

## 禁止公开

- 原始工作资料。
- 桌面近期事项资料。
- 上海盟讯 OCR 全文。
- 2021 档案 OCR 全文。
- 人物档案 PDF。
- 向量文件。
- SQLite 索引和全文索引。
- API key、token、密钥、账号配置。
- 未脱敏的 MiniMax 或 Codex 审读报告。
- 任何会把候选结论误导为可引用定稿的文件。

## 提交前检查

提交前至少检查：

```bash
git status -sb
git diff --stat
git diff --name-only
```

不能出现以下路径或文件类型：

- `data/raw/`
- `data/processed/ocr_markdown_`
- `index/`
- `*.sqlite`
- `*.db`
- `*.jsonl` 中包含全文、向量或内部字段
- `.env`
- `.venv*`

## GitHub 协作规则

- Codex 分支：`codex/*`
- MiniMax 分支：`minimax/*`
- MiniMax 不允许 push main 或 merge main。
- main 只能由 Codex 审核后合并。
- 每轮结束必须留下 `CODEX_REVIEW.md` 或等价复核说明。
