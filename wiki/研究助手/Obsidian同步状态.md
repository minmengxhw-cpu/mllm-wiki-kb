# Obsidian 同步状态

生成时间：2026-06-19T23:36:59

## 总体判断

- 当前状态：缺失。
- 本检查只核对本机知识库到 Obsidian Vault 的文件一致性；手机端是否完成 iCloud 云端下载，还需以手机 Obsidian 实际可见为准。

## 同步概览

| 项目 | 结果 |
| --- | --- |
| Vault | `/Users/cheer/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki` |
| Vault 存在 | 可用 |
| 源文件 | 157 |
| 参与核对 | 156 |
| 已一致 | 154 |
| 缺失 | 0 |
| 需更新 | 2 |
| 整体状态 | 缺失 |

## 需要处理的文件

| 类型 | 源文件 | 目标文件 |
| --- | --- | --- |
| 需更新 | `wiki/log.md` | `/Users/cheer/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki/00-总索引/操作日志.md` |
| 需更新 | `wiki/研究助手/第一批权威网页入库候选队列.md` | `/Users/cheer/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki/01-研究助手/第一批权威网页入库候选队列.md` |

## 使用建议

1. 若存在缺失或需更新，运行 `kb obsidian-sync --vault /Users/cheer/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki`。
2. 同步后再运行 `kb obsidian-status --vault /Users/cheer/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki --save` 复核。
3. 手机端以打开 Obsidian 后能看到 `01-研究助手/民盟研究助手首页.md` 为最终确认。
