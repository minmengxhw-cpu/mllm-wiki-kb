# TASK_QUEUE_MINIMAX

本文件用于 Codex 额度不足时的 MiniMax 接力。MiniMax 只能执行低风险、可复核任务；所有结果均为候选，等待 Codex 主控复核。

## 总规则

- 建议分支必须使用 `minimax/*`。
- 不允许 push main、merge main、删除原始资料、覆盖生产数据。
- 不允许修改密钥、主模型配置、核心 pipeline、向量索引流程、已封版向量文件、原始资料。
- 不允许把抽样结论扩大为全量结论。
- 每个任务结束必须写 `CODEX_REVIEW.md` 或对应报告，说明做了什么、改了哪些文件、如何测试。

## 任务 1：复核上海盟讯红学高亮规则 v1.1

- 建议分支：`minimax/review-redology-v1-1`
- 目标：复核 `shanghai_mengxun_redology_highlight_rules_v1_1.jsonl` 的 10 条规则是否适合“只高亮、不自动替换”的人工审校入口。
- 允许修改：
  - `data/reports/minimax_redology_v1_1_review.md`
  - 如需提出修订，只能新增 `data/config/shanghai_mengxun_redology_highlight_rules_v1_2_candidate.jsonl`
- 禁止修改：
  - `data/config/kb_config.json`
  - `src/`
  - `index/`
  - `data/processed/ocr_markdown_*`
  - 任何原始资料目录
- 必须检查：
  - 是否仍保持 `must_not_auto_replace: true`
  - 是否区分 `shape_error`、`sensitive_term`、`variant_warning`
  - 是否对 `紅裡夢`、`紅樓婆` 等高风险词保留双重核验
  - 是否有把政治/姓名敏感词当作普通形近字的风险
- 测试命令：

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path('data/config/shanghai_mengxun_redology_highlight_rules_v1_1.jsonl')
n = 0
for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
    obj = json.loads(line)
    assert obj.get('must_not_auto_replace') is True, (i, obj.get('rule_id'))
    assert obj.get('review_status') in {'candidate', 'needs_sensitive_double_check'}
    n += 1
print('ok', n)
PY
```

## 任务 2：复核 2021 档案人工核 PDF 队列 v1.1

- 建议分支：`minimax/review-research2021-pdfqueue-v1-1`
- 目标：复核 9 个试点人物 PDF/OCR 人工核验队列是否适合下一轮字段抽取，不直接入库。
- 允许修改：
  - `data/reports/minimax_research2021_pdfqueue_v1_1_review.md`
  - 如需提出修订，只能新增 `data/processed/research_2021_manual_pdf_check_queue_v1_2_candidate.jsonl`
- 禁止修改：
  - `data/processed/research_2021_manual_pdf_check_queue_v1_1.jsonl`
  - `data/processed/person_*`
  - `src/`
  - `index/`
  - 原始 PDF 和 OCR Markdown
- 必须检查：
  - 9 人是否均有原始 PDF 路径和 OCR Markdown 路径
  - `hard_gating_fields` 是否只保留能决定身份/入盟/来源的硬字段
  - 家庭成员、社会关系、履历等是否只作为辅助线索，不作为强制字段
  - `field_confidence` 和 `review_status` 是否避免过度定稿
- 测试命令：

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path('data/processed/research_2021_manual_pdf_check_queue_v1_1.jsonl')
n = 0
for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
    obj = json.loads(line)
    assert obj.get('review_status') == 'needs_manual_pdf_check', (i, obj.get('person_name'))
    assert obj.get('pdf_path') and obj.get('ocr_markdown_path'), (i, obj.get('person_name'))
    assert 'source_file_identity' in obj.get('hard_gating_fields', [])
    n += 1
print('ok', n)
PY
```

## 任务 3：GitHub 工程控制面干跑检查

- 建议分支：`minimax/github-control-plane-dryrun`
- 目标：为后续升级 `minmengxhw-cpu/mllm-wiki-kb` 做只读清单，不推送、不合并。
- 允许修改：
  - `data/reports/minimax_github_control_plane_dryrun.md`
- 禁止修改：
  - `.git/`
  - GitHub 远端
  - 原始资料、OCR 全文、向量文件、索引文件
  - README、代码、配置模板，除非 Codex 后续明确授权
- 必须检查：
  - 哪些文件可以公开：README、docs、脚本模板、配置样例、公开小样本
  - 哪些文件禁止公开：原始资料、内部全文、OCR 全文、向量、索引、未脱敏报告
  - 现有仓库从“公众号语料库”升级为“整体工作知识库工程控制面”的最小改动清单
- 输出要求：
  - 只写报告，不提交远端。
  - 报告中必须标明“可公开”“需脱敏”“禁止公开”三类。
