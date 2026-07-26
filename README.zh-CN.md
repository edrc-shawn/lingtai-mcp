# 灵台 (Lingtai)

> **一个自我进化的知识管理系统。** 收集原料、提炼知识、演化规则、运行体检，甚至在你睡觉时替你写作和发布内容。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP Protocol](https://img.shields.io/badge/MCP-2024--11--05-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)

---

## 灵台是什么？

灵台是一个三层知识体系，由 Obsidian 知识库、自研 MCP Server 和一套自动化巡更任务组成：

| 层级 | 角色 | 说明 |
|------|------|------|
| **原料** | 待加工输入 | 笔记、文章、灵感 — 等待提炼 |
| **丹房** | 结构化知识 | 加工后的页面，按多个领域组织 |
| **记忆银行** | AI 记忆层 | 用户画像、经验教训、观察记录 |

核心是 **灵识** — 一个 AI 身份，充当认知层，通过调用 MCP 工具来搜索、综合、提炼、演化知识库。任何支持 MCP 的 AI 客户端接入灵台后都能即刻懂你。

---

## 架构

```
原料 → 提炼 → 丹房（知识库）→ 灵识（查询/推理/关联）
                              ↓
                        体检（健康检查）→ 内观自省
                              ↓
                        选题池 → 多平台发布
                              ↓
                        归档
```

### MCP Server

服务端位于 `.tool/lingtai-kb/`，纯 Python 实现，包含 **86 个工具**，覆盖知识搜索、记忆管理、内容发布、原料提炼、系统工具等领域 — **零第三方依赖**。

详见 [`.tool/lingtai-kb/README.md`](.tool/lingtai-kb/README.md) 获取完整工具目录、架构细节和 API 参考。

### 巡更系统

灵台内置巡更框架，支持编排定时知识任务。示例配置：

| 时间 | 任务示例 |
|------|----------|
| 03:00 | SkillOpt 引擎 — 分析使用模式、提议规则优化、运行记忆衰减 |
| 08:00 | 晨间简报 — 收集天气、AI 资讯、选题建议 |
| 18:00 | 每日体检 — 质量检查、反向链接、演化追踪、对账修复 |
| 每周 | 深度归档、编码修复、语义缺口检测 |
| 每月 | 时效性扫描、规则演化建议 |

> **说明：** 巡更任务通过 WorkBuddy 或类似调度器运行。你决定何时跑什么任务 — 系统提供工具，你来定义排程。

---

## 快速开始

### 前置条件

- Python 3.10+
- 一个支持 MCP 的 AI 客户端（Claude Desktop、Cursor、VS Code、WorkBuddy）
- 一个 Obsidian 知识库

### 1. 克隆仓库

```bash
git clone https://cnb.cool/edrc.shawn/lingtai-mcp.git
cd lingtai-mcp
```

### 2. 创建知识目录

灵台要求知识库中有以下目录（不包含在仓库中 — 属于你的个人数据）：

```bash
mkdir -p 丹房 原料 体检 画像 作品 存档 简报
```

### 3. 接入 AI 客户端

将以下配置添加到 MCP 配置中：

```json
{
  "mcpServers": {
    "lingtai-kb": {
      "type": "stdio",
      "command": "python",
      "args": [".tool/lingtai-kb/mcp_server.py"],
      "cwd": "/path/to/lingtai/.tool/lingtai-kb",
      "env": {
        "LINGTAI_VAULT": "/path/to/lingtai"
      }
    }
  }
}
```

启动对话，输入 **"调 context_load"** — 如果返回知识库统计信息，说明已连接成功。

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `LINGTAI_VAULT` | 推荐 | Obsidian 知识库根路径 |
| `TAVILY_API_KEY` | 可选 | 联网搜索 API 密钥 |
| `ANYSEARCH_API_KEY` | 可选 | 备用联网搜索 API 密钥 |
| `LINGTAI_CLIENT_ID` | 可选 | 多端场景的客户端标识 |

---

## 项目结构

```
lingtai-mcp/
├── README.md                    # 英文主文档
├── README.zh-CN.md              # 中文主文档（本文件）
├── LICENSE                      # MIT
├── AGENTS.md                    # AI 操作手册 — 灵识的运行规则
├── AGENTS-appendix.md           # 规则附录 + 工具速查
├── .gitignore                   # 排除个人数据目录
├── .github/                     # CI 配置
├── .mcp.json.example            # MCP 配置模板（复制为 .mcp.json）
├── 入门/                         # 入门文档
├── 技能/                         # 技能库（8 个 Skill + 模板）
└── .tool/
    └── lingtai-kb/              # MCP Server
        ├── README.md            # 服务端文档 + 工具目录
        ├── mcp_server.py        # 入口
        ├── router.py            # JSON-RPC 路由
        ├── server.py            # 核心服务（12 个 Mixin 继承）
        ├── server_mixins/       # 22 个按领域划分的 Mixin 模块
        ├── memory_bank/         # 记忆持久化引擎
        └── skillopt/            # 自我进化引擎
```

> **个人数据目录**（`丹房/`、`原料/`、`体检/`、`画像/`、`作品/`、`存档/`、`简报/`）已通过 `.gitignore` 排除，克隆后本地创建即可。

---

## 核心设计理念

1. **知识 ≠ 记忆。** 结构化知识存在丹房；个人经验存在记忆银行。分层不混淆。

2. **建基础设施优于换模型。** 好的记忆架构、上下文工程和可观测性，远比换一个更新的 LLM 重要。

3. **自我进化。** SkillOpt 引擎分析使用模式，自动提议规则优化。规则从观察 → 教训 → 硬规则逐步毕业。

4. **端无关。** 灵台生产的是知识，不是呈现。任何 AI 客户端都能消费 — 呈现层由客户端负责。

5. **可验证输出。** 每个输出必须附带可验证声明。"看起来没问题"是被禁止的措辞。

---

## 参与贡献

开发指南见 [CONTRIBUTING.md](.tool/lingtai-kb/CONTRIBUTING.md)。

---

## 安全规范

- 所有 API 密钥通过环境变量注入 — **绝不硬编码**
- 文件操作限定在知识库目录内
- stdout 专用于 JSON-RPC 协议；所有日志输出到 stderr
- 写操作使用全局互斥锁，超时 5 秒
- 完整安全策略见 [SECURITY.md](.tool/lingtai-kb/SECURITY.md)

---

## 许可证

[MIT](LICENSE) © 2026 耳东日成
