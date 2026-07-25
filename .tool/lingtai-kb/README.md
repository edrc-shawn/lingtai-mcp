# 灵台 MCP Server（Lingtai Knowledge Base MCP Server）

> **灵识 = 灵台管线上的认知层** — 基于 Obsidian 知识库的 MCP Server，提供语义搜索、知识图谱、记忆系统、联网检索等能力。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP Protocol](https://img.shields.io/badge/MCP-2024--11--05-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)

---

## 功能概览

灵台 MCP Server 将 Obsidian 知识库通过 [Model Context Protocol](https://modelcontextprotocol.io) 暴露为 AI 可调用的工具集，核心能力分 6 域：

| 能力域 | 工具数 | 功能 |
|--------|--------|------|
| **知识库搜索** | 12 | 语义搜索、图扩散、热度分析、知识复利、缺口检测 |
| **灵识记忆系统** | 12 | 用户画像、长期记忆、观察归纳、记忆毕业/归档 |
| **内容创作** | 4 | 作品发布（公众号/小红书/抖音/B站）、内容注册表 |
| **原料提炼** | 4 | 零 LLM 原料预检、快速提炼、状态追踪 |
| **宏操作** | 6 | 知识检索宏、提炼宏、会话收尾、演化、波及分析 |
| **系统工具** | 8 | 索引重建、健康体检、联网搜索、Token 统计、跨端协同 |

总计 **68 个 MCP 工具**，全部带有 `readOnlyHint` / `destructiveHint` 标注，客户端可做细粒度授权。

---

## 快速开始

### 前置条件

- Python 3.10+
- 一个 Obsidian 知识库目录（作为数据源）
- （可选）[Tavily](https://tavily.com) 或 [AnySearch](https://anysearch.com) API Key — 用于联网搜索

### MCP Server 启动

将以下配置添加到你的 MCP 客户端（Claude Desktop / Cursor / VS Code / WorkBuddy）：

```json
{
  "mcpServers": {
    "lingtai-kb": {
      "type": "stdio",
      "command": "python",
      "args": ["path/to/lingtai/.tool/lingtai-kb/mcp_server.py"],
      "env": {
        "LINGTAI_VAULT": "path/to/your/vault"
      }
    }
  }
}
```

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `LINGTAI_VAULT` | 推荐 | Obsidian 知识库根路径 |
| `TAVILY_API_KEY` | 可选 | Tavily 搜索密钥 |
| `ANYSEARCH_API_KEY` | 可选 | AnySearch 搜索密钥 |
| `LINGTAI_CLIENT_ID` | 可选 | 端标识（多端场景区分） |

完整变量列表见 [`.env.example`](.env.example)。

### Python 直接调用

```python
import sys
sys.path.insert(0, 'path/to/.tool/lingtai-kb')

from memory_engine import MemoryEngine
from perception import PerceptionTools

# 查询知识
engine = MemoryEngine()
result = engine.query("关键词")

# 注入相关知识点
tools = PerceptionTools()
result = tools.inject("关键词")
```

---

## 架构

```
灵台完整管线：
原料 → 提炼 → 丹房（知识库）→ 灵识（查询/关联/推理） → 体检 → 内观 → 选题池 → 公众号 → 归档

.tool/lingtai-kb/
├── mcp_server.py          # MCP 入口（委托 router.main）
├── router.py              # JSON-RPC 路由 + main 循环
├── server.py              # LingtaiMCPServer（12 mixin 继承）
├── config.py              # 密钥 + 模型注册表统一入口
├── errors.py              # 标准化错误码
├── schema_validator.py    # Schema 校验器
├── concurrency.py         # 写操作全局锁
├── session_tracker.py     # 工具调用即时落盘日志
├── server_mixins/         # 模块化 mixin（10+ 域）
│   ├── knowledge.py       # 知识库
│   ├── perception.py      # 感知工具
│   ├── observation.py     # 观察引擎
│   ├── memory_bank.py     # 记忆银行
│   ├── macros.py          # 宏工具
│   ├── system.py          # 系统工具
│   ├── output.py          # 作品输出
│   ├── skillopt.py        # 技能进化
│   └── llm.py, kar.py, check_point.py ...
├── memory_bank/           # 记忆持久化引擎
├── skillopt/              # 睡眠进化引擎
├── tests/                 # 单元测试
└── README.md              # 本文件
```

---

## 工具清单（精选）

### 知识库搜索

| 工具 | 说明 |
|------|------|
| `knowledge_search` | 语义搜索 + 锚点匹配 + 图扩散 |
| `knowledge_inject` | 注入相关丹房页到 AI 回复 |
| `knowledge_explore` | 知识图探索（相关/图扩散/主题） |
| `knowledge_heatmap` | 页面热度（入链+出链排序） |
| `knowledge_compound` | 知识复利（共现权重增长） |
| `knowledge_gaps` | 知识缺口检测 |
| `page_create` | 创建新知识页 |
| `page_update` | 更新知识页（追加/替换） |

### 灵识记忆系统

| 工具 | 说明 |
|------|------|
| `lingshi_inject` | 注入 4 层记忆摘要 |
| `memory_write` | 写入经验记忆 |
| `memory_search` | 搜索记忆银行 |
| `memory_consolidate` | 记忆→知识毕业建议 |
| `memory_decay` | 遗忘归档管线 |

### 系统工具

| 工具 | 说明 |
|------|------|
| `context_load` | 加载会话上下文（用户画像+记忆+约束集） |
| `health_inspect` | 全量体检汇总 |
| `system_refresh_index` | 重建丹房索引 |
| `web_search` | 联网搜索（Tavily / AnySearch） |
| `session_end` | 会话收尾宏 |

所有工具均通过 `@tool` 装饰器注册于 `decorators.py`，详见各 mixin 文件。

---

## 安全

- **密钥管理**：所有 API Key 通过环境变量或外部 `api_keys.json` 注入，**不硬编码**
- **路径隔离**：文件操作限定在知识库目录内（`LINGTAI_VAULT`）
- **stdout 专用于协议**：日志、调试信息全部输出到 stderr，不污染 JSON-RPC 流
- **写操作锁定**：并发写操作有全局锁（5s 超时）
- **错误安全**：统一 `ok()` / `fail()` 结构，不暴露内部堆栈

详细安全策略见 [`SECURITY.md`](SECURITY.md)。

---

## 依赖

灵台 MCP Server 使用纯 Python 标准库实现，**无第三方依赖**（`json`, `sys`, `os`, `threading`, `pathlib` 等）。联网搜索通过 HTTP 请求实现，同样使用标准库 `urllib`。

---

## 开发

```bash
git clone https://github.com/edrc-shawn/lingtai.git
cd .tool/lingtai-kb
python run_tests.py
```

贡献指南见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

---

## 协议版本兼容

当前兼容 [MCP 2024-11-05](https://spec.modelcontextprotocol.io) 协议版本。MCP 协议仍在快速演进，关注 [官方更新](https://github.com/modelcontextprotocol/specification) 以保持同步。

---

## 许可证

[MIT](LICENSE) © 2026 耳东日成

---

## 相关资源

- [MCP 官方文档](https://modelcontextprotocol.io)
- [MCP Server 目录](https://github.com/modelcontextprotocol/servers)
- [Obsidian](https://obsidian.md)
- [灵台知识库](https://github.com/edrc-shawn/lingtai)