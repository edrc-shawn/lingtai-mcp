"""Patch cli.py docstring to include extract-llm."""
import os

path = os.path.join(os.path.dirname(__file__), "cli.py")
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = "python cli.py extract <md_path>     从单篇丹房页提取 chunk"
new = "python cli.py extract <md_path>     从单篇丹房页提取 chunk（规则版）\n    python cli.py extract-llm <md_path>   从单篇丹房页提取 chunk（LLM 版）"

content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("cli.py docstring patched")