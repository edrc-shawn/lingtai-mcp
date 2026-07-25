---
标题: 单页信息图海报（篇级轨）
日期: 2026-07-01
---

# 单页信息图海报（篇级轨）

> 一张多panel整版图顶 N 格。**这是篇级轨，不是第四条单点轨**——前三轨（情绪/解释/四格）判"单个锚点画成哪种图"，本轨判"整条流程/整组对比要不要打包成一张总览版"。一篇最多一张，放开头当导览或结尾当总结。

## 何时起海报（默认不起，命中才起）

命中任一 → 考虑配一张（且仅一张）总览海报，其余点照常走三轨散图：
- **≥3 步完整主流程**：管线/工作流/操作步骤/生命周期 → 纵向编号流程海报
- **一组并列对比/功能矩阵**：A vs B、N个方案横排、能力矩阵 → 横向分栏 or 网格海报
- **需要一眼看全貌**：文章信息多，读者要先有张地图再逐段深入 → 开头放总览海报

⛔ 不起海报（退回别的轨）：
- **单个机制/单个结构点** → 走**解释图轨**，别硬塞海报
- **流程>6步/每格要塞长文字/各点需要分别放进不同段落** → 退回三轨散图
- 纯叙事/纯情绪 → 四格 or 情绪图

## 核心铁律：锁结构，不是凑版面

海报最大的坑：先想好看的多panel版式，再把内容往格子里塞 → 出来好看但漏关键点/机制画错。反过来做：

1. 先列要进版的N个点（回原文grep，每个panel = 一个真实步骤/对比项/部件）
2. 每个点锁死"这格非画不可的真实部件/数字/标注"
3. **最后**才给每格配一个最小场景 + 编号 + 短标注

先有结构清单，再有版式。版式服务结构，不是结构迁就版式。

## 版式配方（按内容自然形状选）

- **纵向编号流程（3:4）**：N步从上到下堆叠，大号编号01–0N，左侧一条贯穿流程线串起；每格一个最小场景。适合管线/步骤流。
- **横向流程/左右对比（4:3）**：数据左→右流动，or左右分栏A vs B。适合"输入→处理→输出"、双方案对比。
- **网格矩阵（1:1/4:3）**：M×N格每格一个单元。适合功能矩阵/能力对比。
- ⛔ 手机封顶4:3，不上16:9
- ⛔ 比例数值与prompt第一句方向词联动

## 耳东角色适配

⚠️ **耳东不能走"小·嵌入"模式**——纸盒角色缩小后特征丢失，必须保持足够大小。

| 图类型 | 耳东占比 | 说明 |
|--------|---------|------|
| 海报（总览） | 每格20-30% | 角色在每格做动作，但不占C位 |
| 海报（流程线） | 沿流程线分布 | 角色从A走到B，串联各步骤 |

**适配方案**：
- 海报中耳东做"讲解员/行动者"，不是"装饰"
- 每格耳东做该步骤的核心动作，保持纸盒特征可见
- 流程线海报中耳东从起点走到终点，串联各步骤
- 颜色保持style-dna：黑色70% / 红橙色30%

## 文字

每格只放短标注：大号编号 + 关键词（3-6字）+ 一句注脚（≤12字）。长机制/公式/大段说明放不进海报——要讲深的拆回解释图轨。

## prompt 骨架

```
A modern infographic poster, [VERTICAL 3:4 / HORIZONTAL 4:3 / SQUARE 1:1] layout, titled "[中文标题]", minimalist hand-drawn ink line art style. [N]-panel layout, [纵向堆叠/左→右流动/网格]:
  Panel 01 [关键词] — [耳东] [最小动作 embodying 该步], label "[短标注]"
  Panel 02 ...
  ...
Each panel features the character (two stacked square paper boxes with rectangular black-frame glasses on top box only) performing the core action for that step, ~20-30% of each panel. Strong typography: one bold Chinese headline, large two-digit numbers 01–0N, short Chinese labels. Large negative space, thin connecting flow arrows. Orange/red accents ONLY on the key path. Hand-drawn rough ink lines, paper box texture, white background.

Negative: realistic lighting, 3D, glossy, painterly, anime, childish mascot, excessive color, busy background, cluttered layout, heavy gradients, illegible typography, wrong count of items.
```

## 生成 + 自检

- **基准图先行**：多panel海报信息多，先生1张确认版面/数量/标注对不对再定
- ⛔ **数量自检**：海报最易错"该8个画成7个"。生成后逐一数：panel数对不对？
- **短板诚实告知用户**：海报焊死一块，改一处重生成整张，不能单独复用panel

## 与三轨的关系

- 一篇可以：**1张海报（开头导览）+ N张三轨散图（各段深入）**。海报是地图，散图是景点。
- 一篇别：整篇只有一张海报扛所有——读者点不进细节。海报≠偷懒把N张图合一张省事。
