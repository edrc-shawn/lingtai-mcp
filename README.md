# 灵台 (Lingtai)

> **A self-evolving knowledge management system.** It gathers materials, refines knowledge, evolves its own rules, runs health checks, and even writes and publishes content for you — all while you sleep.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP Protocol](https://img.shields.io/badge/MCP-2024--11--05-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)

---

## What is Lingtai?

灵台 is a three-layer knowledge system that combines an Obsidian vault, a custom MCP Server, and a suite of automated patrol tasks:

| Layer | Role | Description |
|-------|------|-------------|
| **Raw Materials** (原料) | Unprocessed inputs | Notes, articles, ideas — waiting to be refined |
| **Knowledge Base** (丹房) | Structured knowledge | Refined pages organized across multiple domains |
| **Memory Bank** | AI memory layer | User profiles, lessons learned, observations |

At its core is **灵识** — an AI identity that serves as the cognition layer, calling MCP tools to search, synthesize, refine, and evolve the knowledge base. Any MCP-compatible AI client can connect to 灵台 and instantly understand you.

---

## Architecture

```
Raw Materials → Refine → Knowledge Base (丹房) → 灵识 (Query/Reason/Associate)
                                                       ↓
                                               Health Checks (体检) → Introspection
                                                       ↓
                                               Topic Pool → Multi-platform Publishing
                                                       ↓
                                               Archive
```

### The MCP Server

The server lives in `.tool/lingtai-kb/`. It's a pure-Python MCP Server with **68 tools** spanning knowledge search, memory management, content publishing, material refinement, system tools, and more — all with **zero third-party dependencies**.

See [`.tool/lingtai-kb/README.md`](.tool/lingtai-kb/README.md) for the full tool catalog, architecture details, and API reference.

### The Patrol System (巡更)

灵台 comes with a patrol framework that lets you schedule automated knowledge tasks. For example, you can configure tasks like:

| Time | Example Task |
|------|-------------|
| 03:00 | SkillOpt engine — analyze usage patterns, propose rule refinements, run memory decay |
| 08:00 | Morning brief — gather weather, AI news, topic suggestions |
| 18:00 | Daily health check — lint, backlinks, evolution, reconciliation, self-heal |
| Weekly | Deep archive, encoding fixes, semantic gap detection |
| Monthly | Timeliness scan, rule evolution suggestions |

> **Note:** Patrol tasks run via WorkBuddy or a similar scheduler. You configure what runs and when — the system provides the tools, you define the schedule.

---

## Quick Start

### Prerequisites

- Python 3.10+
- An MCP-compatible AI client (Claude Desktop, Cursor, VS Code, WorkBuddy)
- An Obsidian vault (for your knowledge base)

### 1. Clone

```bash
git clone https://github.com/edrc-shawn/lingtai.git
cd lingtai
```

### 2. Create Your Knowledge Directories

灵台 expects these directories in your vault (not included in the repo — they're your personal data):

```bash
mkdir -p 丹房 原料 体检 画像 作品
```

### 3. Connect Your AI Client

Add this to your MCP configuration:

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

Then start a conversation and say: **"调 context_load"** — if it returns knowledge base stats, you're connected.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LINGTAI_VAULT` | Recommended | Path to your Obsidian vault root |
| `TAVILY_API_KEY` | Optional | Web search API key |
| `ANYSEARCH_API_KEY` | Optional | Alternative web search API key |
| `LINGTAI_CLIENT_ID` | Optional | Client identifier for multi-end scenarios |

---

## Project Structure

```
lingtai/
├── README.md                    # You are here
├── LICENSE                      # MIT
├── AGENTS.md                    # AI rulebook — the operating manual for 灵识
├── AGENTS-appendix.md           # Rules appendix + tool reference
├── .gitignore                   # Excludes personal data directories
├── .github/                     # GitHub Actions CI
├── 入门/                         # Onboarding documentation
├── 技能/                         # Skill templates (13 skills)
└── .tool/
    ├── lingtai-kb/              # MCP Server (116 Python files)
    │   ├── README.md            # Server-specific docs + tool catalog
    │   ├── mcp_server.py        # Entry point
    │   ├── router.py            # JSON-RPC routing
    │   ├── server.py            # Core server (12 mixin inheritance)
    │   ├── server_mixins/       # 22 mixin modules by domain
    │   ├── memory_bank/         # Memory persistence engine
    │   └── skillopt/            # Self-evolution engine
    └── scripts/                 # 46 utility scripts
```

> **Your personal directories** (`丹房/`, `原料/`, `体检/`, `画像/`, `作品/`) are excluded from the repo via `.gitignore`. Create them locally after cloning.

---

## Key Design Principles

1. **Knowledge ≠ Memory.** Structured knowledge lives in 丹房; personal experience lives in the Memory Bank. They're layered, not mixed.

2. **Infrastructure over model upgrades.** Good memory architecture, context engineering, and observability matter more than switching to a newer LLM.

3. **Self-evolving.** The SkillOpt engine analyzes usage patterns and proposes rule refinements. Rules graduate from observations → lessons → hard rules over time.

4. **End-agnostic.** 灵台 produces knowledge, not presentations. Any AI client can consume it — the presentation layer is the client's responsibility.

5. **Verifiable outputs.** Every output must include a verifiable claim. "Looks fine" is a banned phrase.

---

## Contributing

See [CONTRIBUTING.md](.tool/lingtai-kb/CONTRIBUTING.md) for development guidelines.

---

## Security

- All API keys are injected via environment variables — **never hardcoded**
- File operations are sandboxed within the vault directory
- stdout is reserved for JSON-RPC protocol; all logging goes to stderr
- Write operations use a global mutex with 5s timeout
- See [SECURITY.md](.tool/lingtai-kb/SECURITY.md) for the full policy

---

## License

[MIT](LICENSE) © 2026 耳东日成
