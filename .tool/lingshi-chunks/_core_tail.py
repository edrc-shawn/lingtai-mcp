self.index_dir.mkdir(parents=True, exist_ok=True)
        (self.index_dir / CHUNKS_DIR).mkdir(parents=True, exist_ok=True)

    def extract(self, md_path: str) -> int:
        """从一篇丹房页提取（规则版）。"""
        chunks = self.extractor.extract_from_file(md_path)
        if chunks:
            self.store.delete_by_source(md_path)
            self.store.save_batch(chunks)
        return len(chunks)

    def extract_llm(self, md_path: str, llm_client=None) -> int:
        """从一篇丹房页提取（LLM 版）。"""
        from llm_client import LLMClient
        client = llm_client or LLMClient()
        chunks = self.extractor.extract_with_llm(md_path, client)
        if chunks:
            self.store.delete_by_source(md_path)
            self.store.save_batch(chunks)
        return len(chunks)

    def reindex_all(self) -> int:
        """全量重建索引。"""
        return self.extractor.extract_all()

    def search(self, query: str, top_k: int = 10, **filters) -> list:
        """搜索 chunk。"""
        results = self.searcher.search(query, top_k, **filters)
        return [
            {
                "chunk": c.to_search_snippet(),
                "title": c["title"],
                "domain": c["domain"],
                "content": c["content"][:200],
                "score": round(s, 2),
            }
            for c, s in results
        ]

    def stats(self) -> dict:
        """索引统计。"""
        return self.store.stats()