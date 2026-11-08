"""relay 管理员命令（M6）：发邀请、查看用户、吊销用户。

用法（在 relay 机器上）：
  python -m relay.admin invite 张三 --group 数据库组 --group 平台组 --url ws://10.0.0.5:18760/ws
  python -m relay.admin list
  python -m relay.admin revoke 张三

说明：
- 邀请信息由你复制发给同事（微信/邮件），同事在「设置 → Relay 连接」粘贴。
- 修改用户后 relay 会热加载（同事下次连接即生效），无需重启。
"""
from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG = "relay.json"


def load(path: Path, *, allow_missing: bool = False) -> dict:
    if not Path(path).exists() and allow_missing:
        return {"users": {}, "rate": 0.2, "burst": 2}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        raise SystemExit("配置格式错误：需要 users 字段（nick -> token）")
    return data


def save(path: Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _groups_path(config: Path) -> Path:
    return config.with_name(config.stem + "-groups.json")


def _ensure_groups(config: Path, names: list[str]) -> None:
    """把邀请里的组写入组注册表（无描述），保证同事能看到这些组。"""
    path = _groups_path(config)
    groups: dict = {}
    if path.exists():
        try:
            groups = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            groups = {}
    changed = False
    for name in names:
        name = name.strip()
        if name and name not in groups:
            groups[name] = {"description": "", "created_by": "admin", "created_at": ""}
            changed = True
    if changed:
        path.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_invite(args: argparse.Namespace) -> None:
    config = Path(args.config)
    data = load(config, allow_missing=True)
    users = data["users"]
    token = secrets.token_urlsafe(24)
    users[args.nick] = token
    save(config, data)
    groups = [g.strip() for g in (args.group or []) if g.strip()]
    if groups:
        _ensure_groups(config, groups)
    url = args.url or "ws://127.0.0.1:18760/ws"
    print("===== 把下面内容发给「%s」 =====" % args.nick)
    print("凿壁 ChiselWall 邀请")
    print("  昵称：%s" % args.nick)
    print("  relay 地址：%s" % url)
    print("  token：%s" % token)
    if groups:
        print("  建议加入组：%s" % "、".join(groups))
    print("使用方法：打开凿壁 → 设置 → Relay 连接，填地址/昵称/token，勾选组 → 保存并连接。")
    print("====================================")


def cmd_list(args: argparse.Namespace) -> None:
    data = load(Path(args.config))
    print("用户数：%d" % len(data["users"]))
    for nick, token in sorted(data["users"].items()):
        masked = ("****" + token[-4:]) if len(token) > 4 else "****"
        print("  %-16s %s" % (nick, masked))


def cmd_revoke(args: argparse.Namespace) -> None:
    config = Path(args.config)
    data = load(config)
    if args.nick not in data["users"]:
        raise SystemExit("没有这个用户：%s" % args.nick)
    data["users"].pop(args.nick)
    save(config, data)
    print("已吊销「%s」的 token（其已连接会话在新连接时失效）" % args.nick)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="凿壁 relay 管理员命令")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="relay 配置路径（默认 relay.json）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_invite = sub.add_parser("invite", help="生成邀请（自动创建 token）")
    p_invite.add_argument("nick")
    p_invite.add_argument("--group", action="append", default=[], help="建议加入的组（可多次）")
    p_invite.add_argument("--url", default="", help="relay 地址（默认 ws://127.0.0.1:18760/ws）")
    p_invite.set_defaults(func=cmd_invite)

    p_list = sub.add_parser("list", help="查看用户")
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser("revoke", help="吊销用户 token")
    p_revoke.add_argument("nick")
    p_revoke.set_defaults(func=cmd_revoke)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
