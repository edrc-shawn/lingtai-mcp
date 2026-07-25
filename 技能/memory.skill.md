# 记忆操作 Skill

> 记东西、查记忆、管理记忆银行。

## 流程

### 1. 检测记忆信号
用户说"记住""以后注意""我偏好""不对"时，先调 `detect_memory_signal(text=<用户原话>)`。
`is_signal=True` 则按返回的 `source_type` 决定写入方式。

### 2. 写记忆
调 `memory_write(content=<内容>, tags=<标签>)`。
- 纠正/指令 → `source_type="correction"`，直写 active
- 偏好 → `source_type="preference"`，pending 待确认
- 教训 → `tags=["lesson"]`

### 3. 查记忆
- 找教训/偏好/纠正：`memory_search(keyword=<关键词>)`
- 找历史会话细节：`episodic_search(keyword=<关键词>)`
- 看近期活动：`episodic_search(days=7)`
- 一次性注入全部记忆层：`lingshi_inject(keyword=<主题>)`

### 4. 记忆毕业
调 `memory_consolidate(min_confidence=0.7)` 看哪些记忆可晋升为知识。
确认后 `memory_link(memory_id=<id>, knowledge_pages=<丹房页>)` 建桥。

### 5. 管理记忆
- 采纳/否决：`memory_feedback(action="adopt"|"reject", memory_id=<id>)`
- 归档：`memory_feedback(action="archive", memory_id=<id>)`

## 涉及的子工具

以下工具由本 skill 流程触发，正常情况下不需要单独调：
- `memory_feedback` — 记忆条目操作
- `memory_link` — 记忆→知识桥
- `memory_consolidate` — 记忆毕业建议
- `memory_snapshot` — 跨会话存档
- `memory_restore` — 恢复快照
- `memory_project_snapshot` — 项目快照
- `memory_feedback` — 记忆反馈