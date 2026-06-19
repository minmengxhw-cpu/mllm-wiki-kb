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

## 核稿硬门

```bash
kb check "这里粘贴待核文稿"
kb check --file ~/Desktop/draft.txt
```

`check` 不带正文时仍检查项目状态；带正文或 `--file` 时作为核稿硬门运行，命中黑名单、高风险口径或缺少来源时返回非 0，便于接入 CI 和正式发稿前预审。

本地 CI 脚本已接入基础检查：运行单元测试、Python 编译检查，并验证 `kb check` 对高风险文稿必须失败。

```bash
bash scripts/ci.sh
```

GitHub Actions 工作流需要具备 `workflow` scope 的 GitHub token 后再启用。

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

同步状态检查：

```bash
kb obsidian-status --vault ~/Documents/Obsidian/MllmWiki
kb obsidian-status --vault ~/Documents/Obsidian/MllmWiki --save
```

同步规则：

- 只同步 `wiki/`
- 更新目标文件时只替换 `KB-GENERATED` 区
- 保留 `HUMAN-NOTES` 区
- 覆盖前会生成 `.bak-时间戳` 备份
- 状态检查只核对系统生成区，不会把人工笔记区误判为冲突
- `--save` 会生成 `wiki/研究助手/Obsidian同步状态.md` 和 `obsidian/vault_manifest.json`

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
- 更新微信公众号语料库体检、分类抽检、写作样本、写作规则卡、文史盟史入口和参政议政素材主题库
- 更新核心人物/事件研究档案
- 更新 Google Drive 外部参考层状态、研究助手首页和系统可用性验收报告
- 标记重点研究卡
- 同步到 iCloud Obsidian
- 更新 Obsidian 同步状态页和同步清单

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

`kb staff` 是面向日常工作的固定参谋入口，对应聊天窗口里的 `/稿 /史 /信 /题 /数 /核`。

```bash
kb staff draft "80周年主委讲话" --top-k 12
kb staff draft "主题教育会议报道" --material "这里粘贴活动材料"
kb staff draft "主题教育会议报道" --file ~/Desktop/material.txt
kb staff history "沈钧儒" --top-k 12
kb staff topic "午间盟史课堂：费孝通与江村" --top-k 20
kb staff info "科技创新人才" --top-k 12
kb staff stats "2025 参政议政" --top-k 12
kb staff check "这里粘贴需要核稿的正文"
kb staff check --file ~/Desktop/draft.txt
kb brief "80周年工作安排" --top-k 10
```

如果当前 shell 里没有全局 `kb` 命令，可在项目目录使用 `.venv/bin/kb staff ...`。

六个模式统一输出：

- 结论：先判断当前材料能不能支撑写作、研究或放行。
- 素材：列出同题历史稿、证据摘录、既有卡片或近似篇目，并带 `[S]` 来源和 raw 原文路径。
- `/稿` 会先判断体裁，并自动带出 `上海民盟微信公众号精选写作样本.md` 中的同体裁样本。
- `/稿 --material/--file` 会按体裁模板生成公众号初稿，并把缺失或无法确认的事实标成 `[待核]`。
- `/信` 会按“问题发现、调研依据、对策建议、履职价值、风险核验”组织参政议政素材。
- `/数` 会基于文章标签库输出账号、年份、体裁、主题和最近样本分布。
- 生成初稿后会分开列出“用户材料核验”和“初稿核验”，便于继续补来源和改稿。
- 风险提示：提示口径、史实争议、黑名单命中和 `[待核]` 项。

当前盟参种子库包括：

- `index/formulations.jsonl`：口径库。
- `index/blacklist.csv`：错误提法、敏感史实、核心人物错字。
- `index/entities/*.jsonl`：人物、机构、事件、地点种子实体。

需要保存一次盟参输出时，加 `--save`，系统会写入 `wiki/研究助手/`。

## 三阶段工作台

当前“盟参”已经形成三阶段入口：

- 第一阶段：实战强化。入口为 `wiki/研究助手/80周年专题工作台.md`、`wiki/研究助手/上海民盟五类写作模板精修版.md`、`wiki/研究助手/80周年口径风险清单.md`、`wiki/研究助手/微信公众号新增与月度维护流程.md`。
- 第二阶段：研究深化。入口为 `wiki/研究助手/盟史研究深化工作台.md`、`wiki/研究助手/上海民盟组织沿革时间线.md`、核心人物/事件研究档案和文史盟史研究入口清单。
- 第三阶段：长期维护。入口为 `wiki/研究助手/微信公众号新增与月度维护流程.md`、`wiki/研究助手/Google Drive分层接入与样本轮换流程.md`、Obsidian 同步状态和系统可用性验收报告。

总入口为 `wiki/研究助手/盟参三阶段总工作台.md`。日常使用时，写稿先看第一阶段，研史先看第二阶段，新增文章和收尾检查按第三阶段执行。

80 周年实战生产系统入口为 `wiki/研究助手/80周年实战生产系统.md`，下设核心材料总表、主委讲话、活动报道、文史纪念、基层组织、主题教育五类素材包，以及样本精读、史实核验、称谓规范、实战演练和月度维护 SOP。

## 静态站与公开边界

远端新增的 `webapp/` 和 `docs/` 可把本地 wiki 编译成 GitHub Pages 静态预览。默认加密模式需要口令：

```bash
KB_PASSPHRASE=自定义口令 python3 webapp/build_static.py
```

如需在私有仓库里生成无口令预览，可使用：

```bash
KB_PUBLIC=1 python3 webapp/build_static.py
```

注意：`KB_PUBLIC=1` 会生成明文 `docs/content.json`。构建脚本会脱敏本机绝对路径，但页面内容仍来自本知识库 wiki，只适合私有仓库或内部预览；公开发布前必须重新审查全文边界。红头文件、内部口径、未公开材料和 Google Drive 工作文件不得进入公开静态包。

## 微信公众号语料库建设

```bash
kb corpus
kb corpus-audit
kb corpus-apply-reviews --dry-run
kb corpus-apply-reviews --save
kb corpus-style
```

如果当前 shell 里没有全局 `kb` 命令，可在项目目录使用 `.venv/bin/kb corpus`。

`corpus` 会基于当前 SQLite 中的微信公众号文章生成：

- `index/corpus/article_labels.jsonl`：9368 篇文章的账号、年份、类型、主题词、人物、是否文史类、是否写作样本等标签。
- `index/corpus/article_types.json`：文章分类体系。
- `wiki/研究助手/微信公众号语料库体检报告.md`：全库体检。
- `wiki/研究助手/微信公众号语料库工作台.md`：全库可用度、账号/年份/体裁覆盖、优先校订 Top 20。
- `wiki/研究助手/微信公众号文章分类体系.md`：分类说明。
- `wiki/研究助手/微信公众号分类质量诊断报告.md`：分类边界问题和疑似误判线索。
- `wiki/研究助手/上海民盟2023年以来写作样本库.md`：近期上海民盟写作体例样本候选。
- `wiki/研究助手/上海民盟微信公众号精选写作样本.md`：按体裁精选的优先模仿样本。
- `wiki/研究助手/上海民盟微信公众号写作风格规则卡.md`：把近期上海民盟写作样本压缩成可执行规则。
- `wiki/研究助手/微信公众号文史盟史文章专题库.md`：文史/盟史文章候选库。
- `wiki/研究助手/微信公众号参政议政素材主题库.md`：参政议政、调研、提案、社情民意素材入口。

`corpus-audit` 会生成分类抽检材料：

- `index/corpus/classification_review.csv`：人工校订用抽检表。
- `index/corpus/classification_priority_review.csv`：优先校订清单，默认列出最该先看的 100 篇。
- `wiki/研究助手/微信公众号文章分类抽检表.md`：按类型、低置信、其他/待判汇总的抽检清单。
- `wiki/研究助手/微信公众号分类优先校订清单.md`：按优先级排序的人工复核入口。
- `wiki/研究助手/微信公众号语料库人工校订说明.md`：校订口径和回写流程。

`corpus-apply-reviews` 会读取 `classification_review.csv` 和 `classification_priority_review.csv` 中已经填写的人工校订结果；只有 `review_result=错误` 且 `suggested_type` 合法时，才会回写 `article_labels.jsonl` 的主类型。运行 `corpus-audit` 重新生成抽检表时，会保留同一文章已填写的人工校订列。


语料库精修一期新增两个日常入口：`wiki/研究助手/微信公众号语料库精修一期工作台.md` 用于查看分类精修进度，`wiki/研究助手/上海民盟微信公众号标杆样本定稿表.md` 用于按体裁选取优先模仿样本。

`corpus-style` 会生成写作和研究入口：

- `wiki/研究助手/上海民盟微信公众号分体裁写作模板.md`：按体裁沉淀标题、导语、结构和风险提示。
- `wiki/研究助手/上海民盟微信公众号写作风格规则卡.md`：收到材料后优先参考的体裁判断和写作规则。
- `wiki/研究助手/微信公众号文史盟史研究入口清单.md`：文史/盟史候选文章的人物、主题和来源入口。
- `wiki/研究助手/微信公众号参政议政素材主题库.md`：供 `/信` 和参政议政报道调用的素材入口。

这一步不修改微信公众号原文，也不把 SQLite 数据库提交到 GitHub。


## 专业多党合作语料库来源地图

专业扩容层用于把微信公众号写作库升级为多党合作与民盟研究语料库。入口为 `wiki/研究助手/民盟与多党合作专业语料库来源地图.md` 和 `wiki/研究助手/专业语料库分层入库规则.md`；结构化来源登记在 `index/pro_sources/source_map.jsonl`，来源层级规则在 `index/pro_sources/source_types.json`。

来源管理逻辑已独立到 `src/kb/sources.py`；`src/kb/cli.py` 只负责命令入口、保存报告和日志记录。

核稿硬门的黑名单、口径变体、引用缺失检测已独立到 `src/kb/staff_check.py`；`cli.py` 只保留 `/核` 报告拼装和命令入口。

文章导入的解析、清洗、分块、哈希、raw 原文写入和隔离文件逻辑已独立到 `src/kb/ingest.py`；`cli.py` 只保留 `scan/import` 命令流程和数据库写入。

```bash
kb pro-sources --save
```

`pro-sources` 会基于专业来源地图生成首批 P0 入库任务和查询种子，产物为 `index/pro_sources/intake_tasks.jsonl`、`index/pro_sources/query_seeds.jsonl` 和 `wiki/研究助手/专业语料库首批来源入库工作台.md`。它只生成目录任务，不把未核验材料直接写成史实结论。

```bash
kb sources --save
```

`sources` 会生成 `wiki/研究助手/权威公开资料来源体检.md`，用于检查 L1-L4 权威级别、可引用来源和入库判断覆盖情况。

## Google Drive 工作资料层

当前已登记三个 Drive 工作知识库：

- 宣传部知识库
- 研究室知识库
- 办公室知识库

登记文件为 `index/external_sources/google_drive_folders.jsonl`，文件级盘点为 `index/external_sources/google_drive_inventory.jsonl`，说明页为 `wiki/研究助手/Google Drive工作资料接入清单.md`。

这些材料先作为外部参考层，不直接并入微信公众号主语料。后续接入时应先做文件级清单，再区分公开文章、工作材料、研究报告、讲话稿、公文事务和附件；只有确认可作为公开语料复用的内容，才进入微信公众号主语料层。

```bash
kb external-sources
kb external-sources --save
kb guardrails
kb guardrails --save
kb verify
kb verify --save
```

`external-sources` 会汇总 Drive 外部参考层登记情况；加 `--save` 会写入 `wiki/研究助手/Google Drive外部参考层状态.md`。
`guardrails` 会汇总口径库和黑名单；加 `--save` 会写入 `wiki/研究助手/口径风险清单.md`，用于写稿前查看、成稿后核验。
`verify` 会生成系统可用性验收报告；加 `--save` 会写入 `wiki/研究助手/盟参系统可用性验收报告.md`。

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

## 核心人物研究档案

```bash
kb build-research-dossiers --set core-people --top-k 24
kb build-research-dossiers --set core-events --top-k 24
```

研究档案会写入 `wiki/研究助手/核心人物研究档案/` 和 `wiki/研究助手/核心事件研究档案/`，用于把核心人物、核心事件的来源分布、主题线索、时间线线索、证据摘录和待核字段集中到一个研究入口。它不替代 `wiki/人物/`、`wiki/事件/` 下的自动卡，而是面向盟史研究和写作核验。

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
- `kb staff`（盟参首席参谋入口：/稿 /史 /信 /题 /数 /核）
- `kb brief`（领导参阅/工作简报素材包）
- `kb verify`（盟参系统可用性验收报告）
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


盟史事实底座和口径核稿库一期入口：`wiki/研究助手/盟史事实底座升级一期工作台.md`、`wiki/研究助手/口径核稿库增强一期工作台.md`。
