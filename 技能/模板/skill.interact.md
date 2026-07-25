---
标题: skill.interact
日期: 2026-07-02
skill_id: agent_interaction
name: 对话响应与知识注入
description: 当用户提问/提供信息/新会话启动时，执行灵台感知规则链（知识注入·信息保存·关联推荐·三步检索纪律）。对应 AGENTS.md 规则①②③④⑤。
trigger: 每次用户提问/提供信息/新会话启动
---

## 执行阶段

| 阶段 | 时机 | 核心规则 |
|:-----|:-----|:---------|
| 启动协议 | 新会话开始时 | 规则④ 四步启动：读目录图→perception_context→mem_query教训→融合背景 |
| 提问响应 | 用户提问时 | 规则① 知识注入 + 规则③ 关联推荐 + 规则⑤ 三步检索 |
| 信息学习 | 用户提供信息时 | 规则② 自动学习（perception_save） |
| 偏好记录 | 用户透露偏好时 | 规则⑦ 用户画像（user_push）|
| 纠错反馈 | 用户纠正/确认时 | 规则⑨ 用户反馈（user_feedback）+ 规则⑧ 记忆银行（mem_write/feedback）|
| 收尾归档 | 用户说"总结到灵台"/"结束" | 规则⑩ 会话归档（→skill.refine）|

## context_load（上下文加载）

| 来源 | 优先级 | 触发时机 |
|:-----|:-------|:---------|
| `丹房/索引.md` | mandatory | 启动协议 step 1 |
| `perception_context` | mandatory | 启动协议 step 2 |
| `mem_query(tags=["lesson"], min_confidence=0.5)` | mandatory | 启动协议 step 3 |
| `user_profile` | optional | 需要适配用户风格时 |
| 相关丹房页面 | optional | 规则①/③ 触发时按需加载 |

## tool_chain（工具链）

```
启动协议（新会话、仅一次）:
  step 1: 读 AGENTS.md §一（目录地图）
  step 2: perception_context → 加载上次会话摘要
  step 3: mem_query(tags=["lesson"], min_confidence=0.5) → 加载教训
  step 4: 融合背景 → 准备响应

提问响应（每次用户提问）:
  规则① 专指词直达/泛指词升维 → perception_inject(keyword)
  规则⑤ 三步检索或 kar_unified 简化替代
  规则③ perception_recommend → 末尾推荐相关页面

信息学习（用户提供具体事实时）:
  判断：是否为具体事实（日期/人名/项目/数字/决策）
  → 是：perception_save(content, category, source)
  → 否：不学习（模糊观点/闲聊/重复）

纠错反馈（用户纠正时）:
  mem_write(content, source="user_correction", tags=["lesson", ...])
  user_feedback(what, correction) → 画像学习纠正模式
  如已存在同类教训：mem_feedback(memory_id, action="adopt") → 增强置信度

收尾归档（用户触发）:
  → 转 skill.refine（对话归档模式）
```

## quality_gate（质量门控）

| 门控 | 条件 | 动作 |
|:-----|:-----|:-----|
| 检索纪律 | 信息类问题必须三步检索 | 缺一步即违规，0 结果须附带记录 |
| 检索违规 | 连续 2 次同类违规 | 强制先输出检索记录再作答 |
| 学习判断 | 模糊观点/闲聊/重复 | 不保存 |
| 教训毕业 | 同一 pattern ≥3 次或用户确认 | 写入 AGENTS.md + mem_feedback adopt |
| 频率控制 | 长对话每 3-5 轮自检一次 | 避免频繁无谓调用 |

## 使用方式

```
新会话启动：
  → 自动执行启动协议（规则④）
  → 用户提问 → 规则①+⑤
  → 用户提供信息 → 规则②

用户说"不对"：
  → 规则⑧ mem_write + 规则⑨ user_feedback

用户说"总结到灵台"：
  → 转 skill.refine
```
