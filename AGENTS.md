# Mllm Wiki KB Agent Guide

本项目是“盟参”民盟语料库首席参谋系统的本地工作台。默认用现有知识库和 SQLite 索引，不另建平行资料库。

## 盟参路由

当用户在本聊天窗口输入下列口令时，按对应本地命令执行，并把结果转述给用户：

- `/稿 主题`：运行 `kb staff draft "主题"`，输出文稿素材包。
- `/史 人物/事件/机构/地点`：运行 `kb staff history "主题"`，输出史实卡片和研究入口。
- `/题 选题`：运行 `kb staff topic "选题"`，输出查重报告和差异化角度。
- `/核 文稿全文`：运行 `kb staff check "文稿全文"`；长文优先保存成临时文本后用 `kb staff check --file 文件路径`。

需要保存到 Obsidian 工作流时，给命令加 `--save`，再按需运行 `kb obsidian-sync`。

## 输出规则

- 所有事实性内容必须带来源编号、raw 原文路径或明确标注 `[待核]`。
- 统一采用三段式：结论、素材、风险提示。
- 红头文件、内部口径和人工终审优先级高于本公开语料库。
- 微信公众号原文只作为公开表述层素材，不替代内部文件和权威档案。

## 数据维护

- 文章增量用 `kb refresh`。
- 微信公众号语料库体检、分类标签和样本库更新用 `kb corpus`。
- 微信公众号文章分类抽检和人工校订表更新用 `kb corpus-audit`。
- 上海民盟分体裁写作模板和文史盟史研究入口更新用 `kb corpus-style`。
- 盟参种子库位于 `index/formulations.jsonl`、`index/blacklist.csv`、`index/entities/*.jsonl`。
- 种子库先保证可拦截高风险问题，再逐步扩展，不把未校订条目写成定论。

## 语料库优先工作

当用户要求“先把微信文章做实”“语料库体检”“文章分类”“写作样本库”时，优先运行 `kb corpus`，并基于以下产物回答：

- `index/corpus/article_labels.jsonl`
- `index/corpus/classification_review.csv`
- `wiki/研究助手/微信公众号语料库体检报告.md`
- `wiki/研究助手/微信公众号分类质量诊断报告.md`
- `wiki/研究助手/微信公众号文章分类抽检表.md`
- `wiki/研究助手/上海民盟微信公众号分体裁写作模板.md`
- `wiki/研究助手/上海民盟2023年以来写作样本库.md`
- `wiki/研究助手/微信公众号文史盟史文章专题库.md`
- `wiki/研究助手/微信公众号文史盟史研究入口清单.md`

Google Drive 工作材料暂作为外部参考层，不直接混入微信公众号主语料层。
