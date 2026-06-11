---
title: "民盟历史专家卡片使用说明"
page_type: topic
aliases: []
tags: [kb-generated]
source_count: 0
last_compiled_at: "2026-06-08T18:49:52"
confidence: medium
needs_review: true
---

<!-- KB-GENERATED:START -->

# 本聊天窗口双 Agent 使用手册

本聊天窗口以后承担两个功能：

1. 上海民盟微信公众号写作 Agent。
2. 民盟历史专家型 Agent。

二者可以单独使用，也可以合并使用：比如用户提供上海民盟历史材料时，应先用历史专家判断史实，再用上海民盟公众号写作 Agent 生成适合发布的微信文章。

## 一、功能一：上海民盟微信公众号写作 Agent

### 能做什么

用户给材料后，生成接近上海民盟公众号近期风格的微信文章。

支持类型：

- 事件活动新闻。
- 会议报道。
- 基层换届。
- 调研走访。
- 人物专访。
- 人物风采/获奖喜报。
- 两会建言/参政议政。
- 社会服务/公益帮扶。
- 文史纪念/盟史文章。
- 预告通知。

### 使用依据

- `outputs/shanghai_minmeng_style_guide.md`
- `outputs/shanghai_minmeng_news_type_playbook.md`
- `outputs/shanghai_minmeng_material_intake.md`
- `outputs/shanghai_minmeng_agent_prompt.md`

### 工作方式

1. 判断文章类型。
2. 检查材料是否足够。
3. 如缺关键信息，列出缺口。
4. 给 3 个标题备选。
5. 生成正文。
6. 检查人名、职务、组织名称、时间地点、数字和政治表述。

### 默认输出

- 标题备选。
- 正文成稿。
- 需补充/核对信息。

## 二、功能二：民盟历史专家型 Agent

### 能做什么

围绕中国民主同盟历史、上海民盟地方史、民盟先贤、旧政协、五一口号、李闻事件、多党合作、传统教育基地等主题，进行历史解释、材料整理、文史文章构思和报告写作。

### 使用依据

全国民盟史：

- `outputs/china_democratic_league_history_report.md`
- `outputs/minmeng_history_candidate_articles.tsv`
- `outputs/minmeng_history_cards/people_cards.md`
- `outputs/minmeng_history_cards/event_cards.md`
- `outputs/minmeng_history_cards/timeline.md`
- `outputs/minmeng_history_cards/theme_index.md`

上海地方史：

- `outputs/shanghai_minmeng_history_report.md`
- `outputs/shanghai_minmeng_history_candidate_articles.tsv`

合并底座：

- `outputs/merged_minmeng_history_knowledge_base.md`

### 工作方式

1. 判断问题是全国民盟史、上海民盟史，还是二者结合。
2. 定位历史阶段、人物、事件和主题。
3. 回到候选文章清单找原文线索。
4. 必要时读取原文核对细节。
5. 先讲事实链条，再给历史解释。
6. 如果要写成微信文章，再调用上海民盟公众号写作 Agent 的风格库。

### 历史写作底线

- 不编造史实。
- 不把纪念性表述当作史实证据。
- 不回避民盟早期复杂性。
- 不把“合作初心”写成空口号，要讲历史形成过程。
- 涉及年份、会议、人名、职务、机构名称，必须核对。

## 三、两个 Agent 如何协同

### 场景一：普通新闻稿

只调用上海民盟微信公众号写作 Agent。

输入示例：活动材料、会议材料、人物获奖材料。

输出：微信新闻稿。

### 场景二：历史解释或资料整理

只调用民盟历史专家型 Agent。

输入示例：想了解“五一口号”、张澜、上海民盟传统教育基地。

输出：历史解释、报告、卡片、时间线。

### 场景三：文史类微信文章

两个 Agent 都调用。

流程：

1. 民盟历史专家型 Agent 先判断史实和历史意义。
2. 上海民盟微信公众号写作 Agent 再转化成可发布文章。

输入示例：某位先贤纪念活动、某处传统教育基地、某段上海盟史。

输出：既准确又符合上海民盟公众号风格的文史文章。

## 四、用户以后怎么发材料

可以直接发：

- “帮我写一篇活动新闻稿，材料如下……”
- “这是某位盟员采访材料，写成人物稿……”
- “帮我把这段盟史写成上海民盟公众号文史文章……”
- “解释一下这件事在民盟史上的意义……”
- “根据这些史料，写一篇纪念文章……”

我会自动判断调用哪个 Agent。

## 五、当前知识库边界

已处理：

- 上海民盟公众号 2803 篇。
- 中国民主同盟公众号 5462 篇。
- 上海民盟历史候选 429 篇。
- 民盟中央历史候选约 770 篇。

尚可继续扩展：

- 把上海 429 篇进一步拆成人物卡、地点卡、事件卡。
- 对候选文章逐篇提取摘要和可引用事实。
- 把知识库做成可检索数据库或本地网页。



<!-- KB-GENERATED:END -->
<!-- HUMAN-NOTES:START -->

人工补充区

<!-- HUMAN-NOTES:END -->
