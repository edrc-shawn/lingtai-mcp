# 系统操作 Skill

> 会话收尾、索引重建、工具查询。

## 流程

### 1. 会话收尾（必做）
调 `session_end(session_start=<时间>, work_imprint=<摘要>)`。
不调 = 画像和记忆不更新。

### 2. 重建索引
调 `system_refresh_index()` 增量重建丹房索引。

### 3. 提 Git
```bash
cd /c/Obsidian仓库/edrc/灵台
git add <文件>
git commit -m "前缀: 描述"
git push
```
commit 前缀：`feat:`(新功能) `fix:`(修 bug) `refine:`(提炼/规则改进) `docs:`(文档)

### 4. 工具用法查询
调 `system_sop(tool=<工具名>)` 查具体工具 SOP。

## 涉及的子工具

以下工具由本 skill 流程触发，正常情况下不需要单独调：
- `sys_reload` — 热重载 MCP
- `system_sop` — 工具 SOP
- `skillopt_*` (6个) — 睡眠进化
- `topic_match` — 热点匹配
- `output_list` / `output_publish` — 作品发布
- `user_feedback` / `user_push` — 用户反馈
- `agent_recommend` / `agent_feedback` / `agent_skills` — 技能推荐
- `external_tool_recommend` — 外部工具推荐