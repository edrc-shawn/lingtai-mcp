# -*- coding: utf-8 -*-
"""
敏感度门控（方向⑩）
=====================
借鉴 Polaris memoryEngine.ts classifyMemoryWriteItems 理念。

功能：
- PII 检测（身份证/手机/地址/银行卡/密码/病史等）
- 自动脱敏替换
- 敏感度分级：low / high
- 脱敏后标记 pii: true 放入隔离域
"""

import re
from typing import Dict, List, Tuple

# ─── PII 检测正则模式（Polaris HIGH_RISK_PATTERN 扩展）───

PII_PATTERNS = {
    '身份证': [
        r'\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b',
        r'\b[1-9]\d{16}[\dXx]\b',
    ],
    '手机号': [
        r'(?<!\d)1[3-9]\d{9}(?!\d)',
    ],
    '固定电话': [
        r'\b0\d{2,3}[-\s]?\d{7,8}\b',
    ],
    '银行卡': [
        r'\b[1-9]\d{15,18}\b',
    ],
    '邮箱': [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    ],
    '地址': [
        r'(?:地址|住址|住在|位于)[：:\s]*[\u4e00-\u9fff]{2,}(?:省|市|区|县|镇|乡|村|路|街|巷|号|栋|单元|室|楼)',
    ],
    '生日': [
        r'\b(?:19|20)\d{2}[-/年](?:0?[1-9]|1[0-2])[-/月](?:0?[1-9]|[12]\d|3[01])[日号]?\b',
        r'(?:生日|出生|生于)[：:\s]*\d{4}',
    ],
    '真名': [
        r'(?:真名|本名|全名|姓名|名字)[：:\s]*[\u4e00-\u9fff]{2,4}',
        r'(?:我叫|我是|本人)[：:\s]*[\u4e00-\u9fff]{2,4}(?:(?:，|。|$|的))',
    ],
    '密码/密钥': [
        r'(?:密码|口令|密钥|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*\S{8,}',
        r'\b(?:sk-|pk-|AKIA)[A-Za-z0-9]{16,}\b',
    ],
    '医疗健康': [
        r'(?:病史|疾病|诊断|处方|用药|服药|住院|手术|过敏)[：:\s]*[\u4e00-\u9fff]{3,}',
    ],
    '收入财务': [
        r'(?:月薪|年薪|工资|收入|薪资|待遇)[：:\s]*\d{4,}',
        r'(?:银行卡|银行账户|支付宝|微信)[：:\s]*\d',
    ],
}

# 低敏感度模式（偏好/习惯类）
LOW_SENSITIVITY_PATTERNS = {
    '昵称': [
        r'(?:叫我|称呼)[：:\s]*[\u4e00-\u9fffA-Za-z0-9_]{2,10}',
    ],
    '年龄范围': [
        r'(?:我|今年|年龄)[：:\s]*(?:\d{2}|\d{1,2}岁|二十|三十|四十|五十)',
        r'\b(?:90后|00后|80后)\b',
    ],
    '城市': [
        r'(?:在|住|来|去)[：:\s]*([\u4e00-\u9fff]{2,}(?:市|城))',
    ],
}


def detect_pii(text: str) -> List[Dict]:
    """
    检测文本中的 PII 内容
    
    Returns:
        list: [{type, match, start, end, sensitivity}, ...]
    """
    findings = []
    
    for pii_type, patterns in PII_PATTERNS.items():
        for pat in patterns:
            for m in re.finditer(pat, text):
                findings.append({
                    'type': pii_type,
                    'match': m.group(),
                    'start': m.start(),
                    'end': m.end(),
                    'sensitivity': 'high',
                })
    
    for pii_type, patterns in LOW_SENSITIVITY_PATTERNS.items():
        for pat in patterns:
            for m in re.finditer(pat, text):
                findings.append({
                    'type': pii_type,
                    'match': m.group(),
                    'start': m.start(),
                    'end': m.end(),
                    'sensitivity': 'low',
                })
    
    # 去重（重叠匹配取最长的）
    deduped = []
    for f in sorted(findings, key=lambda x: x['start']):
        if deduped and f['start'] < deduped[-1]['end']:
            # 重叠：保留更长的
            if f['end'] > deduped[-1]['end']:
                deduped[-1] = f
        else:
            deduped.append(f)
    
    return deduped


def redact_pii(text: str, mask_char: str = '█') -> Tuple[str, List[Dict]]:
    """
    脱敏 PII 内容
    
    Args:
        text: 原始文本
        mask_char: 掩码字符
    
    Returns:
        (redacted_text, findings)
    """
    findings = detect_pii(text)
    if not findings:
        return text, []
    
    # 从后往前替换（避免偏移问题）
    segments = []
    last_end = 0
    for f in sorted(findings, key=lambda x: x['start']):
        if f['start'] > last_end:
            segments.append(text[last_end:f['start']])
        redacted = f'[{f["type"]}-已脱敏]'
        segments.append(redacted)
        last_end = f['end']
    
    if last_end < len(text):
        segments.append(text[last_end:])
    
    return ''.join(segments), findings


def classify_sensitivity(text: str) -> dict:
    """
    对整段文本做敏感度分级
    
    借鉴 Polaris classifyMemoryWriteItems()
    
    Returns:
        dict: {level, has_high, has_low, high_items, low_items, redacted_text}
    """
    findings = detect_pii(text)
    
    high_items = [f for f in findings if f['sensitivity'] == 'high']
    low_items = [f for f in findings if f['sensitivity'] == 'low']
    
    has_high = len(high_items) > 0
    has_low = len(low_items) > 0
    
    if has_high:
        level = 'high'
    elif has_low:
        level = 'low'
    else:
        level = 'none'
    
    redacted, _ = redact_pii(text)
    
    return {
        'level': level,
        'has_high': has_high,
        'has_low': has_low,
        'high_items': [f['type'] for f in high_items],
        'low_items': [f['type'] for f in low_items],
        'finding_count': len(findings),
        'redacted_text': redacted if has_high else text,
    }


def redact_file_content(content: str) -> dict:
    """
    对原料文件内容执行完整脱敏（可直接集成到 raw_preprocess --step redact_pii）
    
    Args:
        content: 文件完整内容（含 frontmatter）
    
    Returns:
        dict: 处理结果
    """
    # 分离 frontmatter 和正文
    body = content
    fm_text = ''
    has_fm = content.startswith('---')
    if has_fm:
        parts = content.split('---', 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2]
    
    # 只对正文做脱敏
    redacted_body, findings = redact_pii(body)
    
    redacted = has_pii = len(findings) > 0
    has_high = any(f['sensitivity'] == 'high' for f in findings)
    high_types = list(set(f['type'] for f in findings if f['sensitivity'] == 'high'))
    
    if redacted:
        # 构建新内容
        if has_fm:
            # 在 frontmatter 中添加 pii 标记
            new_fm = fm_text.rstrip()
            if has_high:
                if 'pii:' not in fm_text:
                    new_fm += '\npii: true'
                if '域:' not in fm_text:
                    new_fm += '\n域: 98-敏感'
            new_content = f"---\n{new_fm}---\n{redacted_body}"
        else:
            new_content = f"---\npii: true\n域: 98-敏感\n---\n{redacted_body}"
    else:
        new_content = content
    
    return {
        'redacted': redacted,
        'has_high': has_high,
        'high_types': high_types,
        'finding_count': len(findings),
        'findings': findings,
        'new_content': new_content if redacted else None,
    }


if __name__ == '__main__':
    # 测试
    test = "我叫张三，电话是13800138000，住在北京市海淀区中关村大街1号，生日是1990-01-01"
    result = redact_file_content(test)
    print(f"redacted: {result['redacted']}")
    print(f"has_high: {result['has_high']}")
    print(f"types: {result['high_types']}")
    print(f"result: {result['new_content']}")