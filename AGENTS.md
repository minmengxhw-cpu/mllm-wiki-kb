# Mllm Wiki KB Agent Guide

本项目是“盟参”民盟语料库首席参谋系统的本地工作台。默认用现有知识库和 SQLite 索引，不另建平行资料库。

## 盟参路由

当用户在本聊天窗口输入下列口令时，按对应本地命令执行，并把结果转述给用户：

- `/稿 主题`：运行 `kb staff draft "主题"`，输出文稿素材包；用户贴材料时运行 `kb staff draft "主题" --material "材料"`，长材料用 `--file`。
- `/史 人物/事件/机构/地点`：运行 `kb staff history "主题"`，输出史实卡片和研究入口。
- `/题 选题`：运行 `kb staff topic "选题"`，输出查重报告和差异化角度。
- `/信 主题`：运行 `kb staff info "主题"`，输出统战信息/参政议政素材包。
- `/数 主题`：运行 `kb staff stats "主题"`，输出账号、年份、体裁和主题分布。
- `/核 文稿全文`：运行 `kb staff check "文稿全文"`；长文优先保存成临时文本后用 `kb staff check --file 文件路径`。

需要保存到 Obsidian 工作流时，给命令加 `--save`，再按需运行 `kb obsidian-sync`。
需要确认电脑端 Obsidian/iCloud Vault 是否已同步到位时，运行 `kb obsidian-status --save`。

## 输出规则

- 所有事实性内容必须带来源编号、raw 原文路径或明确标注 `[待核]`。
- 统一采用三段式：结论、素材、风险提示。
- 红头文件、内部口径和人工终审优先级高于本公开语料库。
- 微信公众号原文只作为公开表述层素材，不替代内部文件和权威档案。

## 数据维护

- 文章增量用 `kb refresh`。
- 同步状态用 `kb obsidian-status`；它只核对系统生成区，保留人工笔记区。
- 微信公众号语料库体检、分类标签和样本库更新用 `kb corpus`。
- 微信公众号文章分类抽检和人工校订表更新用 `kb corpus-audit`。
- 上海民盟分体裁写作模板和文史盟史研究入口更新用 `kb corpus-style`。
- Google Drive 外部参考层状态查看用 `kb external-sources`；需要写入 wiki 时用 `kb external-sources --save`。
- 专业多党合作来源地图和首批入库任务用 `kb pro-sources --save`，产物在 `index/pro_sources/intake_tasks.jsonl`、`index/pro_sources/query_seeds.jsonl` 和 `wiki/研究助手/专业语料库首批来源入库工作台.md`。
- 权威公开资料来源分级体检用 `kb sources --save`，用于确认 L1-L4 覆盖、可引用来源和入库边界。
- 系统可用性验收用 `kb verify`；需要写入 wiki 时用 `kb verify --save`。
- 核心人物研究档案更新用 `kb build-research-dossiers --set core-people`；核心事件研究档案更新用 `kb build-research-dossiers --set core-events`。
- 盟参种子库位于 `index/formulations.jsonl`、`index/blacklist.csv`、`index/entities/*.jsonl`。
- 种子库先保证可拦截高风险问题，再逐步扩展，不把未校订条目写成定论。
- 来源管理纯逻辑位于 `src/kb/sources.py`；新增来源分级、任务生成和来源体检时优先改该模块，不继续膨胀 `src/kb/cli.py`。
- 核稿硬门纯逻辑位于 `src/kb/staff_check.py`；新增黑名单匹配、口径变体、引用缺失和严重度规则时优先改该模块。
- 本地 CI 脚本 `scripts/ci.sh` 会运行单元测试、编译检查和 `kb check` 核稿硬门验证；GitHub Actions 工作流需具备 `workflow` scope 的 token 后再启用。
- 静态站构建优先使用加密模式 `KB_PASSPHRASE=... python3 webapp/build_static.py`；`KB_PUBLIC=1` 只用于私有仓库或内部预览，生成前后必须确认本机绝对路径已脱敏，且 Google Drive 工作材料、内部文件和未公开口径未进入公开包。

## 语料库优先工作

当用户要求“先把微信文章做实”“语料库体检”“文章分类”“写作样本库”时，优先运行 `kb corpus`，并基于以下产物回答：

- `wiki/研究助手/盟参三阶段总工作台.md`
- `wiki/研究助手/80周年实战生产系统.md`
- `wiki/研究助手/80周年核心材料总表.md`
- `wiki/研究助手/80周年主委讲话生产包.md`
- `wiki/研究助手/80周年活动报道素材包.md`
- `wiki/研究助手/80周年文史纪念素材包.md`
- `wiki/研究助手/80周年基层组织活动报道素材包.md`
- `wiki/研究助手/80周年主题教育素材包.md`
- `wiki/研究助手/上海民盟五类写作样本精读报告.md`
- `wiki/研究助手/80周年史实核验清单.md`
- `wiki/研究助手/核心人物称谓与组织名称规范表.md`
- `wiki/研究助手/盟参实战演练报告.md`
- `wiki/研究助手/月度维护SOP.md`
- `wiki/研究助手/80周年专题工作台.md`
- `wiki/研究助手/上海民盟五类写作模板精修版.md`
- `wiki/研究助手/80周年口径风险清单.md`
- `wiki/研究助手/盟史研究深化工作台.md`
- `wiki/研究助手/上海民盟组织沿革时间线.md`
- `wiki/研究助手/微信公众号新增与月度维护流程.md`
- `wiki/研究助手/Google Drive分层接入与样本轮换流程.md`
- `index/corpus/article_labels.jsonl`
- `index/corpus/classification_review.csv`
- `index/corpus/classification_priority_review.csv`
- `wiki/研究助手/微信公众号语料库体检报告.md`
- `wiki/研究助手/微信公众号语料库工作台.md`
- `wiki/研究助手/微信公众号语料库精修一期工作台.md`
- `wiki/研究助手/上海民盟微信公众号标杆样本定稿表.md`
- `wiki/研究助手/盟史事实底座升级一期工作台.md`
- `wiki/研究助手/口径核稿库增强一期工作台.md`
- `wiki/研究助手/民盟与多党合作专业语料库来源地图.md`
- `wiki/研究助手/专业语料库分层入库规则.md`
- `wiki/研究助手/专业语料库首批来源入库工作台.md`
- `wiki/研究助手/权威公开资料来源体检.md`
- `index/pro_sources/source_map.jsonl`
- `index/pro_sources/intake_tasks.jsonl`
- `index/pro_sources/query_seeds.jsonl`
- `wiki/研究助手/微信公众号分类质量诊断报告.md`
- `wiki/研究助手/微信公众号文章分类抽检表.md`
- `wiki/研究助手/微信公众号分类优先校订清单.md`
- `wiki/研究助手/上海民盟微信公众号分体裁写作模板.md`
- `wiki/研究助手/上海民盟微信公众号精选写作样本.md`
- `wiki/研究助手/上海民盟微信公众号写作风格规则卡.md`
- `wiki/研究助手/上海民盟2023年以来写作样本库.md`
- `wiki/研究助手/微信公众号文史盟史文章专题库.md`
- `wiki/研究助手/微信公众号文史盟史研究入口清单.md`
- `wiki/研究助手/微信公众号参政议政素材主题库.md`
- `wiki/研究助手/核心人物研究档案/索引.md`
- `wiki/研究助手/核心事件研究档案/索引.md`
- `wiki/研究助手/Google Drive工作资料接入清单.md`
- `wiki/研究助手/Google Drive外部参考层状态.md`
- `wiki/研究助手/盟参系统可用性验收报告.md`

Google Drive 工作材料暂作为外部参考层，不直接混入微信公众号主语料层。已登记入口见 `index/external_sources/google_drive_folders.jsonl`；处理时先做文件级清单，再判断是否可进入公开公众号语料。
