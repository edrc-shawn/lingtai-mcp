# 灵台 MCP 功能改造计划

> ✅ **Loop 1+2 已完成**（2026-07-04）
> - `knowledge_compound` — 基于 Hebbian 权重的知识复利
> - `knowledge_heatmap` — 页面热度排序
> - `knowledge_gaps` — 独立知识缺口检测
>
> ✅ **Loop 3 已完成**（2026-07-04）
> - `page_link_suggest` — 自动链接建议（标签/同域/关键词）
> - 修复 `analyze()` 中 `find关联`→`find潜在关联` 死代码 bug
>
> ✅ **Loop 4 已完成**（2026-07-04）
> - `output_list` — 列出作品/ 各平台产出物（共24篇）
> - `output_publish` — 从丹房知识生成发布文件（公众号/小红书/抖音/哔哩哔哩）
>
> ✅ **Loop 5 已完成**（2026-07-04）
> - `page_history` — 页面版本追溯（从 oplog.jsonl 提取修改历史）
>
> **全部 5 个 Loop 已完成。三门柱 + 四横刀 39 个工具。**

## 框架：三门柱 + 四横刀

```
三门柱（纵向流水线）
  LLM Wiki:  采集→提炼→存储→检索→注入
  灵识:      观察→记忆→画像→人格注入
  输出:      选择→转换→配图→发布

四横刀（横向跨所有柱子）
  复利:  热度 + 共现 + 涌现 → 推荐
  缺口:  待提炼扫描 + 断裂关联 → 提炼建议
  追溯:  版本 + 来源 + 变更历史
  冲突:  丹房页矛盾检测 → 对账
```

---

## Loop 1：知识复利 + 热度（已有代码，直接暴露）

### 背景
Hebbian 权重引擎已存在（`hebbian_weights.py:77`），追踪页面间共现频率，有 `get_top_co_occurrences()`、`decay()`、`get_stats()`。但**零工具暴露**。

### 动作
1. 新建 MCP 工具 `knowledge_compound`：返回高共现对、活跃边、衰退中边
2. 新建 MCP 工具 `knowledge_heatmap`：每页被查询次数、被注入次数、最近更新时间
3. 将 `hebbian_weights` 挂到 `knowledge_search` 每次调用时自动记录（`on_query` 已在 memory_engine 中调用，但检查是否完整）

### 依赖
- `hebbian_weights.py` → 现有，不修改
- `router.py` → 加 2 个 handler
- `tools.py` → 加 2 个工具定义
- `server_mixins/knowledge.py` → 加 2 个 handler 方法

### 验证
- `knowledge_compound(keyword="含人量")` → 返回与含人量共现最强的 5 个页面
- `knowledge_heatmap()` → 返回 top 10 热门页面 + 零查询页面

---

## Loop 2：独立知识缺口工具（已有代码，拆出）

### 背景
缺口检测在 `reflect_engine.py:104`，埋在体检五检中。返回 raw list，无域分组/优先级排序。

### 动作
1. 新建 MCP 工具 `knowledge_gaps`：参数可选 `domain`、`min_severity`
2. 按域分组输出，附带"添加为原料"的快捷建议
3. `observation_reflect` 保持不动（体检报告仍是完整五检）

### 依赖
- `reflect_engine.py` → 抽出 `_check_knowledge_gaps` 为公共方法
- 无其他组件修改

### 验证
- `knowledge_gaps(domain="07-工具与AI")` → 返回该域下待提炼且丹房无对应条目的原料

---

## Loop 3：自动链接建议（已有代码，暴露）

### 背景
`auto_edge.py:97` 的 `find潜在关联` 和 `get_link_suggestions` 有建链建议，但零工具暴露。

### 动作
1. 新建 MCP 工具 `page_link_suggest`：输入页面路径，返回推荐链接的候选列表

### 依赖
- `auto_edge.py` → 现有
- 理解 `find潜在关联` 的算法：标签重叠 + 同域 + 关键词重叠

### 验证
- `page_link_suggest(path="丹房/00-思考与认知/含人量")` → 返回前 5 条推荐链接 + 原因

---

## Loop 4：输出（新造）

### 背景
作品/ 下有 5 个目录（公众号/抖音/小红书/哔哩哔哩/配图），24 页内容，但 **0 个 MCP 工具** 操作它。

### 动作
1. 新建 MCP 工具 `output_list`：列出作品目录中的产出物
2. 新建 MCP 工具 `output_publish`：从丹房知识 → 选择格式（公众号/小红书/抖音）→ 写入作品/ 对应目录

### 依赖
- 需要定义"发布格式模板"（公众号文章结构、小红书帖子结构）
- 可复用 `耳东配图 SKILL` 的配图逻辑

### 验证
- `output_list()` → 返回作品/ 各目录下文件数
- `output_publish(source="...", format="公众号")` → 在作品/公众号/ 下生成文章

---

## Loop 5：版本追溯（新造）

### 背景
丹房页无版本号，修改历史只有 git log。

### 动作
1. 新建 MCP 工具 `page_history`：输入页面路径，返回修改记录（从 `丹房/日志.md` + `oplog.jsonl` 提取）
2. 新建 MCP 工具 `page_diff`：两个版本间的差异（可省略，git diff 已经够用）

### 依赖
- `丹房/日志.md` 和 `丹房/.meta/oplog.jsonl` → 当前的日志格式已支持
- 需要确保每次 `page_update` 写日志时记录了变更摘要

### 验证
- `page_history(path="丹房/00-思考与认知/含人量")` → 返回该页的修改时间线

---

## 优先级矩阵

| Loop | 能力 | 代码状态 | 可见度 | 用户价值 | 工作量 | 推荐 |
|:----:|------|:--------:|:------:|:--------:|:-----:|:----:|
| 1 | 知识复利+热度 | ✅ 70%已有 | 无工具 | ⭐⭐⭐ | 小 | **下周** |
| 2 | 知识缺口 | ✅ 80%已有 | 埋在体检 | ⭐⭐⭐ | 极小 | **下周** |
| 3 | 自动建链 | ✅ 80%已有 | 无工具 | ⭐⭐ | 小 | 下下周 |
| 4 | 输出 | ❌ 0% | 无 | ⭐⭐⭐⭐ | 中 | 下下周 |
| 5 | 版本追溯 | ⚠️ 50%日志 | 无工具 | ⭐⭐ | 小 | 下下下周 |

**下周先做 Loop 1 + 2**（已有代码，直接暴露，产出最快）。

---

## 工具命名规约

新工具统一映射到三门柱 + 四横刀命名：

```
三门柱
  knowledge_*  → LLM Wiki（已有）
  page_*       → 丹房页管理（已有）
  memory_*     → 灵识记忆银行（已有）
  user_*       → 用户画像（已有）
  lingshi_*    → 统一注入（已有）
  output_*     → 输出（新）

四横刀
  knowledge_compound  → 复利
  knowledge_heatmap   → 热度
  knowledge_gaps      → 缺口
  page_link_suggest   → 自动建链
  page_history        → 追溯
```