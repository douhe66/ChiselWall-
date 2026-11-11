# Windows：后台守护 relay

Windows 没有 systemd。推荐用“计划任务 + watchdog 脚本”：

- 开机启动；
- relay 异常退出后 5 秒自动重启；
- relay 自身日志写到同目录 logs\relay.log；
- watchdog 日志写到 logs\relay-watchdog.log。

## 一键安装（管理员 PowerShell）

解压发行包后，在发行包根目录执行：

    powershell -ExecutionPolicy Bypass -File .\install-relay-task.ps1

脚本会注册计划任务 ChiselWall Relay，并以 SYSTEM 开机启动。

卸载：

    powershell -ExecutionPolicy Bypass -File .\install-relay-task.ps1 -Uninstall

## 前置条件

1. 发行包根目录有 relay.json（没有就先初始化）：

       .\chiselwall-relay\chiselwall-relay.exe --init

2. 用 admin 命令发出邀请，或手工编辑 relay.json。

## 查看状态

    Get-ScheduledTask -TaskName "ChiselWall Relay"
    Get-Process chiselwall-relay
    Get-Content .\logs\relay.log -Tail 50
    Get-Content .\logs\relay-watchdog.log -Tail 50

## 更正式：NSSM 注册为 Windows 服务

如果机器上已有 NSSM：

    nssm install ChiselWallRelay "C:\path\chiselwall\chiselwall-relay\chiselwall-relay.exe" "--config C:\path\chiselwall\relay.json --host 0.0.0.0 --port 18760"
    nssm set ChiselWallRelay AppDirectory "C:\path\chiselwall"
    nssm set ChiselWallRelay Start SERVICE_AUTO_START
    nssm set ChiselWallRelay AppStdout "C:\path\chiselwall\logs\relay.log"
    nssm set ChiselWallRelay AppStderr "C:\path\chiselwall\logs\relay.log"
    nssm start ChiselWallRelay

NSSM 的 AppExit 默认会重启进程；也可在 nssm edit 中设置重启策略。

## 注意

- 计划任务方案需要管理员权限；
- 防火墙放行 18760/TCP；
- relay 只路由，不存知识，日志不记录问题正文或 token。
