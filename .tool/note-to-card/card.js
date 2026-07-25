#!/usr/bin/env node
/**
 * card.js — Markdown → 小红书风格卡片 PNG
 *
 * 用法:
 *   node card.js <input.md>                          # 基本用法，输出到 input/ 目录
 *   node card.js <input.md> --dir ./输出              # 指定输出目录
 *   node card.js <input.md> --split h1               # 按 H1 分割（默认 H2）
 *   node card.js <input.md> --prefix 内观             # 输出文件名前缀
 *   node card.js <input.md> --single                 # 只输出第一张（封面）
 *   node card.js <input.md> --theme minimal           # 切换主题
 *   node card.js <input.md> --width 1080 --height 1920 # 自定义尺寸
 *   node card.js <input.md> --browser                # 显示浏览器窗口（调试用）
 *
 * 管道模式:
 *   cat note.md | node card.js --stdin
 *
 * 输出: 当前目录或 --dir 下，每张卡片一个 .png
 */

import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'fs';
import { resolve, dirname, basename, extname } from 'path';
import { chromium } from 'playwright';
import { marked } from 'marked';

// ── 默认配置 ──
const DEFAULTS = {
  width: 1080,
  height: 1920,
  split: 'h2',         // h1 | h2
  theme: 'earong',
  prefix: 'card',
  single: false,
  browser: false,
  stdin: false,
  avatar: '耳',
  nickname: '耳东日成',
  userId: 'erdongricheng',
  brand: '灵台 · 耳东日成',
};

// ── 解析 CLI ──
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = { ...DEFAULTS };
  const positional = [];

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--dir':      opts.dir = args[++i]; break;
      case '--prefix':   opts.prefix = args[++i]; break;
      case '--split':    opts.split = args[++i]; break;
      case '--width':    opts.width = parseInt(args[++i], 10); break;
      case '--height':   opts.height = parseInt(args[++i], 10); break;
      case '--theme':    opts.theme = args[++i]; break;
      case '--avatar':   opts.avatar = args[++i]; break;
      case '--nickname': opts.nickname = args[++i]; break;
      case '--userid':   opts.userId = args[++i]; break;
      case '--brand':    opts.brand = args[++i]; break;
      case '--single':   opts.single = true; break;
      case '--browser':  opts.browser = true; break;
      case '--stdin':    opts.stdin = true; break;
      default:
        if (!args[i].startsWith('--')) positional.push(args[i]);
    }
  }

  if (opts.stdin) {
    opts.input = null; // 从 stdin 读
  } else if (positional.length > 0) {
    opts.input = resolve(positional[0]);
    if (!existsSync(opts.input)) {
      console.error(`❌ 文件不存在: ${opts.input}`);
      process.exit(1);
    }
  } else {
    console.error('❌ 未指定输入文件。用法: node card.js <input.md>');
    console.error('   或: cat note.md | node card.js --stdin');
    process.exit(1);
  }

  return opts;
}

// ── 读 Markdown ──
async function readMarkdown(opts) {
  if (opts.stdin) {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    return chunks.join('');
  }
  return readFileSync(opts.input, 'utf-8');
}

// ── 按标题分割 ──
function splitByHeadings(md, level) {
  const pattern = level === 'h1' ? /^# /gm : /^## /gm;
  const parts = [];
  let lastIndex = 0;
  let match;

  while ((match = pattern.exec(md)) !== null) {
    if (lastIndex > 0 || match.index > 0) {
      parts.push(md.slice(lastIndex, match.index).trim());
    }
    lastIndex = match.index;
  }
  if (lastIndex < md.length) {
    parts.push(md.slice(lastIndex).trim());
  }

  // 如果没按预期分割，退回整篇
  if (parts.length === 0) parts.push(md.trim());
  return parts.filter(p => p.length > 0);
}

// ── 拼 HTML 卡片 ──
function buildCardHTML(sectionHTML, pageNum, total, opts) {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
${readThemeCSS(opts.theme)}
/* 覆写宽高由 JS 传入 */
</style>
</head>
<body style="width:${opts.width}px;min-height:${opts.height}px;">
  <div class="user-bar">
    <div class="avatar">${opts.avatar}</div>
    <div class="user-info">
      <span class="nickname">${escapeHTML(opts.nickname)}</span>
      <span class="user-id">${escapeHTML(opts.userId)}</span>
    </div>
  </div>
  <div class="content">
    ${sectionHTML}
  </div>
  <div class="footer">
    <span class="brand">${escapeHTML(opts.brand)}</span>
  </div>
  <div class="page-num">${pageNum} / ${total}</div>
</body>
</html>`;
}

// ── 读主题 CSS ──
function readThemeCSS(theme) {
  const themePath = resolve(dirname(process.argv[1]), 'themes', `${theme}.css`);
  try {
    return readFileSync(themePath, 'utf-8');
  } catch {
    console.warn(`⚠️ 主题 "${theme}" 未找到，使用默认样式`);
    return readFileSync(resolve(dirname(process.argv[1]), 'themes', 'earong.css'), 'utf-8');
  }
}

// ── HTML 转义 ──
function escapeHTML(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── 确保输出目录 ──
function ensureDir(dir) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

// ── 填充零位 ──
function pad(n) { return String(n).padStart(2, '0'); }

// ── 主流程 ──
async function main() {
  const opts = parseArgs();

  // 1. 读 Markdown
  const md = await readMarkdown(opts);
  if (!md.trim()) { console.error('❌ 输入内容为空'); process.exit(1); }

  // 2. 分割
  const sections = splitByHeadings(md, opts.split);
  const total = opts.single ? 1 : sections.length;
  console.log(`📄 分割为 ${sections.length} 段，输出 ${total} 张卡片`);

  // 3. 确定输出目录
  const outDir = opts.dir
    ? resolve(opts.dir)
    : resolve(dirname(opts.input || '.'), basename(opts.input || 'output', extname(opts.input || '.md')));
  ensureDir(outDir);

  // 4. 启动浏览器
  const browser = await chromium.launch({ channel: 'chrome', headless: !opts.browser });
  const context = await browser.newContext({
    deviceScaleFactor: 2,  // Retina 清晰度
    viewport: { width: opts.width, height: opts.height },
  });

  try {
    for (let i = 0; i < total; i++) {
      const idx = i;
      const rawHTML = marked.parse(sections[idx], { async: false });
      const html = buildCardHTML(rawHTML, idx + 1, total, opts);
      const outPath = resolve(outDir, `${opts.prefix}-${pad(idx + 1)}.png`);

      const page = await context.newPage();
      await page.setContent(html, { waitUntil: 'networkidle' });

      // 等字体渲染
      await page.waitForTimeout(500);

      // 截图（只截卡片区域，避免多余背景）
      const clip = { x: 0, y: 0, width: opts.width, height: opts.height };
      await page.screenshot({ path: outPath, clip, fullPage: false });

      await page.close();
      console.log(`  ✅ [${pad(idx + 1)}/${total}] ${outPath}`);
    }
  } finally {
    await browser.close();
  }

  console.log(`\n🎉 完成！共 ${total} 张卡片 → ${outDir}`);
}

main().catch(err => {
  console.error('❌ 脚本异常:', err);
  process.exit(1);
});
