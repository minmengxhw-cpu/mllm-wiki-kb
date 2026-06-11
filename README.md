# Mllm Wiki KB

本项目是一个面向“民盟、上海民盟、统一战线、盟史、人物、会议、履职、宣传文稿”的 Mac 本地研究助手。

设计目标是混合使用：

- 工程化 RAG
- Karpathy LLM Wiki 思路
- Obsidian 本地阅读和人工维护

当前已经导入微信公众号文章，可作为民盟研究、上海民盟地方史整理、人物事件地点卡片、参政议政素材和微信公众号写作辅助的本地知识底座。

## 默认路径

- 项目路径：`~/Documents/mllm-wiki-kb`
- 微信公众号原文路径：`~/Downloads/微信公众号`
- Obsidian Vault：`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki`

当前原则：

- 不修改微信公众号原始文件。
- `data/raw/` 保存清洗后的不可变原文层。
- `index/kb.sqlite` 保存元数据和 chunk。
- `wiki/` 保存 LLM 编译后的结构化知识页。
- Obsidian 只同步 `wiki/`，不默认同步全部 raw 原文。

## macOS 安装

```bash
cd ~/Documents/mllm-wiki-kb
python3 -m pip install -e .
```

安装后可以使用：

```bash
kb --help
```

如果不想安装，也可以临时运行：

```bash
cd ~/Documents/mllm-wiki-kb
PYTHONPATH=src python3 -m kb.cli --help
```

## 初始化

```bash
kb init
```

这会创建目录、初始化 `index/kb.sqlite`，并写入操作日志。

## 扫描原文

```bash
kb scan --input ~/Documents/微信公众号
```

扫描只统计文件，不导入、不修改原文。

如果文章实际还在下载目录，可以先测试：

```bash
kb scan --input ~/Downloads/微信公众号
```

## 测试导入 20 篇

先 dry-run：

```bash
kb import --input ~/Documents/微信公众号 --dry-run --limit 20
```

再真实导入 20 篇：

```bash
kb import --input ~/Documents/微信公众号 --limit 20
```

导入会：

- 支持 `.md`、`.txt`、`.html`、`.htm`、`.json`
- 提取标题、公众号、作者、发布时间、原文链接
- 清洗微信页噪声
- 用 `content_hash` 去重
- 写入 `data/raw/`
- 写入 `articles` 和 `article_chunks`
- 失败文件复制到 `data/quarantine/`

## Obsidian 同步

dry-run：

```bash
kb obsidian-sync --vault ~/Documents/Obsidian/MllmWiki --dry-run
```

真实同步：

```bash
kb obsidian-sync --vault ~/Documents/Obsidian/MllmWiki
```

同步规则：

- 只同步 `wiki/`
- 更新目标文件时只替换 `KB-GENERATED` 区
- 保留 `HUMAN-NOTES` 区
- 覆盖前会生成 `.bak-时间戳` 备份

## 状态检查

```bash
kb check
```

## 查看操作日志

```bash
kb log
```

## 本地全文索引

```bash
kb index
```

当前使用 SQLite FTS5 全文索引 + 本地哈希向量索引。向量索引保存在 `chunk_vectors` 表，用于补充中文长问题、近义主题和跨文章主题检索。

## 一键刷新

```bash
kb refresh --dry-run
kb refresh
```

`refresh` 是日常维护入口，会自动完成：

- 扫描 `~/Downloads/微信公众号`
- 按内容哈希跳过重复文章
- 导入新增文章
- 重建全文索引和本地向量索引
- 更新人物、事件、地点卡
- 生成专题研究包
- 生成微信公众号写作工作流
- 标记重点研究卡
- 同步到 iCloud Obsidian

## 检索

```bash
kb search "上海民盟 主题教育 参政履职" --top-k 20
```

## 问答

```bash
kb ask "上海民盟有哪些盟史资源？" --top-k 10
```

`ask` 会先检索本地 raw 原文，再输出带 `[S1]` 来源编号的抽取式回答，避免凭记忆回答。

## 民盟研究助手

```bash
kb assistant --install
kb assistant "五一口号在民盟史上的意义" --mode history --save
kb assistant "上海民盟盟史资源有哪些" --mode history --save
kb assistant "参政议政写作素材" --mode policy --save
kb assistant "主题教育基层落实机制" --mode theme --save
kb assistant "上海民盟微信公众号人物采访写法" --mode writing --save
```

`assistant` 是日常使用入口。它会按任务自动或手动切换为：

- `history`：民盟史、上海民盟地方史、人物事件地点研究。
- `policy`：参政议政、提案、社情民意、调研素材。
- `theme`：主题教育、思想建设、基层落实机制。
- `writing`：上海民盟微信公众号体例写作辅助。
- `research`：综合资料梳理。

输出包含初步判断、处理规则、主要来源、证据摘录、时间线线索、人物组织地点线索、可转化成果和待核实清单。加 `--save` 会写入 `wiki/研究助手/`，再通过 `kb obsidian-sync` 同步到 Obsidian。

## 盟参首席参谋

`kb staff` 是面向日常工作的固定参谋入口，对应聊天窗口里的 `/稿 /史 /题 /核`。

```bash
kb staff draft "80周年主委讲话" --top-k 12
kb staff history "沈钧儒" --top-k 12
kb staff topic "午间盟史课堂：费孝通与江村" --top-k 20
kb staff check "这里粘贴需要核稿的正文"
kb staff check --file ~/Desktop/draft.txt
```

如果当前 shell 里没有全局 `kb` 命令，可在项目目录使用 `.venv/bin/kb staff ...`。

四个模式统一输出：

- 结论：先判断当前材料能不能支撑写作、研究或放行。
- 素材：列出同题历史稿、证据摘录、既有卡片或近似篇目，并带 `[S]` 来源和 raw 原文路径。
- 风险提示：提示口径、史实争议、黑名单命中和 `[待核]` 项。

当前盟参种子库包括：

- `index/formulations.jsonl`：口径库。
- `index/blacklist.csv`：错误提法、敏感史实、核心人物错字。
- `index/entities/*.jsonl`：人物、机构、事件、地点种子实体。

需要保存一次盟参输出时，加 `--save`，系统会写入 `wiki/研究助手/`。

## 微信公众号语料库建设

```bash
kb corpus
kb corpus-audit
kb corpus-style
```

如果当前 shell 里没有全局 `kb` 命令，可在项目目录使用 `.venv/bin/kb corpus`。

`corpus` 会基于当前 SQLite 中的微信公众号文章生成：

- `index/corpus/article_labels.jsonl`：9368 篇文章的账号、年份、类型、主题词、人物、是否文史类、是否写作样本等标签。
- `index/corpus/article_types.json`：文章分类体系。
- `wiki/研究助手/微信公众号语料库体检报告.md`：全库体检。
- `wiki/研究助手/微信公众号文章分类体系.md`：分类说明。
- `wiki/研究助手/微信公众号分类质量诊断报告.md`：分类边界问题和疑似误判线索。
- `wiki/研究助手/上海民盟2023年以来写作样本库.md`：近期上海民盟写作体例样本候选。
- `wiki/研究助手/微信公众号文史盟史文章专题库.md`：文史/盟史文章候选库。

`corpus-audit` 会生成分类抽检材料：

- `index/corpus/classification_review.csv`：人工校订用抽检表。
- `wiki/研究助手/微信公众号文章分类抽检表.md`：按类型、低置信、其他/待判汇总的抽检清单。
- `wiki/研究助手/微信公众号语料库人工校订说明.md`：校订口径和回写流程。

`corpus-style` 会生成写作和研究入口：

- `wiki/研究助手/上海民盟微信公众号分体裁写作模板.md`：按体裁沉淀标题、导语、结构和风险提示。
- `wiki/研究助手/微信公众号文史盟史研究入口清单.md`：文史/盟史候选文章的人物、主题和来源入口。

这一步不修改微信公众号原文，也不把 SQLite 数据库提交到 GitHub。

## 自动编译 wiki 页面

```bash
kb compile --topic "上海民盟盟史资源" --page-type topic --top-k 12
kb compile --topic "主题教育基层落实机制" --page-type topic --top-k 12
```

生成的页面会写入 `wiki/`，并登记到 SQLite 的 `wiki_pages` 和 `wiki_sources`。

## 生成人物/事件/地点卡

```bash
kb build-cards --set all --top-k 8
kb build-cards --set people --limit 5
kb build-cards --set shanghai-history-people --top-k 15
```

预置卡片包括民盟核心历史人物、上海民盟历史人物、关键事件和传统教育相关地点。生成后可用 `kb obsidian-sync` 同步到 Obsidian。

## 研究卡校订状态

```bash
kb curate-cards
```

重点卡会标记为：

- `review_status: 重点待校订`
- `priority_card: true`

默认重点对象包括张澜、沈钧儒、黄炎培、史良、李公朴、闻一多、陶行知、费孝通、钱伟长，五一口号、旧政协、新政协、民盟一届二中全会、民盟一届三中全会、李闻事件，周公馆、虹桥疗养院、上海清华同学会、陶行知纪念馆、钱伟长纪念馆、上海市档案馆。

## 专题研究包

```bash
kb build-packs
```

当前生成 6 个专题包：

- 民盟与五一口号
- 民盟与人民政协
- 上海民盟组织建立与早期发展
- 上海民盟传统教育基地
- 民盟先贤与上海
- 参政议政写作素材库

## 微信公众号写作工作流

```bash
kb build-writing-workflows
```

当前生成 5 类写作工作流：

- 活动会议报道
- 人物采访人物风采
- 文史纪念文章
- 参政议政报道
- 主题教育报道

## 导出

```bash
kb export --title "民盟与五一口号" --format all
kb export --path wiki/人物/沈钧儒.md --format markdown --format docx
```

支持导出：

- Markdown：`exports/markdown`
- Word：`exports/docx`
- PDF：`exports/pdf`

## 已实现

- `kb init`
- `kb scan`
- `kb import`
- `kb check`
- `kb obsidian-sync`
- `kb log`
- `kb index`（SQLite FTS5 + 本地向量索引）
- `kb refresh`（一键更新）
- `kb search`（SQLite FTS5 + 本地向量补充）
- `kb ask`（带来源引用的抽取式问答）
- `kb assistant`（民盟研究助手入口）
- `kb staff`（盟参首席参谋入口：/稿 /史 /题 /核）
- `kb corpus`（微信公众号语料库体检、分类标签和样本库）
- `kb compile`（自动编译带来源 wiki 页面）
- `kb build-cards`（生成人物/事件/地点卡）
- `kb curate-cards`（标记重点待校订卡）
- `kb build-packs`（生成专题研究包）
- `kb build-writing-workflows`（生成公众号写作工作流）
- `kb export`（导出 Markdown/Word/PDF）

## 数据库

schema 位于：

```text
schema.sql
```

主要表：

- `articles`
- `article_chunks`
- `entities`
- `article_entities`
- `wiki_pages`
- `wiki_sources`
- `chunk_vectors`
- `operations_log`

## 下一阶段建议

1. 扩展实体抽取，把人物、组织、地点、会议、事件自动入库。
2. 把重点研究卡从“待校订”升级为“已校订”。
3. 把重要专题从“摘录式初稿”升级为“人工校订研究报告”。
4. 后续如需要更强检索，可替换为专业 embedding 模型。
