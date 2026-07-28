---
name: "leisure-mode"
description: "Opens a bookmarked video for user and notifies via sound when long tasks complete, enabling hands-off workflow. Invoke when starting multi-step tasks or operations exceeding 30 seconds."
---

# 休闲模式（不做 AI 的监工）

## 理念

用户发起任务后可以切去看视频，AI 自动从书签片单随机打开一个视频，完成任务后发出通知（声音 + 系统通知 + 窗口聚焦）把用户叫回来。用户不需要盯着 AI 干活。

## 前置准备（一次性）

用户需在 Chrome 或 Edge 书签栏创建名为「休闲片单」的文件夹，把爱看的视频链接放进去（B 站 UP 主主页、YouTube 频道、抖音收藏等）。AI 只读这个文件夹，不碰其他书签。

## 触发条件

当检测到以下任一情况时激活：
- 任务涉及多步骤操作（多文件编辑、运行脚本、搜索 + 分析、构建项目）
- 预计执行时间超过 30 秒
- 用户明确表示要离开（"你先忙着""我先去干别的""弄好了叫我"）

**不触发**：简单问答、单文件小改、30 秒内能完成的操作。

## 执行流程

### 第一步：打开休闲视频 + 提示

执行书签脚本，从「休闲片单」随机打开一个视频：

```powershell
python "c:\Obsidian仓库\edrc\灵台\.trae\skills\leisure-mode\open_leisure.py"
```

参数选项：
- `--browser edge`：用 Edge 书签（默认 Chrome）
- `--folder 自定义名`：指定其他书签文件夹名（默认「休闲片单」）
- `--dry-run`：只输出选中 URL 不打开（调试用）

脚本退出码：0=成功打开，2=找不到文件夹（提示用户建），3=文件夹为空。

打开后回复开头加一句提示：

> 任务开始，已为你打开「{视频名}」。这个任务可能需要 X 分钟，完成后我会通知你。

**脚本报错处理**：如果返回退出码 2（找不到文件夹），跳过打开视频步骤，直接提示：

> 没找到「休闲片单」书签文件夹，先去浏览器建一个放爱看的视频链接。任务开始，完成后我会通知你。

### 第二步：正常执行任务

按常规流程执行，不需要中途汇报进度。专注干活，不水回复。

### 第三步：任务完成时

执行通知脚本（三选一，按优先级尝试）：

**方式 A — 调用脚本文件（推荐）：**

```powershell
powershell -ExecutionPolicy Bypass -File "c:\Obsidian仓库\edrc\灵台\.trae\skills\leisure-mode\notify.ps1"
```

**方式 B — 内联执行（脚本文件不可用时）：**

```powershell
powershell -Command "[System.Media.SystemSounds]::Exclamation.Play(); Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; $n.BalloonTipTitle = 'TRAE 任务完成'; $n.BalloonTipText = 'AI 已完成任务，请回来查看'; $n.Visible = $true; $n.ShowBalloonTip(5000); Start-Sleep 6; $n.Dispose(); $w = New-Object -ComObject WScript.Shell; $w.AppActivate('TRAE')"
```

**方式 C — 最简版（仅声音，兜底）：**

```powershell
powershell -Command "[System.Media.SystemSounds]::Exclamation.Play()"
```

### 第四步：任务失败/需要用户输入时

同样执行通知脚本，但将通知标题改为"任务需要你介入"。让用户知道该回来看了。

## 限制说明

| 能力 | 状态 | 说明 |
|------|------|------|
| 完成时播放提示音 | ✅ 可靠 | 系统级 API，稳定 |
| 显示系统气泡通知 | ✅ 可靠 | Windows NotifyIcon，稳定 |
| 聚焦 TRAE 窗口 | ⚠️ 基本可用 | 依赖窗口标题匹配，偶尔可能失效；声音通知是可靠后备 |
| 从书签打开视频 | ✅ 可靠 | 读 Chrome/Edge 书签 JSON，os.startfile 调系统浏览器 |
| 自动切换到休闲应用 | ❌ 不可行 | Skill 无法控制其他应用（浏览器、视频播放器等） |
| 自动暂停休闲进度 | ❌ 不可行 | 无法暂停视频/游戏等外部应用的播放进度 |

**核心价值**：任务开始时自动打开视频 + 任务完成时声音通知把人叫回来。Skill 的角色是"安排休闲 + 叫人回来"，不是"替人操作其他应用"。

## 注意事项

- 通知脚本执行时间约 6 秒（气泡显示需要 Sleep），属于正常行为
- 如果用户说"别通知了""安静模式"，本会话跳过通知步骤
- 一次任务只通知一次，不要中途多次触发
