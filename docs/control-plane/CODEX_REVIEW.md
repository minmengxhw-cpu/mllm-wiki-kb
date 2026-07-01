# CODEX_REVIEW

## 本轮变更目的

本轮把 MiniMax 对“上海盟讯红学高亮规则”和“2021 档案人工核 PDF 工作流”的审读建议，转化为 Codex 主控后的 v1.1 候选文件，并补齐 MiniMax 接力工单。

## 已生成候选文件

- `data/config/shanghai_mengxun_redology_highlight_rules_v1_1.jsonl`
- `data/reports/shanghai_mengxun_redology_highlight_rules_v1_1.md`
- `data/processed/research_2021_manual_pdf_check_queue_v1_1.jsonl`
- `data/reports/research_2021_archives_manual_pdf_check_workflow_v1_1.md`
- `data/reports/codex_review_minimax_redology_2021_v1.md`
- `TASK_QUEUE_MINIMAX.md`

## 安全结论

- 未删除原始资料。
- 未覆盖生产数据。
- 未重建向量或索引。
- 未修改主模型配置、核心架构、关键依赖或密钥。
- 所有新增规则、队列和审读结论均为候选，不进入默认检索层，不作为可引用定稿。

## 最低成本复核步骤

1. 查看 `data/reports/codex_review_minimax_redology_2021_v1.md`，确认 MiniMax 建议采纳/不采纳边界。
2. 校验两个 JSONL 文件：

```bash
python3 - <<'PY'
import json
from pathlib import Path
targets = [
    ('redology_v1_1', Path('data/config/shanghai_mengxun_redology_highlight_rules_v1_1.jsonl'), 10),
    ('research2021_queue_v1_1', Path('data/processed/research_2021_manual_pdf_check_queue_v1_1.jsonl'), 9),
]
for name, path, expected in targets:
    n = 0
    for i, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        json.loads(line)
        n += 1
    print(name, n, 'expected', expected, 'ok=', n == expected)
PY
```

3. 查看 `TASK_QUEUE_MINIMAX.md`，确认 MiniMax 只被授权审读报告和候选文件。
4. 查看 `当前进度看板.md` 末尾 117-119 项，确认本轮状态未被表述成已入库或已验证。

## 下次 Codex 优先事项

1. 审核 MiniMax 是否按 `TASK_QUEUE_MINIMAX.md` 生成候选报告。
2. 先处理上海盟讯红学高亮规则的人工审校入口，再处理 2021 档案字段抽取。
3. GitHub 阶段只发布工程控制面和公开样例，不发布内部资料、OCR 全文、向量或索引。
