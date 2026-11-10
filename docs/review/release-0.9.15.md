# 凿壁 ChiselWall v0.9.15

Windows / Linux x86_64 免安装包。

## 本版更新

- 随手问：发送后立即显示问题与“正在智能改写”状态；
- 智能改写结果默认折叠展示，可展开查看同义词组/补充问句；
- 修复多轮问答消息被 flex 压缩、中间列没有外层滚动的问题；
- AI 配置占位符说明中文化；
- 查询计划更名为「查询智能改写」，移除 P2 标记；
- 新建提问组上移到组列表上方；
- 本地 API token 弹窗增加校验与错误提示。

## 资产

- `chiselwall-0.9.15-win64.zip`（128,363,097 B）
  SHA256: `a1782e57e0f6bda85fd1eb13b42e61ec2a1c959c1e7bbe16ed37ad136ffb8879`
- `chiselwall-0.9.15-linux-x86_64.tar.gz`（124,627,043 B）
  SHA256: `ae584299ee07eae3f8534277d41783cd0d1f12f6ae45f8cb20fb3e0db67ca82d`

## 验证

- Windows/Linux 打包 node 启动后 `/api/health` 返回 `0.9.15`；
- 全量 320 项测试通过。