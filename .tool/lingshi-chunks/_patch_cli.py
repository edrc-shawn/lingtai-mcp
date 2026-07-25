"""Patch cli.py: add extract-llm command."""
import os

path = os.path.join(os.path.dirname(__file__), "cli.py")
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Add cmd_extract_llm function before main()
old_func = 'def main():'
new_func = '''def cmd_extract_llm(si: StructuredIndex, args: list):
    """lingtai chunks extract-llm <md_path>"""
    if not args:
        print("? 请指定丹房页路径或标题")
        return
    target = find_md(si.vault_path, args[0])
    print(f"? LLM 提取: {target}")
    count = si.extract_llm(target)
    print(f"? 完成: {count} chunks")


def main():'''

content = content.replace(old_func, new_func, 1)

# Add to commands dict
old_cmds = '"extract": cmd_extract,'
new_cmds = '"extract": cmd_extract,\n        "extract-llm": cmd_extract_llm,'
content = content.replace(old_cmds, new_cmds, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("cli.py patched successfully")