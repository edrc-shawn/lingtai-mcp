# 知识检索 Skill

> 用户问"什么是X""解释X""X和Y的区别"时，按此流程执行。

## 流程

### 1. 先查记忆
调 `lingshi_inject(keyword=<主题>)` 获取画像+教训+记忆上下文。
如果用户之前问过类似问题，不再重复回答。

### 2. 再查知识库
调 `knowledge_synthesize(keyword=<主题>)` 一步完成检索+合成+差距分析。

### 3. 需要原始页面时
如果 knowledge_synthesize 的合成回答不够，调 `knowledge_search(keyword=<主题>)` 获取原始页面列表。

### 4. 搜非丹房文件
如果搜的是原料/技能/作品文件，调 `fulltext_search(keyword=<主题>)`。

### 5. 知识库无答案时
调 `web_search(keyword=<主题>)` 联网搜索。

### 6. 问题模糊时
调 `question_dissolve(question=<原话>)` 消解方向，确认后再搜。

## 涉及的子工具

以下工具由本 skill 流程触发，正常情况下不需要单独调：
- `knowledge_explore` — 不确定关键词时"逛"知识图谱
- `knowledge_inject` — 注入片段到上下文（有 token 上限）
- `knowledge_gaps` — 查看知识缺口
- `knowledge_compound` — 查看共现权重
- `knowledge_heatmap` — 查看页面热度
- `knowledge_stats` — 知识库统计

## 特殊场景

| 场景 | 怎么做 |
|------|--------|
| 用户问"这个和那个有什么区别" | `knowledge_synthesize` 带对比参数 |
| 用户问"最近有什么新东西" | `knowledge_heatmap` 看热度变化 |
| 用户问"我还缺什么" | `knowledge_gaps` 看缺口 |
| 用户不确定想问什么 | `knowledge_explore` 发散探索