---
标题: 灵台 MCP 工具设计准则
日期: 2026-07-09
skill_id: mcp_design
name: mcp-design
description: 当设计、审查或重构灵台 MCP 工具（函数签名、返回结构、错误处理、命名、上下文预算）时使用，确保工具对 AI 协作者高可用、低 token 消耗、且端无关
trigger: 新增/修改 MCP tool、会话启动协议调整、工具评审、上下文预算优化
---

# 灵台 MCP 工具设计准则

> 来源：吸收 `技能/外部参考/awesome-claude-skills/mcp-builder` 的工程纪律（Agent-Centric 设计、上下文预算、可操作错误、评估驱动）
> 配套：`技能/模板/_skill规范.md`（D1–D5 工程纪律）/ `技能/capability-map.md`（端无关与能力协商）/ `AGENTS.md` 规则⑰
> 适用范围：灵台 `.tool/lingtai-kb/` 下所有 MCP 工具的增改查评审

---

## 执行阶段（时机 / 核心规则）

### 阶段一 · 设计前（工作流而非端点）

- **时机**：决定"要不要加这个工具 / 这个工具长啥样"时
- **核心规则**：
  - **合并完整任务**：不要只包裹单个 API 调用。若一个常见工作流需要"查 + 建"，合成一个工具（如原 `observation_list` + `observation_reflect` 常连用 → 评估是否合成）
  - **按人类思考命名**：工具名反映任务而非底层函数（如 `context_load` 而非 `get_cached_profile`）
  - **一致前缀**：同域工具用统一前缀提升可发现性（灵台现有 `perception_*` / `memory_*` / `knowledge_*` / `skillopt_*` 已符合）

### 阶段二 · 实现（上下文预算 + 可操作错误）

- **时机**：写 tool 逻辑与 description、schema 时
- **核心规则**：
  - **高信号返回**：只回高信号数据，不 dump 全量；提供 `concise|detailed` 分级（灵台 `context_load` 已按层返回，未显式分级——待补）
  - **可读标识优先**：返回用人类可读名而非技术 ID（灵台 registry_context 已用中文名）
  - **可操作错误**：错误要教育性——指出下一步（如"参数 source 非法，应为 '对话'|'网页'|'文件'"），不写裸堆栈
  - **输入校验**：schema 写清类型、约束（min/max、regex、枚举）、示例
  - **工具注解**：只读工具标 `readOnlyHint=true`；非破坏标 `destructiveHint=false`
  - **截断兜底**：超长返回截断并标注"（已截断，用 detailed 或分页取余）"，设字符上限

## 不做什么

> 借鉴 dbskill 的边界定义方法：设计准则最容易膨胀成"什么都管"，先划清不管什么。

1. **不替代人工 code review** — 这是设计准则，不是自动化审查工具
2. **不生成未经测试的代码直接部署** — 设计输出是规范，实现需另行测试
3. **不覆盖 AGENTS.md 中已锁定的硬规则** — 工具设计准则服务于硬规则，不推翻
4. **不对非灵台 MCP 工具做约束** — 只管 `.tool/lingtai-kb/` 下的工具，不越界

### 阶段三 · 评审（端无关 + 一致性）

- **时机**：改动前自查
- **核心规则（灵台专用，补充自规则⑰）**：
  - **端无关**：工具返回 `content + modality 偏好 + fallback`，引用能力写 `capability:*`，绝不写具体桌面端名（呼应 capability-map）
  - **描述路由句**：每个工具的 description 写清"何时用、何时不用"，供 AI 协作者路由（对齐 `_skill规范` D1）
  - **一致性**：相似操作返回相似结构（灵台 `_log_*` / `stats` 类统一 JSON 形状）

### 阶段四 · 评估驱动（长期）

- **时机**：工具上线后
- **核心规则**：
  - 早期写 3–5 个真实评估问题（独立 / 只读 / 复杂 / 可验证）
  - 让 agent 反馈驱动迭代（灵台已有 skillopt 夜间回验机制，可复用其范式）

---

## tool_chain（设计自查顺序）

1. 读 `_skill规范.md` D1–D5，确认工程纪律基线
2. 列该工具的"完整工作流"（不止 API 端点）
3. 定命名 + 前缀，写 description 触发句
4. 设计返回：`content` / 是否需要 `concise-detailed` / 截断上限 / `fallback`（端无关）
5. 写 schema：类型约束 + 枚举 + 示例
6. 写错误：每条异常给"教育性下一步"
7. 标工具注解（read / destructive）
8. 对照下方 `quality_gate` 自查
9. 用 `python -m py_compile` 验语法（灵台已用此法）

---

## quality_gate（质量门控）

| 门 | 条件 | 不通过后果 |
|----|------|-----------|
| G1 工作流完整 | 工具是否让 agent 完成一个任务而非半步 | 被迫多次调用，token 翻倍 |
| G2 上下文预算 | 默认 concise；detailed 显式请求才给 | 长对话被无关数据淹没 |
| G3 错误可操作 | 每条异常含"下一步建议" | agent 卡死重试 |
| G4 端无关 | 返回不含具体桌面端名；能力走 `capability:*` | 换端即失效，违反规则⑰ |
| G5 命名可发现 | 同域统一前缀 + 反映任务 | 工具泛滥难路由 |
| G6 校验完备 | schema 有类型 / 约束 / 示例 | 脏输入崩溃 |
| G7 描述路由 | description 写清何时用 / 不用 | AI 误触发或漏触发 |

---

## 灵台现有工具对照（反向体检基线）

> 下述为吸收本准则后的现状快照，供后续迭代比对。不在此处改代码，仅记录。

- ✅ `context_load`：Workflow 命名好、一致前缀；✅ **已加 `detail=concise|detailed` 分级**（2026-07-09 落地：concise 截断协作者约束集全文到前 600 字、hub 去 backlinks，画像三层/scene/capabilities 不裁）
- ✅ `perception_save`：教育性错误尚可；✅ **`observation_feedback` 已验证天然短（1-2 行观察反馈），弱端无超上下文风险**（D-D 验证结论：2026-07-09）；`skillopt_*` 日志已结构化分条，保持 G2 分条约束
- ✅ `knowledge_search`：三级管线 + 日志自动追加，高信号；✅ 已端无关
- ✅ `skillopt_*`：**关键错误已附教育性"下一步"建议**（2026-07-09 落地：`_ERROR_HINTS` + `_log_err`，覆盖 mine_pattern / write_lessons / auto_adopt / mem_decay 四类阶段错误）
- ✅ 全局：规则⑰ 已强制端无关；capability-map 已定义 modality 契约

---

## 使用方式

| 典型用户 / 协作者指令 | 对应动作 |
|----------------------|---------|
| "给灵台加个 X 工具" | 走阶段一→二→`tool_chain`，过 `quality_gate` 再写代码 |
| "评审这段 MCP 改动" | 按 `quality_gate` G1–G7 逐条对照 |
| "为什么我的工具总被误触发" | 检查 G5 / G7（命名 + description 路由句） |
| "换端后工具失效" | 检查 G4（端无关 / `capability:*`） |
