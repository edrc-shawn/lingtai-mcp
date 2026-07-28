# notify.ps1 - TRAE 任务完成通知
# 功能：播放提示音 + 显示系统气泡通知 + 聚焦 TRAE 窗口
# 用法：powershell -ExecutionPolicy Bypass -File notify.ps1 [-Title "标题"] [-Message "内容"]

param(
    [string]$Title = "TRAE 任务完成",
    [string]$Message = "AI 已完成任务，请回来查看结果"
)

# 1. 播放系统提示音
try {
    [System.Media.SystemSounds]::Exclamation.Play()
} catch {
    # 静默失败，继续后续步骤
}

# 2. 显示 Windows 气泡通知
try {
    Add-Type -AssemblyName System.Windows.Forms
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.BalloonTipTitle = $Title
    $notify.BalloonTipText = $Message
    $notify.Visible = $true
    $notify.ShowBalloonTip(5000)
    Start-Sleep -Seconds 6
    $notify.Dispose()
} catch {
    # 气泡通知失败不影响声音和窗口聚焦
}

# 3. 聚焦 TRAE 窗口（尝试多种窗口标题模式）
try {
    $wshell = New-Object -ComObject WScript.Shell
    $patterns = @('TRAE Work', 'TRAE', 'Trae', 'trae')
    $activated = $false
    foreach ($pattern in $patterns) {
        if ($wshell.AppActivate($pattern)) {
            $activated = $true
            break
        }
    }
    # 如果标题匹配失败，尝试按进程名查找窗口句柄
    if (-not $activated) {
        Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class Win32Helper {
            [DllImport("user32.dll")]
            public static extern bool SetForegroundWindow(IntPtr hWnd);
            [DllImport("user32.dll")]
            public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
            [DllImport("user32.dll")]
            public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
        }
"@
        $traeProcess = Get-Process | Where-Object {
            $_.ProcessName -match 'trae|Trae|TRAE' -and $_.MainWindowHandle -ne 0
        } | Select-Object -First 1
        if ($traeProcess) {
            [Win32Helper]::ShowWindow($traeProcess.MainWindowHandle, 9)  # SW_RESTORE
            [Win32Helper]::SetForegroundWindow($traeProcess.MainWindowHandle)
        }
    }
} catch {
    # 窗口聚焦失败不影响声音通知
}
