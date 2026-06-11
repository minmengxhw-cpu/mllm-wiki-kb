# Google Drive工作资料接入清单

生成时间：2026-06-11

本页登记已经连接到 Google Drive 的工作资料入口。当前原则是：微信公众号文章仍是主语料层，Drive 工作文件先作为外部参考层，不直接混入公众号公开语料库。

## 已登记文件夹

| 文件夹 | 用途判断 | raw | wiki | 管理手册 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| 宣传部知识库 | 写作、宣传、公众号体例和工作材料外部参考层 | [raw](https://drive.google.com/drive/folders/12xBqeT5-7GTzmdNfdydXaYBEe8xQc19h) | [wiki](https://drive.google.com/drive/folders/1Xl3vZU0ZN9s3epfuzpbR9CNgFWLH_DfU) | [宣传部知识库管理手册.md](https://drive.google.com/file/d/1pgo7ngdS1V4AGaIKCnsZJUC8dwnrcuVu/view?usp=drivesdk) | 已登记，未并入主语料 |
| 研究室知识库 | 参政议政、调研、理论研究和研究报告外部参考层 | [raw](https://drive.google.com/drive/folders/1CutOvCfa1rjXCgjearGHp_-hXA-fZ_Gb) | [wiki](https://drive.google.com/drive/folders/1DQr3ug3guKZMLHVjUvSjjzKaGhEk6cSp) | [研究室知识库管理手册.md](https://drive.google.com/file/d/1Wyut03qHLcN3W7yA_NnlA8oFsileEkMM/view?usp=drivesdk) | 已登记，未并入主语料 |
| 办公室知识库 | 机关运行、公文事务和综合材料外部参考层 | [raw](https://drive.google.com/drive/folders/1bByerPJthcLj4H_uQNNVJ_JbwRFfugBj) | [wiki](https://drive.google.com/drive/folders/1iylvLzG4ce5d7nvjuNJ607OFYYtg9xB4) | [办公室知识库管理手册.md](https://drive.google.com/file/d/17shU48SMRsjpCjE0ar3jODnQgncKMfIM/view?usp=drivesdk) | 已登记，未并入主语料 |

## 分层边界

- 微信公众号公开文章：进入 `data/raw/`、SQLite、FTS、文章分类、写作样本库和盟史研究档案。
- Google Drive 工作材料：先进入外部参考层，只用于人工查阅、材料比对和后续分库规划。
- 内部文件、红头文件、未公开草稿：不进入公开语料库；如需使用，只能作为人工终审依据或单次写作材料。
- 正式输出时，公开公众号语料不能替代内部口径和权威档案。

## 下一步接入流程

1. 先逐层列出三个 Drive 知识库的 raw 和 wiki 文件，形成文件级清单。
2. 按来源类型标注：公众号公开稿、工作材料、研究报告、讲话稿、公文事务、图片或附件。
3. 只把确认属于公开文章或可公开复用的资料纳入微信公众号主语料。
4. 研究报告、讲话稿和内部工作材料另建外部参考索引，供 `/稿`、`/史`、`/核` 在需要时提示“可人工查阅”，不自动当作事实出处。
5. 每次导入前保留原始 Drive 链接、文件名、修改时间和导入决策，避免来源混淆。

## 当前判断

这三个 Drive 知识库已经具备接入条件，但第一阶段不建议直接全量入库。更稳妥的做法是先完成文件级盘点，再按“公开语料”和“工作参考”拆分处理。
