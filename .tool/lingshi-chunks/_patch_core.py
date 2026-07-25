"""Patch core.py: add extract_llm method to StructuredIndex class."""
import os

path = os.path.join(os.path.dirname(__file__), "core.py")
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '    def extract(self, md_path: str) -> int:\n        """从一篇丹房页提取。"""\n        chunks = self.extractor.extract_from_file(md_path)\n        if chunks:\n            self.store.delete_by_source(md_path)\n            self.store.save_batch(chunks)\n        return len(chunks)\n\n    def reindex_all(self) -> int:'

new = '    def extract(self, md_path: str) -> int:\n        """从一篇丹房页提取（规则版）。"""\n        chunks = self.extractor.extract_from_file(md_path)\n        if chunks:\n            self.store.delete_by_source(md_path)\n            self.store.save_batch(chunks)\n        return len(chunks)\n\n    def extract_llm(self, md_path: str, llm_client=None) -> int:\n        """从一篇丹房页提取（LLM 版）。"""\n        from llm_client import LLMClient\n        client = llm_client or LLMClient()\n        chunks = self.extractor.extract_with_llm(md_path, client)\n        if chunks:\n            self.store.delete_by_source(md_path)\n            self.store.save_batch(chunks)\n        return len(chunks)\n\n    def reindex_all(self) -> int:'

assert old in content, "old block not found!"
content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("core.py patched successfully")