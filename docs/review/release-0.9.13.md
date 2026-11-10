# 凿壁 ChiselWall v0.9.13

Windows / Linux x86_64 免安装包。

## 本轮包含

- AI 综合 SSE 流式输出、上下文预算、逐轮 citations；
- AI 自定义网关（模板占位符 + 本地文件 + 自定义请求头）；
- 发送前查询智能改写（同义词 OR、软 AND 覆盖度、原问题兜底）；
- 追问「重新检索」与无上一轮结果自动新检索；
- 单篇私有/隐藏/共享、挂载暂停、提问组独立设置、在线成员；
- Linux 思源 inotify watch 范围收窄，避免队列溢出循环；
- UI：可折叠侧栏、搜索结果清除、设置保存区统一、token 校验错误提示。

## 资产

- `chiselwall-0.9.13-win64.zip`（122.4 MB）
  SHA256: `12e45cfca146d6cc6d859917c332cadc07ddca27c6b699e5b8377772b26e05b9`
- `chiselwall-0.9.13-linux-x86_64.tar.gz`（118.9 MB）
  SHA256: `7160b9ea15f641ce5bb897c2574c6860dab4dcaea6fac5a0dfdc125be14cc7d1`

## Windows

解压后双击 `启动节点.cmd`；relay 机器双击 `启动relay.cmd`。

## Linux

```bash
tar -xzf chiselwall-0.9.13-linux-x86_64.tar.gz
cd chiselwall-0.9.13
./start-node.sh
```

## 已验证

- Windows node/relay 可执行文件启动并通过 `/api/health`；
- Linux node/relay（WSL Ubuntu）启动并通过 `/api/health`；
- 源码 320 项测试通过。