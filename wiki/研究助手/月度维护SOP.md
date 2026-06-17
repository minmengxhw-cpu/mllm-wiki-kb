# 月度维护SOP

生成时间：2026-06-17

## 周期

- 每周：导入新增公众号文章，检查检索是否可用。
- 每月：更新分类、样本库、口径库、Obsidian、静态站和 GitHub。
- 重大专题前：生成专题素材包和核验清单。

## 标准命令

```bash
kb refresh
kb corpus
kb corpus-audit
kb corpus-style
kb external-sources --save
kb guardrails --save
kb verify --save
kb obsidian-sync --vault "/Users/cheer/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki"
kb obsidian-status --vault "/Users/cheer/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki" --save
KB_PUBLIC=1 python3 webapp/build_static.py
python3 -B -m unittest discover -s tests
python3 -m compileall -q src tests webapp
```

## 人工检查

- 查看新增文章数量是否合理。
- 查看 `data/quarantine/` 是否有失败文件。
- 抽查 `classification_priority_review.csv`。
- 检查 [[上海民盟微信公众号精选写作样本]] 是否需要替换。
- 检查 [[80周年口径风险清单]] 是否需要新增条目。
- 检查 `docs/content.json` 是否不含 `/Users/cheer`。

## GitHub 收尾

```bash
git status --short --branch
git add 相关文件
git commit -m "chore: monthly corpus maintenance"
git push origin main
git ls-remote origin refs/heads/main
```

## 完成标准

- 测试通过。
- Obsidian 状态缺失 0、需更新 0。
- 静态站构建成功。
- 本地 HEAD 与 GitHub main 一致。
- 工作树干净。
