# 体检 Skill

> 知识库健康检查、缺口发现、清理建议。

## 流程

### 1. 全景体检
调 `health_inspect()` 一次看全貌（聚合缺口+热度+规则+反思+对账）。

### 2. 看缺口
调 `knowledge_gaps()` 看待提炼原料。
调 `lifecycle_scan()` 看可清理/可降级的陈旧页。

### 3. 看热度
调 `knowledge_heatmap()` 看哪些页面最活跃。
调 `knowledge_compound()` 看哪些知识边最常被共现查询。

### 4. 跨域关联
调 `concept_collide(min_similarity=0.6, max_similarity=0.75)` 看意外关联。

### 5. 反思
调 `observation_reflect()` 全量反思五检。

### 6. 标记缺口
调 `health_ledger(action="read")` 读对账面板。
调 `health_ledger(action="close", gap=<缺口名>)` 关闭已修缺口。

## 涉及的子工具

以下工具由本 skill 流程触发，正常情况下不需要单独调：
- `knowledge_quality_check` — 检索质量基准检测
- `knowledge_stats` — 知识库统计
- `observation_reflect` — 反思五检
- `lifecycle_scan` — 生命周期扫描
- `knowledge_gaps` — 知识缺口
- `knowledge_heatmap` — 知识热度
- `knowledge_compound` — 知识复利