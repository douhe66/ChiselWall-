# 凿壁 ChiselWall v0.9.23

Windows / Linux x86_64 免安装包。

## 本版更新

### 文件日志
- 节点默认写 `%USERPROFILE%\.chiselwall\logs\node.log`；
- relay 默认写 `relay.json` 同目录 `logs/relay.log`；
- 控制台 + UTF-8 文件双输出，单文件 10MB、保留 5 个备份；
- 新增 `--log-file`、`--log-level`；
- 补充启动/退出、挂载/共享、索引、敏感词扫描、API 慢请求/异常、relay 连接/断开/鉴权失败/限流/查询路由等日志；
- 日志不记录问题正文和 token。

### relay 守护
- Linux：`systemd/chiselwall-relay.service.template` + `install-relay-service.sh`，`Restart=always`；
- Windows：`install-relay-task.ps1` + `relay-watchdog.ps1`，计划任务开机启动、异常退出 5 秒重启；
- Windows 也可用 NSSM 注册为服务，见 `README-windows-service.md`。

### 其他
- 发行包组装脚本 `packaging/assemble_release.py`；
- 全量 334 项测试通过。

## 资产

- `chiselwall-0.9.23-win64.zip`（128,401,643 B）
  SHA256: `12f38df96b35763599c6c92d1f45976b38dfaacc565e03fa3eea80aaf086a162`
- `chiselwall-0.9.23-linux-x86_64.tar.gz`（124,160,061 B）
  SHA256: `04c6f190c4d4e566dbd5af66727b5a5a0977af95bee94722e523096365000a64`

## 验证

- Windows/Linux 打包 node 启动后 `/api/health` 返回 `0.9.23`；
- 文件日志实测写入 node.log / relay.log；
- Linux systemd 脚本 `bash -n` 通过；Windows PowerShell 脚本解析通过。