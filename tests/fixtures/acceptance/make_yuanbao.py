"""元宝 JSON 验收样本生成（reviewer，幂等，精简核心集）。完整清单见 m2-testdata-plan §5.5。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

YB = Path(__file__).resolve().parent / "yuanbao"

CONV = {
    "exportTime": "2026-05-08T08:30:00",
    "conversations": [{
        "id": "uuid-cw0001", "title": "元宝标准对话",
        "messages": [
            {"role": "user", "content": "什么是 FTS5？凿壁元宝词·标准"},
            {"role": "assistant", "content": "FTS5 是 SQLite 的全文检索模块。", "thinking": "需要解释清楚"},
        ],
    }],
}

SPEECH = {
    "conversations": [{
        "id": "uuid-cw0002", "title": "元宝语音回退对话",
        "messages": [
            {"role": "user", "content": "帮我总结。凿壁元宝词·回退"},
            {"role": "assistant", "content": "", "speech": "speech 字段的回退文本内容。"},
        ],
    }],
}

BAD_JSON = '{"conversations": [{"title": "坏样本", 缺逗号'

def main() -> None:
    if YB.exists():
        shutil.rmtree(YB)
    YB.mkdir(parents=True)
    (YB / "标准对话.json").write_text(json.dumps(CONV, ensure_ascii=False), encoding="utf-8")
    (YB / "speech回退.json").write_text(json.dumps(SPEECH, ensure_ascii=False), encoding="utf-8")
    # md 孪生：与标准对话内容一致的 md 导出
    (YB / "标准对话孪生.md").write_text(
        "# 元宝对话导出\n\n## 导出时间：2026-05-08 08:30:00\n\n### [我]\n什么是 FTS5？凿壁元宝词·标准\n\n### [元宝]\nFTS5 是 SQLite 的全文检索模块。\n",
        encoding="utf-8")
    # 反例：普通配置 JSON（非对话结构）
    (YB / "普通配置.json").write_text('{"app": "chiselwall", "port": 8317, "凿壁元宝词·反例": "x"}', encoding="utf-8")
    # 坏 JSON
    (YB / "坏样本.json").write_text(BAD_JSON, encoding="utf-8")
    # 对照：另一场对话（不同内容）
    (YB / "对照对话.json").write_text(json.dumps({
        "conversations": [{"id": "uuid-cw0003", "title": "元宝对照对话",
                           "messages": [{"role": "user", "content": "凿壁元宝词·对照 独立内容。"}]}],
    }, ensure_ascii=False), encoding="utf-8")
    # 自检
    ok = all((YB / f).exists() for f in ["标准对话.json", "speech回退.json", "标准对话孪生.md", "普通配置.json", "坏样本.json", "对照对话.json"])
    print("生成 yuanbao 样本:", "OK" if ok else "FAIL")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
