凿壁 ChiselWall（免安装版）
=================================

一、同事机器（每人一份）
1. 双击「启动节点.cmd」（或 chiselwall-node\chiselwall-node.exe）；
2. 浏览器会自动打开 http://127.0.0.1:生成端口；
3. 首次需要 token：控制台窗口里有一行「API token: ...」，复制粘贴到页面弹窗；
   token 也保存在 %USERPROFILE%\.chiselwall\config.json 里，仅本机使用；
4. 「随手记」里添加要共享的笔记目录（支持目录 / 单文件 / Obsidian / 思源工作区）；
5. 「设置 → Relay 连接」里填管理员给你的地址/昵称/token，点「保存并连接」。

二、管理员（relay 机器，一次）
1. 双击「启动relay.cmd」第一次会提示生成配置；或执行：
     chiselwall-relay.exe --init
2. 发邀请（每个同事一条，token 自动生成）：
     chiselwall-relay.exe admin invite 张三 --group 全员组 --url ws://<本机内网IP>:18760/ws
   把输出的邀请文本发给同事；
3. 查看用户：chiselwall-relay.exe admin list
   吊销 token：chiselwall-relay.exe admin revoke 张三
4. 组管理：同事在各自设置页「提问组」加入/退出、查看在线成员、建组；描述所有人可见。

二·五、随手问会话（v0.9.22）
- 左侧会话列表：新建/切换/改名/删除；历史持久化；
- 首问广播给在线同事；追问默认基于上一轮结果，勾选「重新检索」可广播新问题，无上一轮结果时自动重新检索；
- 提问框旁「AI 综合」勾选：勾选=自动生成回答，不勾=先看右侧搜索结果再手动点「AI 综合」；
- 只有已加入的提问组会出现在提问面板；设置 → 提问组可查看成员。
- 右侧结果栏可拖动调宽；结果/回答各自滚动。

二·六、日志与后台守护（v0.9.24）
- 日志默认写入：%USERPROFILE%\.chiselwall\logs\node.log；relay 写到 relay.json 同目录 logs\relay.log；
  单文件 10MB，保留 5 个备份，同时输出到控制台。
- Windows relay 后台守护（管理员 PowerShell，在发行包根目录）：
    powershell -ExecutionPolicy Bypass -File .\install-relay-task.ps1
  计划任务名 ChiselWall Relay，开机启动，异常退出 5 秒后重启；卸载加 -Uninstall。
  也可以用 NSSM 注册为 Windows 服务，详见 README-windows-service.md。
- Linux relay 守护（systemd）：
    cd systemd
    sudo ./install-relay-service.sh /opt/chiselwall /var/lib/chiselwall/relay.json
  日志：journalctl -u chiselwall-relay -f 或 /var/log/chiselwall/relay.log。

三、语义搜索（可选，默认关）
- 包内已带向量模型（bge-small-zh，约 23MB）与运行库，设置 → 语义搜索 → 勾选启用 → 保存 → 重建向量索引；
- 开启后空闲内存约 200MB，索引期有少量 CPU 占用；关闭即回退关键词检索；
- 重排（rerank）默认关：优先填内网 rerank 服务地址（零安装）；本地 rerank 模型不随包分发，
  可把标准 cross-encoder 转成 ONNX 放到 models/rerank/<名字>/（格式见 models/rerank/README.md）。

四、常见问题
- 端口占用：默认节点 8317、relay 18760；Windows 排除 8160-8759，请避开该区间；
- 防火墙：首次运行 Windows 会询问，允许「专用网络」即可；
- AI：设置 → AI 配置，填 OpenAI 兼容 base_url / model / key（内网模型直接填内网地址）；
- 数据位置：%USERPROFILE%\.chiselwall\（索引库、配置、token）；升级不会删除。