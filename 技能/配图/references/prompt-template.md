---
标题: Prompt 模板
日期: 2026-06-30
---

# Prompt 模板

> 填空式 prompt，确保每张图风格统一。

## 模板

```
参考图中的角色，[角色动作描述]，[物件/场景]，[颜色指示]。不是可爱的卡通，手绘粗糙墨线纸盒质感，严格只有两个纸盒叠在一起不能三个，黑色框矩形眼镜只在上盒且镜片有白色反光，下盒无五官无眼镜
```

## 填空说明

| 变量 | 填什么 | 示例 |
|:-----|:-------|:-----|
| 角色动作描述 | 耳东在做什么（从隐喻发明来） | 身体前倾微抿嘴，一只手够红色灯泡 |
| 物件/场景 | 关键物件+环境 | 红色灯泡漂浮在上方，另一只手拉绳子 |
| 颜色指示 | 非黑白的部分用什么色 | 灯泡红色，绳子黑色 |

## API 调用

```
POST https://apihub.agnes-ai.com/v1/images/generations
Authorization: Bearer {API_KEY}
{
  "model": "agnes-image-2.1-flash",
  "prompt": "[填好的模板]",
  "size": "2K",
  "ratio": "16:9",
  "extra_body": {
    "image": ["data:image/png;base64,{BASE64_ENCODED_ANCHOR}"]
  }
}
```
> ⚠️ **`requests` 直连且 prompt 轻量、依赖锚点图驱动角色时，锚点图必须放 `extra_body.image`（嵌套数组），不是顶层 `image`** —— 放顶层会被忽略、退化为纯文生图（实测：同一轻 prompt，顶层 `image` 完全丢失耳东 IP，改 `extra_body.image` 后才出角色）。若用官方 OpenAI SDK（`gen_driver.py` 方式），`extra_body` 由 SDK 客户端扁平透传为顶层 `image` 发出；且 `gen_driver` 的 prompt 本身写满角色、不依赖锚点，故两种写法对它都"够用"。**结论：走 `requests` 直连做锚点驱动（如封面）→ 必须用 `extra_body.image`；走 SDK 且 prompt 写满角色 → 顶层/extra_body 均工作，对字段位置不敏感。** 默认不传 `response_format`（Agnes 报 `UnsupportedParamsError`），默认返回图片 URL（SDK 取 `r.data[0].url`；个别情况返回 `b64_json`，驱动已兼容两种）。锚点图数组可多张：主锚点必传 + 情绪锚点可选。

API Key 通过 `.tool/config/api_keys.json` 统一管理（`api_keys.get("agnes")`）。锚点图（主）：`技能/配图/erdong/refs/00-cover.png`；情绪锚点：`技能/配图/erdong/refs/{neutral,confused,content,happy,focused}.png`（按 shot 情绪选传，主锚点必传）。批量生图走 `作品/配图/png/layered-confirm-illustrations/gen_driver.py`，manifest 驱动、强制 img2img、自动重试+断点续跑。

## 比例与方向词联动

> API 实际用 `ratio` 参数（如 `"16:9"`）配合 `size` 档位（`1K/2K/3K/4K`）控制输出比例，详见上方 API 调用示例。公众号首图用 `size:"2K"` + `ratio:"16:9"`（输出 2624×1472）。

| 比例 | 方向词 | 适用场景 |
|:-----|:-------|:---------|
| 3:4 | VERTICAL portrait | 竖内容：纵向堆叠/漏斗/阶梯/单角色立姿 |
| 4:3 | HORIZONTAL landscape | 横内容：横排并列/左右对比/时间线 |
| 1:1 | SQUARE | 方/网格/单概念 |

⛔ 手机封顶 4:3，全篇换节奏别一个比例到底。

## 生图纪律（必读，防反复踩坑）

- ⛔ **生图前必读 `技能/配图/erdong/erdong-ip.md`（IP 规格书）**：角色长相、眼镜数量与位置、配色以规格书为唯一真源；提示词直接复用其第五节英文段，**禁止凭记忆重写角色描述**（曾因规格书/模板/锚点图三源不自洽，导致角色在双纸盒↔单纸箱、眼镜一副↔两副间反复横跳）。
- ✅ **眼镜硬约束**：严格 **一副** 黑框眼镜，**只在上盒**；下盒绝无眼镜、无五官。提示词须显式写 `ONE pair ... ONLY on the top box`，防止模型画出两副或误删。
- ⛔ **QA 交人，AI 不读图自判**：生图后直接 present 给用户看；AI 只做文件存在/体积等机械校验，不做"角色漂移/构图/眼镜数量"类视觉 QA。视觉验收权归用户。
- 📋 **提示词版本留痕**：`cover_shots.json` 等 manifest 的 prompt 改动不覆盖，用版本号 shot 名（v1/v2/v3）保留可复现历史。
- ✅ **模型分工**：角色图用 Agnes（img2img 传锚点），信息图/海报用 U1 Fast（纯文生图，不用锚点），详见 `erdong/erdong-ip.md#八-生图模型对比测试记录`

## U1 Fast 信息图 Prompt 模板

> SenseNova U1 Fast 专供信息图（Infographics）生成，适合版式排版、横向对比、纵向流程、多层堆叠。不适合角色图（双纸盒结构不稳定、眼镜位置漂移）。

### 横向对比海报

```
A professional horizontal comparison infographic poster with white background.
The poster is titled at the top in bold Chinese text: [主标题].
The layout has [N] columns side by side.
LEFT column has a [颜色] header bar with title: [左栏标题].
RIGHT column has a [颜色] header bar with title: [右栏标题].
LEFT column has [N] rows each with [图标] and Chinese text: 1. [内容1] 2. [内容2] 3. [内容3].
RIGHT column has [N] rows each with [图标] and Chinese text: 1. [内容1] 2. [内容2] 3. [内容3].
Clean minimalist style, flat vector design, NO hand-drawn lines, modern infographic aesthetic, professional typography, generous white space.
```

### 纵向流程海报

```
A professional vertical flowchart infographic poster with white background.
The poster is titled at the top in bold Chinese text: [主标题].
The layout shows a vertical timeline/flow with [N] numbered steps, connected by a vertical line.
Each step has a circular numbered badge on the left ([颜色] color), and to the right a content box with a title and description.
Step 1 - title: [步骤1标题], description: [步骤1描述].
Step 2 - title: [步骤2标题], description: [步骤2描述].
Step 3 - title: [步骤3标题], description: [步骤3描述].
Clean minimalist style, flat vector design, NO hand-drawn lines, modern infographic aesthetic, professional typography, each step has a small relevant icon.
Generous white space, clean layout.
```

### API 调用

```python
import requests

API_KEY = "从 .tool/config/api_keys.json 读取 sensenova.key"
ENDPOINT = "https://token.sensenova.cn/v1/images/generations"

payload = {
    "model": "sensenova-u1-fast",
    "prompt": "[填好的模板]",
    "size": "2752x1536",
    "n": 1
}
headers = {
    "Authorization": "Bearer " + API_KEY,
    "Content-Type": "application/json"
}
r = requests.post(ENDPOINT, json=payload, headers=headers, timeout=120)
img_url = r.json()["data"][0]["url"]
# 下载图片（URL 有效期 1 小时）
img_data = requests.get(img_url, timeout=60).content
```

### U1 Fast 支持尺寸

| 尺寸 | 比例 | 适用场景 |
|:-----|:-----|:---------|
| 2752×1536 | 16:9 | 横向对比海报 |
| 2496×1664 | 3:2 | 横向信息图 |
| 1536×2752 | 9:16 | 竖版封面 |
| 2048×2048 | 1:1 | 方形信息图 |
| 1344×3136 | 9:21 | 竖版长流程 |
| 1664×2496 | 2:3 | 竖版信息图 |
| 2368×1760 | 4:3 | 横向流程 |
| 1760×2368 | 3:4 | 竖版流程 |
| 1824×2272 | 4:5 | 竖版 |
| 2272×1824 | 5:4 | 横版 |
| 3072×1376 | 21:9 | 超宽横幅 |
| 2560×720 | - | 视频封面 |
| 3072×864 | - | 超宽 |

> ⚠️ U1 Fast 返回的图片 URL 为临时访问链接，固定有效期 1 小时，超时后链接直接失效，无法再次访问图片。**必须及时下载保存**。
