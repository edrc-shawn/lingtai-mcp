# -*- coding: utf-8 -*-
"""
灵台MCP - 推理引擎模块
======================
基于灵识的推理引擎，适配灵台的Markdown知识管理系统。

功能：
- 文本分析（LLM增强）
- 文章总结（LLM增强）
- 因果链提取
- 洞察提取（LLM增强）
- 链接建议（LLM增强）
"""

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from logger import get_logger

# 尝试导入LLM推理引擎
try:
    from llm_reasoning import LLMReasoning
    HAS_LLM = True
except ImportError:
    try:
        from .llm_reasoning import LLMReasoning
        HAS_LLM = True
    except ImportError:
        HAS_LLM = False


log = get_logger(__name__)

class ReasoningEngine:
    """灵台灵识推理引擎"""
    
    def __init__(self, data_dir: str = None, use_llm: bool = True):
        """
        初始化推理引擎
        
        Args:
            data_dir: 数据目录路径
            use_llm: 是否使用LLM增强
        """
        if data_dir is None:
            skill_dir = Path(__file__).resolve().parent.parent.parent
            data_dir = skill_dir / ".meta"
        
        self.data_dir = Path(data_dir)
        self.data_file = self.data_dir / "left_brain_data.json"
        
        # 初始化LLM推理引擎（如果可用）
        self.llm = None
        if use_llm and HAS_LLM:
            try:
                self.llm = LLMReasoning()
            except Exception:
                log.debug("suppressed", exc_info=True)
        
        # 加载数据
        self.data = self._load_data()
    
    def _load_data(self) -> dict:
        """加载数据文件"""
        if self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        
        return {"nodes": [], "edges": [], "stats": {}}
    
    def analyze(self, text: str) -> dict:
        """
        分析文本
        
        Args:
            text: 待分析的文本
        
        Returns:
            dict: 分析结果
        """
        # 优先使用LLM分析
        if self.llm:
            try:
                llm_result = self.llm.analyze(text)
                # 合并基础分析结果
                basic_result = self._basic_analyze(text)
                return {
                    "summary": llm_result.get("summary", ""),
                    "keywords": llm_result.get("keywords", []),
                    "key_sentences": basic_result.get("key_sentences", []),
                    "numbers": basic_result.get("numbers", []),
                    "char_count": basic_result.get("char_count", 0),
                    "sentence_count": basic_result.get("sentence_count", 0),
                    "category": llm_result.get("category", ""),
                    "sentiment": llm_result.get("sentiment", ""),
                    "complexity": llm_result.get("complexity", "")
                }
            except Exception:
                log.debug("suppressed", exc_info=True)
        
        # 回退到基础分析
        return self._basic_analyze(text)
    
    def _basic_analyze(self, text: str) -> dict:
        """基础文本分析（不依赖LLM）"""
        # 提取数字
        numbers = re.findall(r'\d+\.?\d*', text)
        
        # 提取关键句（基于句号、感叹号、问号分割）
        sentences = re.split(r'[。！？\n]', text)
        key_sentences = [s.strip() for s in sentences if len(s.strip()) > 5][:5]
        
        # 提取关键词（中英文混合支持）
        word_freq = {}
        current_en = []
        
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff':
                # 中文字符
                if current_en:
                    word = ''.join(current_en)
                    if len(word) >= 2:
                        word_freq[word] = word_freq.get(word, 0) + 1
                    current_en = []
                # 单个中文字符不作为关键词
            elif ch.isalnum():
                current_en.append(ch)
            else:
                if current_en:
                    word = ''.join(current_en)
                    if len(word) >= 2:
                        word_freq[word] = word_freq.get(word, 0) + 1
                    current_en = []
        
        # 处理末尾英文
        if current_en:
            word = ''.join(current_en)
            if len(word) >= 2:
                word_freq[word] = word_freq.get(word, 0) + 1
        
        # 按频率排序，取前10个
        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # 统计字符数（对中文更准确）
        char_count = len(text)
        
        return {
            "numbers": numbers,
            "key_sentences": key_sentences,
            "keywords": [w[0] for w in top_words],
            "char_count": char_count,
            "sentence_count": len([s for s in sentences if s.strip()])
        }
    
    def summarize(self, text: str, max_length: int = 200) -> str:
        """
        总结文本
        
        Args:
            text: 待总结的文本
            max_length: 最大长度
        
        Returns:
            str: 总结结果
        """
        # 优先使用LLM总结
        if self.llm:
            try:
                return self.llm.summarize(text, max_length)
            except Exception:
                log.debug("suppressed", exc_info=True)
        
        # 回退到本地TF-IDF总结
        return self._local_summarize_tfidf(text, max_length)
    
    def _extract_chinese_keywords(self, text: str, top_n: int = 15, min_freq: int = 1) -> list:
        """提取中文关键词（支持混合中英文，按频率排序）
        
        Args:
            text: 输入文本
            top_n: 返回关键词数量
            min_freq: 最小出现频率（默认1，即所有词）
        """
        word_freq = {}
        
        # 1. 提取2-4字中文词组
        chinese_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        for word in chinese_words:
            word_freq[word] = word_freq.get(word, 0) + 1
        
        # 2. 提取英文单词（2+字符）
        english_words = re.findall(r'[a-zA-Z]{2,}', text)
        for word in english_words:
            word_lower = word.lower()
            word_freq[word_lower] = word_freq.get(word_lower, 0) + 1
        
        if not word_freq:
            return []
        
        # 过滤：只保留出现min_freq次以上的词
        filtered_words = {w: f for w, f in word_freq.items() if f >= min_freq}
        
        if not filtered_words:
            filtered_words = word_freq
        
        # 按频率排序，取top_n
        sorted_words = sorted(filtered_words.items(), key=lambda x: x[1], reverse=True)
        return [w[0] for w in sorted_words[:top_n]]
    
    def _local_summarize_tfidf(self, text: str, max_length: int = 200) -> str:
        """本地TF-IDF句子打分总结（零LLM）"""
        # 清理文本：去除所有元数据和格式标记
        clean_text = text
        
        # 移除frontmatter：只处理文件开头的---块（前20行内的连续---块）
        lines = clean_text.split('\n')
        content_lines = []
        in_frontmatter_block = False
        frontmatter_has_content = False
        frontmatter_ended = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 只处理前20行内的frontmatter
            if i > 20:
                content_lines.append(line)
                continue
            
            if stripped == '---':
                if frontmatter_ended:
                    # frontmatter已经结束，这是分隔符，跳过
                    continue
                if not in_frontmatter_block:
                    # 开始新的frontmatter块
                    in_frontmatter_block = True
                    frontmatter_has_content = False
                    continue
                else:
                    # 结束frontmatter块
                    in_frontmatter_block = False
                    frontmatter_ended = True
                    continue
            
            if in_frontmatter_block:
                # 在frontmatter块内，标记有内容
                frontmatter_has_content = True
                continue
            
            # 检查是否是元数据行（独立的元数据行也跳过）
            if re.match(r'^(创建于|回链|处理状态|处理日期|提炼摘要|→)\s*[：:]', stripped):
                continue
            
            content_lines.append(line)
        
        clean_text = '\n'.join(content_lines)
        
        # 移除所有元数据行（无论在哪）
        clean_text = re.sub(r'^创建于：.*$', '', clean_text, flags=re.MULTILINE)
        clean_text = re.sub(r'^回链[：:].*$', '', clean_text, flags=re.MULTILINE)
        clean_text = re.sub(r'^处理状态[：:].*$', '', clean_text, flags=re.MULTILINE)
        clean_text = re.sub(r'^处理日期[：:].*$', '', clean_text, flags=re.MULTILINE)
        clean_text = re.sub(r'^提炼摘要[：:].*$', '', clean_text, flags=re.MULTILINE)
        clean_text = re.sub(r'^→\s*\[\[.*?\]\].*$', '', clean_text, flags=re.MULTILINE)
        
        # 移除markdown格式
        clean_text = re.sub(r'^#{1,6}\s+', '', clean_text, flags=re.MULTILINE)
        clean_text = re.sub(r'\[.*?\]\(.*?\)', '', clean_text)
        clean_text = re.sub(r'\[\[.*?\]\]', '', clean_text)
        
        # 压缩空行和残留的---
        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text)
        clean_text = re.sub(r'^---\s*$', '', clean_text, flags=re.MULTILINE)
        clean_text = clean_text.strip()
        
        if not clean_text:
            clean_text = text
        
        # 分句（支持中英文标点）
        sentences = re.split(r'[。！？\n]+', clean_text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
        
        if not sentences:
            return text[:max_length]
        
        if len(sentences) <= 2:
            result = "。".join(sentences)
            return result[:max_length] if len(result) > max_length else result
        
        # 提取全文关键词
        keywords = self._extract_chinese_keywords(clean_text, top_n=15)
        if not keywords:
            # 无关键词时回退到位置打分
            return self._summarize_by_position(sentences, max_length)
        
        # 构建关键词集合（用于快速查找）
        keyword_set = set(keywords)
        
        # 计算每个句子的TF-IDF得分
        scored_sentences = []
        total_sentences = len(sentences)
        
        for i, sentence in enumerate(sentences):
            score = 0.0
            
            # 1. 关键词密度得分（TF-IDF简化版）
            # 匹配中文词组、英文单词、单个重要字符（如O、π）
            sentence_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}|[a-zA-Z]{2,}|[A-Za-zπ]', sentence))
            keyword_matches = sentence_words & keyword_set
            if sentence_words:
                tf_score = len(keyword_matches) / len(sentence_words)
            else:
                tf_score = 0
            score += tf_score * 3.0  # 关键词密度权重
            
            # 2. 关键词覆盖得分（覆盖更多不同关键词得分更高）
            if keywords:
                coverage = len(keyword_matches) / len(keywords)
                score += coverage * 2.0
            
            # 3. 位置得分（首句加分，末句小加分）
            if i == 0:
                score += 1.5  # 首句很重要
            elif i == total_sentences - 1:
                score += 0.5  # 末句可能有总结
            elif i <= total_sentences * 0.2:
                score += 0.8  # 前20%的句子
            
            # 4. 句子长度惩罚（太短或太长都扣分）
            sent_len = len(sentence)
            if sent_len < 5:
                score -= 1.0  # 太短，信息量少
            elif sent_len > 100:
                score -= 0.5  # 太长，可能不够精炼
            
            # 5. 数字/具体信息加分（含数字的句子通常更具体）
            if re.search(r'\d+', sentence):
                score += 0.3
            
            scored_sentences.append((i, score, sentence))
        
        # 按得分排序，取top句子
        scored_sentences.sort(key=lambda x: x[1], reverse=True)
        
        # 贪心选择：按得分从高到低选，去重，直到达到max_length
        selected = []
        selected_texts = set()  # 用于去重
        current_length = 0
        
        for _, _, sentence in scored_sentences:
            # 去重：检查是否与已选句子高度相似
            sent_key = sentence[:40]  # 用前40字做去重标识
            if sent_key in selected_texts:
                continue
            
            if current_length + len(sentence) > max_length:
                # 尝试截断最后一个句子
                remaining = max_length - current_length
                if remaining > 20:  # 至少保留20字才有意义
                    selected.append(sentence[:remaining])
                break
            selected.append(sentence)
            selected_texts.add(sent_key)
            current_length += len(sentence)
        
        if not selected:
            return text[:max_length]
        
        # 按原文顺序重新排列
        selected_set = set(s[:40] for s in selected)
        ordered = [s for s in sentences if s[:40] in selected_set]
        
        result = "。".join(ordered)
        return result[:max_length] if len(result) > max_length else result
    
    def _summarize_by_position(self, sentences: list, max_length: int) -> str:
        """位置打分总结（无关键词时的回退方案）"""
        # 首句 + 中间均匀采样
        selected = []
        current_length = 0
        
        # 先加首句
        if sentences:
            selected.append(sentences[0])
            current_length += len(sentences[0])
        
        # 均匀采样中间句子
        if len(sentences) > 2:
            step = max(1, len(sentences) // 3)
            for i in range(step, len(sentences) - 1, step):
                if current_length + len(sentences[i]) > max_length:
                    break
                selected.append(sentences[i])
                current_length += len(sentences[i])
        
        result = "。".join(selected)
        return result[:max_length] if len(result) > max_length else result
    
    def entangle(self, keyword: str, hops: int = 2) -> list:
        """
        纠缠场关联分析
        
        Args:
            keyword: 关键词
            hops: 扩散跳数
        
        Returns:
            list: 关联的知识链
        """
        # 找到匹配的节点
        start_nodes = []
        keyword_lower = keyword.lower()
        
        for node in self.data["nodes"]:
            if keyword_lower in node["text"].lower():
                start_nodes.append(node)
        
        if not start_nodes:
            return []
        
        # BFS扩散
        visited = set()
        result_chain = []
        queue = [(node, 0, [node["text"]]) for node in start_nodes]
        
        while queue:
            current_node, current_hop, current_chain = queue.pop(0)
            
            if current_node["id"] in visited:
                continue
            if current_hop > hops:
                continue
            
            visited.add(current_node["id"])
            result_chain.append({
                "node": current_node,
                "chain": current_chain,
                "hop": current_hop
            })
            
            # 找到关联节点
            for edge in self.data["edges"]:
                neighbor_id = None
                if edge["source"] == current_node["id"]:
                    neighbor_id = edge["target"]
                elif edge["target"] == current_node["id"]:
                    neighbor_id = edge["source"]
                
                if neighbor_id and neighbor_id not in visited:
                    neighbor = self._get_node_by_id(neighbor_id)
                    if neighbor:
                        new_chain = current_chain + [neighbor["text"]]
                        queue.append((neighbor, current_hop + 1, new_chain))
        
        return result_chain
    
    def extract_causality(self, text: str) -> list:
        """
        提取因果链
        
        Args:
            text: 待分析的文本
        
        Returns:
            list: 因果链列表
        """
        # 简单的因果关系提取（基于关键词）
        causality_patterns = [
            (r'因为(.+?)所以(.+?)。', '因为{}所以{}'),
            (r'由于(.+?)导致(.+?)。', '由于{}导致{}'),
            (r'(.+?)因此(.+?)。', '{}因此{}'),
            (r'(.+?)所以(.+?)。', '{}所以{}'),
        ]
        
        causality_chain = []
        
        for pattern, template in causality_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                causality_chain.append({
                    "cause": match[0].strip(),
                    "effect": match[1].strip(),
                    "template": template
                })
        
        return causality_chain
    
    def detect_patterns(self, texts: list) -> dict:
        """
        检测文本模式
        
        Args:
            texts: 文本列表
        
        Returns:
            dict: 检测到的模式
        """
        if not texts:
            return {"patterns": [], "common_keywords": []}
        
        # 提取所有关键词
        all_keywords = []
        for text in texts:
            words = text.split()
            for word in words:
                if len(word) >= 2:
                    all_keywords.append(word.lower())
        
        # 统计词频
        keyword_freq = {}
        for keyword in all_keywords:
            keyword_freq[keyword] = keyword_freq.get(keyword, 0) + 1
        
        # 找到高频词（出现>=2次）
        common_keywords = [k for k, v in keyword_freq.items() if v >= 2]
        
        return {
            "patterns": [],
            "common_keywords": common_keywords[:10]
        }
    
    def extract_insights(self, text: str) -> dict:
        """
        提取文本中的洞察（LLM增强）
        
        Args:
            text: 待分析的文本
        
        Returns:
            dict: 洞察结果
        """
        if self.llm:
            try:
                return self.llm.extract_insights(text)
            except Exception:
                log.debug("suppressed", exc_info=True)
        
        # 回退到基础分析
        analysis = self._basic_analyze(text)
        return {
            "core_insight": analysis["key_sentences"][0] if analysis["key_sentences"] else "",
            "implications": [],
            "actionable": [],
            "connections": []
        }
    
    def suggest_links(self, page_content: str, other_pages: List[dict]) -> List[dict]:
        """
        建议页面间的链接（本地关键词匹配）
        
        Args:
            page_content: 当前页面内容
            other_pages: 其他页面列表 [{"path": "...", "title": "...", "summary": "..."}]
        
        Returns:
            list: 链接建议 [{"title": ..., "reason": ..., "strength": "强/中/弱"}]
        """
        if self.llm:
            try:
                return self.llm.suggest_links(page_content, other_pages)
            except Exception:
                log.debug("suppressed", exc_info=True)
        
        return self._local_suggest_links(page_content, other_pages)
    
    def _local_suggest_links(self, page_content: str, other_pages: List[dict]) -> List[dict]:
        """本地链接建议：关键词重叠 + 标题匹配"""
        if not other_pages:
            return []
        
        # 清理并提取当前页面关键词
        clean_content = self._clean_content_for_links(page_content)
        current_keywords = set(self._extract_chinese_keywords(clean_content, top_n=50, min_freq=1))
        
        if not current_keywords:
            return []
        
        # 评估每个候选页面
        suggestions = []
        for page in other_pages[:50]:
            title = page.get('title', '')
            summary = page.get('summary', '')
            path = page.get('path', '')
            
            if not title:
                continue
            
            # 合并标题和摘要作为候选文本
            candidate_text = f"{title} {summary}"
            candidate_keywords = set(self._extract_chinese_keywords(candidate_text, top_n=30, min_freq=1))
            
            if not candidate_keywords:
                continue
            
            # 1. 关键词重叠得分
            overlap = current_keywords & candidate_keywords
            overlap_ratio = len(overlap) / len(candidate_keywords) if candidate_keywords else 0
            
            # 2. 标题匹配得分（标题关键词完全匹配）
            title_keywords = set(re.findall(r'[\u4e00-\u9fff]{2,4}', title))
            title_overlap = current_keywords & title_keywords
            title_bonus = len(title_overlap) * 0.1
            
            # 总分
            total_score = overlap_ratio + title_bonus
            
            # 过滤低相关度
            if total_score < 0.03:
                continue
            
            # 判断强度
            if total_score >= 0.3:
                strength = "强"
            elif total_score >= 0.15:
                strength = "中"
            else:
                strength = "弱"
            
            # 生成关联原因
            reasons = []
            if overlap:
                reasons.append(f"共同概念: {', '.join(list(overlap)[:3])}")
            if title_overlap:
                reasons.append(f"标题匹配: {', '.join(list(title_overlap)[:2])}")
            
            reason = "; ".join(reasons) if reasons else "主题相关"
            
            suggestions.append({
                "title": title,
                "path": path,
                "reason": reason,
                "strength": strength,
                "score": round(total_score, 3)
            })
        
        # 按相关度排序，返回top 5
        suggestions.sort(key=lambda x: x["score"], reverse=True)
        return suggestions[:5]
    
    def _clean_content_for_links(self, text: str) -> str:
        """清理文本用于链接建议（复用frontmatter清理逻辑）"""
        clean_content = text
        lines = clean_content.split('\n')
        content_lines = []
        in_frontmatter_block = False
        frontmatter_ended = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            if i > 20:
                content_lines.append(line)
                continue
            
            if stripped == '---':
                if frontmatter_ended:
                    continue
                if not in_frontmatter_block:
                    in_frontmatter_block = True
                    continue
                else:
                    in_frontmatter_block = False
                    frontmatter_ended = True
                    continue
            
            if in_frontmatter_block:
                continue
            
            if re.match(r'^(创建于|回链|处理状态|处理日期|提炼摘要|→)\s*[：:]', stripped):
                continue
            
            content_lines.append(line)
        
        clean_content = '\n'.join(content_lines)
        clean_content = re.sub(r'^#{1,6}\s+', '', clean_content, flags=re.MULTILINE)
        clean_content = re.sub(r'\[\[.*?\]\]', '', clean_content)
        return clean_content
    
    def _compute_tf(self, text: str) -> dict:
        """计算词频（TF）并返回带权重的词典"""
        word_freq = {}
        
        # 提取2-4字中文词组
        chinese_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        for word in chinese_words:
            word_freq[word] = word_freq.get(word, 0) + 1
        
        # 提取英文单词
        english_words = re.findall(r'[a-zA-Z]{2,}', text)
        for word in english_words:
            word_lower = word.lower()
            word_freq[word_lower] = word_freq.get(word_lower, 0) + 1
        
        if not word_freq:
            return {}
        
        # 归一化为TF值
        total = sum(word_freq.values())
        tf = {w: f / total for w, f in word_freq.items()}
        
        return tf
    
    def _extract_ngrams(self, text: str, n_range: tuple = (2, 3), max_ngrams: int = 100) -> set:
        """提取N-gram组合（2-3词连续组合，基于关键词）"""
        # 提取所有2-4字词组
        words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        
        # 过滤停用词
        stop_words = set(['的了是在有和与或但而你我他她它们这那哪个些没不都也就什么为什么怎么'])
        words = [w for w in words if w not in stop_words]
        
        # 提取N-gram
        ngrams = set()
        for n in range(n_range[0], n_range[1] + 1):
            for i in range(len(words) - n + 1):
                gram = tuple(words[i:i+n])
                # 只保留包含有意义词的N-gram
                if any(len(w) >= 2 for w in gram):
                    ngrams.add(gram)
                    if len(ngrams) >= max_ngrams:
                        return ngrams
        
        return ngrams
    
    def _get_node_by_id(self, node_id: str) -> Optional[dict]:
        """根据ID获取节点"""
        for node in self.data["nodes"]:
            if node["id"] == node_id:
                return node
        return None

    def chat(self, prompt: str, max_tokens: int = 2048, action: str = "query") -> str:
        """直接调用 LLM chat（公开接口，替代直接调 _call_llm）

        Args:
            prompt: 完整 prompt
            max_tokens: 最大输出 token 数
            action: 操作类型，用于 token 监控分类

        Returns:
            str: LLM 返回文本
        """
        if not self.llm:
            # 惰性重试：之前初始化失败（如相对导入问题），现在再试一次
            try:
                from llm_reasoning import LLMReasoning
                self.llm = LLMReasoning()
            except Exception:
                log.debug("suppressed", exc_info=True)
        if not self.llm:
            return "[错误: LLM 未配置]"
        return self.llm._call_llm(prompt, max_tokens=max_tokens, action=action)


# 便捷函数
def create_reasoning_engine(data_dir: str = None, use_llm: bool = True) -> ReasoningEngine:
    """创建推理引擎实例"""
    return ReasoningEngine(data_dir, use_llm)


if __name__ == "__main__":
    # 测试
    engine = ReasoningEngine()
    
    # 分析测试
    text = "Python是一种编程语言，它简单易学。Python广泛应用于数据分析、人工智能等领域。"
    analysis = engine.analyze(text)
    print(f"分析结果: {analysis}")
    
    # 总结测试
    summary = engine.summarize(text)
    print(f"总结结果: {summary}")
