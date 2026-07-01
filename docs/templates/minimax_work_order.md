# MiniMax Work Order

## 目标

[一句话说明任务目标]

## 建议分支

`minimax/[task-name]`

## 允许修改

- `[path]`

## 禁止修改

- `main`
- `.git/`
- `data/raw/`
- `data/processed/ocr_markdown_*`
- `index/`
- `*.sqlite`
- `.env`
- 原始资料
- 密钥和配置

## 输入材料

- `[path]`

## 输出要求

- 只输出候选报告或候选文件。
- 不得把抽样结论扩大成全量结论。
- 不得把 `ocr_unverified` 写成 verified。

## 测试命令

```bash
git diff --name-only
git diff --stat
```

## Codex 复核方式

Codex 只看 diff、报告和测试输出。
