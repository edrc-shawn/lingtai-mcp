# 提炼 Skill

> 保存素材→提炼成知识页→收尾登记，完整流程。

## 流程

### 1. 保存素材
调 `raw_save(content=<内容>)` 存到 `原料/` 目录。
素材未加工前不入丹房。

### 2. 扫描原料
调 `raw_derive(mode="batch")` 或 `knowledge_gaps()` 看哪些原料待提炼。

### 3. 提炼执行
**优先用宏**：调 `refine_quick(content=<正文>, raw_paths=<原料路径列表>)` 一步完成预检→写页→标记→建链。

**不满足时手动分步**：
1. `raw_derive(mode="single", raw_path=<路径>)` 看原料元数据
2. `page_create(title=<标题>, content=<正文>, domain=<域>)` 建页
3. `refine_mark(raw_path=<路径>, target=<丹房页>, summary=<摘要>)` 收尾登记

### 4. 波及检查
调 `ingest_ripple(new_page=<新页路径>)` 预览波及页。
逐条 `page_append_section` 补引用，目标 8-15 页。

### 5. 质检
- 入链 ≥ 2 条（`grep -r slug 丹房/`）
- 出链 ≥ 2 条 wikilink
- 原料标记 `状态: 已提炼`
- 索引重建（`system_refresh_index`）
- Git 干净

## 涉及的子工具

以下工具由本 skill 流程触发，正常情况下不需要单独调：
- `refine_status` — 查提炼状态
- `raw_derive` — 批量/单篇扫描原料
- `ingest_ripple` — 波及分析
- `page_add_link` — 手动建链
- `page_link_suggest` — 自动链接建议
- `page_history` — 页面版本追溯
- `page_compress` — 编译真理压缩
- `nightly_enrich` — 轻量自主丰富

## 惯例

详细流程见 `技能/惯例/提炼流程.md`。