# 凿壁 ChiselWall · src 代码说明（M2 本地核心）

## 目录

    src/
      connectors/   连接器协议与实现（Directory/Obsidian：txt/md/docx/xlsx/pdf/csv/元宝 json；
                    Siyuan：.sy=Lute AST 直读，Spec 2-6 兼容，加密笔记本跳过并记录）
      indexer/      切分（chunker）、jieba 分词（jieba_util）、索引库（db）、
                    索引管线（indexer）、增量监视（watcher）、资源控制（resources）
      searcher/     本地搜索（topk/bm25/路由兜底/上下文片段）
      api/          FastAPI 本地接口（127.0.0.1 + token）
      ui/           Vue3 CDN 最小 Web UI（无构建工具）
      node/         NodeAgent 组装与入口
    run.py          入口：python run.py

## 运行

    pip install -r requirements.txt
    python run.py                      # 默认 http://127.0.0.1:8317
    python run.py --db <path> --port 8317 --debounce 4 --cpu-threshold 70

首启在控制台打印本地 API token（存于 ~/.chiselwall/config.json），
UI 首次请求 401 后弹出输入框，输入一次即记住（localStorage）。

## 测试

    python tests/make_fixtures.py      # 幂等生成测试数据（自建，无真实数据）
    python -m unittest discover -s tests -p "test_*.py"

## 硬性红线（实现落点）

    ① 只读：连接器只 open('rb')，绝不修改/移动/复制用户笔记
    ② chiselwall.db 默认在 ~/.chiselwall/，拒绝把索引库所在目录挂载进去（node/agent.py）
    ③ 资源：索引单线程 worker、进程低优先级（indexer/resources.py）、空闲调度、防抖
    ④ 失败隔离：单文件 ParseError 记入失败列表，不阻断整体索引
    ⑤ 排除即不可见：扫描/监视/全文获取三处统一判定（excludes.py + agent._verified_doc）

## §16 预留项登记（只登记、不动工）

    - trigram 兜底表：建表 + 查询路由就绪（P1），但默认关闭（--trigram 开启）、
      开启时仅对总字符数 ≤100K 的小文档写入（体积保护，F2 评审决策）；idf 截断为后续优化
    - sqlite-vec 向量表 / 语义搜索：schema 未建，M5 可选开关
    - SiyuanConnector（.sy 直读）：M3
    - Notion/Youdao/LinkConnector：M6 后；.doc/.xls/.ppt 旧格式：明确不做
    - 部门自定义词典（jieba.load_userdict）：词典更新须触发全量重索引（jieba_util 注释）
    - 「只出标题」权限分级：docs 表排除位预留扩枚举
    - 分组/提问组/黑名单/relay/限流：M4 起
    - UI 生产打包（Vue CDN 本地内置、PyInstaller）：M6

## 资源达标线自查（评审结论①，4C8G 参考机）

    - 空闲 CPU<1% / RSS≤200MB：仅 uvicorn + 单 SQLite 连接 + watchdog 线程；
      空闲时无任务、无轮询索引（事件驱动），实测见验收文档
    - 索引期 CPU≤50% / 峰值≤1GB：单线程 + 低优先级 + 空闲调度 + 大文件哈希采样
    - 检索 P50≤30ms/P95≤100ms：FTS5 进程内毫秒级
    - 索引库≤索引文本 50%：FTS5 词级索引约 30%（detail 默认，保 bm25 排序）
