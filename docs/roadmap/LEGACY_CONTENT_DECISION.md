# 旧仓库内容处理决策

## 背景

本仓库原来以微信公众号语料库和盟史 wiki 生成为主。现在 main 已改为“民盟工作知识库工程控制面”，旧内容需要重新判断是否继续保留。

当前原则：先记录处理决策，不立即删除。任何删除、移动或归档都必须经过 Codex 主控审核。

## 保留

- `README.md`
- `AGENTS.md`
- `docs/control-plane/`
- `docs/roadmap/`
- `docs/templates/`
- `.gitignore`

这些文件构成新的工程控制面和协作交接层。

## 候选保留

- `src/`：待 Codex 审核后决定是否改写为新本地知识库 CLI。
- `tests/`：待 Codex 审核后决定是否重写为新工作流测试。
- `scripts/`：待检查是否只包含安全工具脚本。
- `pyproject.toml`、`schema.sql`、`Dockerfile`：待判断是否仍适配新定位。

候选保留内容必须满足：不包含私密路径、不依赖未公开数据、不误导用户以为 GitHub 是完整资料库。

## 候选归档或删除

- `wiki/`：旧微信公众号语料库生成内容，可能与新整体知识库定位不一致。
- `index/`：旧索引和样例，需确认是否含不该公开内容。
- `obsidian/`：旧同步状态，可能包含过时路径和状态。
- `webapp/`：旧展示应用，需判断是否继续维护。
- `docs/content.json`：旧静态站内容，需确认是否仍适合公开。

## 推荐处理路径

### 方案 A：归档旧公众号库

把旧内容移动到：

```text
legacy/wechat-public-kb/
```

并在目录内新增说明：

```text
这些内容是旧微信公众号语料库历史快照，不代表当前完整工作知识库，不作为内部资料或引用级史料定稿。
```

优点：保留历史成果，降低误删风险。

缺点：仓库仍然较重，读者可能误解旧内容和新系统的关系。

### 方案 B：删除旧生成内容，仅保留工程控制面

删除或后续重新生成：

- `wiki/`
- `index/`
- `obsidian/`
- `webapp/`
- `docs/content.json`

优点：仓库清爽，定位明确。

缺点：旧公众号成果从 GitHub main 消失，需要依赖 Git 历史或本地备份回看。

### 方案 C：分阶段迁移

第一步只保留工程控制面；第二步由 MiniMax 生成旧内容清单；第三步 Codex 决定按目录归档或删除。

这是当前推荐方案。

## 决策原则

- 不保留会让读者误以为这是完整资料库的旧生成内容。
- 不保留难以维护的旧索引。
- 不把 GitHub 当作本地生产数据仓库。
- 需要保留的旧内容必须标注历史快照、来源层级和非定稿状态。
- 删除前先生成清单和可回滚 commit。

## MiniMax 审读工单

建议分支：`minimax/legacy-content-review`

允许修改：

- `docs/roadmap/minimax_legacy_content_review.md`

禁止修改：

- `main`
- `wiki/`
- `index/`
- `webapp/`
- `obsidian/`
- `src/`
- `tests/`
- 原始资料、OCR 全文、向量、索引、密钥

目标：

- 只读审查旧内容是否应归档、删除或保留。
- 输出每类目录的风险、收益、建议处理方式。
- 不直接移动或删除任何文件。

测试：

```bash
git diff --name-only
git diff --stat
```
