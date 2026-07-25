# Contributing to 灵台 MCP Server

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/edrc-shawn/lingtai.git
   cd lingtai
   ```

2. **Set up Python environment**:
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Linux/macOS
   source .venv/bin/activate
   ```

3. **No pip install needed** — the server uses only the Python standard library. All dependencies (network requests, JSON parsing, threading) are stdlib.

4. **Configure API keys** (optional for development):
   Create `.tool/config/api_keys.json`:
   ```json
   {
     "tavily": {"key": "your-tavily-key"},
     "agnes": {"key": "your-agnes-key"}
   }
   ```

## Codebase Structure

```
.tool/lingtai-kb/
├── mcp_server.py          # Entry point (delegates to router)
├── router.py              # JSON-RPC routing + main loop
├── server.py              # LingtaiMCPServer class (mixin inheritance)
├── config.py              # API key + model registry
├── errors.py              # Standardized error codes
├── schema_validator.py    # Page path/frontmatter validation
├── concurrency.py         # Write lock (threading)
├── session_tracker.py     # Tool call logging
├── server_mixins/         # Module mixins (knowledge, memory, etc.)
├── memory_bank/           # Memory persistence engine
├── tests/                 # Unit tests
└── README.md              # This file
```

## Coding Guidelines

### Protocol
- All tools must use `annotations.readOnlyHint` or `annotations.destructiveHint` for client authorization
- Error returns must use `errors.ok()` / `errors.fail()` helpers
- JSON-RPC responses must go through `sys.stdout.write()`, never `print()`

### Security
- **Never hardcode API keys** — always load from `config.get_api_key()` or environment variables
- **Never use string concatenation for shell commands** — use parameter arrays
- Always validate input paths are within the vault directory
- Log to stderr, not stdout (stdout is reserved for JSON-RPC)

### Testing
- Place tests in `tests/test_*.py`
- Each test is a function starting with `test_`
- Run all tests: `python run_tests.py`

## Pull Request Process

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-change`
3. Make your changes and run `python run_tests.py`
4. Commit with clear messages following [Conventional Commits](https://www.conventionalcommits.org/)
5. Push and open a PR against `main`

## Code of Conduct

Be respectful, constructive, and inclusive. We're all here to learn and build something useful.