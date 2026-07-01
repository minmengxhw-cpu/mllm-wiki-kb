# Agent Guide

本仓库是“民盟工作知识库”的工程控制面。所有 Agent 必须遵守：Codex 定方向、做关键代码、控风险；MiniMax 做低风险执行；GitHub 做交接。

## 主控规则

- Codex / GPT-5.5 是唯一主控。
- Codex 负责总体规划、架构判断、核心 pipeline、关键配置、风险判断、最终审核和合并。
- MiniMax 不得替代 Codex 做架构判断、关键决策、核心配置修改或最终合并。
- Codex 额度不足时，MiniMax 只能按 `TASK_QUEUE_MINIMAX.md` 工单接力，结果作为候选，等待 Codex 恢复后复核。

## MiniMax 可做

- 批量阅读和摘要。
- 报告整理。
- 抽样验证。
- 文档更新。
- 测试补充。
- 非关键代码草稿。
- 候选规则、候选清单、候选审读报告。

## MiniMax 禁止

- push main 或 merge main。
- 修改主模型配置、核心架构、向量索引流程、生产数据或密钥。
- 删除原始资料。
- 覆盖 OCR 全文、向量文件、索引文件。
- 把抽样结论扩大成全量结论。
- 把 `ocr_unverified` 或候选字段写成可引用定稿。

## 分支规则

- Codex 分支使用 `codex/*`。
- MiniMax 分支使用 `minimax/*`。
- main 只能由 Codex 审核后合并。
- 每轮结束必须留下可复核交接信息。

## 每轮交接必须说明

1. 本轮完成了什么。
2. 修改了哪些文件。
3. 当前分支和 commit。
4. 测试命令和结果。
5. 是否已推送 GitHub。
6. 给 MiniMax 的下一步任务。
7. Codex 下次如何最低成本审核。

## 公开仓库边界

允许进入 GitHub：

- README、PRD、SOP、任务队列、复核清单。
- 可公开脚本、配置样例、脱敏小样本。
- 工程控制面和协作流程。

禁止进入 GitHub：

- 原始资料。
- 内部全文。
- OCR 全文。
- 向量文件。
- 索引文件。
- SQLite 数据库。
- 密钥和 API key。
- 未脱敏报告。

## 审核方式

Codex 恢复额度后，只审核 GitHub diff、`CODEX_REVIEW.md`、MiniMax 报告和最小测试结果。不要重新探索全项目，不要重跑全量任务，除非发现明确风险。
