# 灵台 MCP Server — 开发命令速查
# 用法: make test / make lint / make check

.PHONY: test lint check clean

# 全量测试
test:
	python -m pytest tests/ -v

# 快速 smoke test（不含 FTS/BM25 构建）
smoke:
	python -m pytest tests/test_tools_smoke.py -v

# Lint（ruff，需 pip install ruff）
lint:
	python -m ruff check . --select E,F,W --ignore E501,E402,W503

# 类型检查（mypy，需 pip install mypy）
typecheck:
	python -m mypy fts_index.py bm25_engine.py concurrency.py --ignore-missing-imports

# 全量检查（lint + test）
check: lint test

# 清理缓存
clean:
	rm -f .fts5.db .fts5.db-wal .fts5.db-shm 2>/dev/null || true
	rm -rf .pytest_cache 2>/dev/null || true
	rm -rf __pycache__ 2>/dev/null || true
	rm -rf tests/__pycache__ 2>/dev/null || true
