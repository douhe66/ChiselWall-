"""把已用 PyInstaller 构建好的 node/relay 目录组装成发行包。

用法：
    python packaging/assemble_release.py --version 0.9.23 --platform win
    python packaging/assemble_release.py --version 0.9.23 --platform linux
    python packaging/assemble_release.py --version 0.9.23 --platform all

Windows 输入：dist/release-<version>/{chiselwall-node,chiselwall-relay}
Linux 输入：dist-linux/release-<version>/{chiselwall-node,chiselwall-relay}
"""
from __future__ import annotations

import argparse
import shutil
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _write_cmds(rel: Path) -> None:
    (rel / "启动节点.cmd").write_bytes(
        '@echo off\r\ncd /d "%~dp0"\r\nstart "" "chiselwall-node\\chiselwall-node.exe"\r\n'.encode("utf-8")
    )
    relay = (
        "@echo off\r\ncd /d \"%~dp0\"\r\n"
        "if not exist \"relay.json\" (\r\n"
        "  echo No relay.json, initializing...\r\n"
        "  \"chiselwall-relay\\chiselwall-relay.exe\" --init\r\n"
        "  echo Edit relay.json or use admin invite, then run again.\r\n"
        "  pause\r\n"
        "  exit /b 0\r\n"
        ")\r\n"
        "\"chiselwall-relay\\chiselwall-relay.exe\" --config relay.json --host 0.0.0.0 --port 18760\r\n"
        "pause\r\n"
    )
    (rel / "启动relay.cmd").write_bytes(relay.encode("utf-8"))


def package_windows(version: str) -> Path:
    rel = ROOT / "dist" / ("release-" + version)
    if not (rel / "chiselwall-node").is_dir() or not (rel / "chiselwall-relay").is_dir():
        raise SystemExit("缺少 Windows 构建目录：" + str(rel))
    _write_cmds(rel)
    shutil.copy2(ROOT / "packaging" / "README-dist.txt", rel / "使用说明.txt")
    for name in ("relay-watchdog.ps1", "install-relay-task.ps1", "README-windows-service.md"):
        shutil.copy2(ROOT / "packaging" / "windows" / name, rel / name)
    out = ROOT / "dist" / ("chiselwall-%s-win64.zip" % version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(rel.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(rel).as_posix())
    return out


def package_linux(version: str) -> Path:
    rel = ROOT / "dist-linux" / ("release-" + version)
    if not (rel / "chiselwall-node").is_dir() or not (rel / "chiselwall-relay").is_dir():
        raise SystemExit("缺少 Linux 构建目录：" + str(rel))
    shutil.copy2(ROOT / "packaging" / "README-dist.txt", rel / "README.txt")
    (rel / "start-node.sh").write_text(
        '#!/usr/bin/env bash\ncd "$(dirname "$0")"\nexec ./chiselwall-node/chiselwall-node "$@"\n',
        encoding="utf-8",
    )
    (rel / "start-relay.sh").write_text(
        '#!/usr/bin/env bash\ncd "$(dirname "$0")"\nexec ./chiselwall-relay/chiselwall-relay "$@"\n',
        encoding="utf-8",
    )
    (rel / "README-linux.txt").write_text(
        "凿壁 ChiselWall Linux x86_64（v%s）\n\n"
        "1. 启动节点：./start-node.sh\n"
        "2. 浏览器打开 http://127.0.0.1:8317\n"
        "3. relay 机器：./start-relay.sh；也可用 systemd/install-relay-service.sh 守护\n"
        "4. 数据目录：~/.chiselwall/\n" % version,
        encoding="utf-8",
    )
    (rel / "start-node.sh").chmod(0o755)
    (rel / "start-relay.sh").chmod(0o755)
    systemd_src = ROOT / "packaging" / "systemd"
    systemd_dst = rel / "systemd"
    if systemd_dst.exists():
        shutil.rmtree(systemd_dst)
    shutil.copytree(systemd_src, systemd_dst)
    (systemd_dst / "install-relay-service.sh").chmod(0o755)
    out = ROOT / "dist-linux" / ("chiselwall-%s-linux-x86_64.tar.gz" % version)
    with tarfile.open(out, "w:gz") as tf:
        for p in sorted(rel.rglob("*")):
            if p.is_file():
                info = tf.gettarinfo(str(p), arcname=str(p.relative_to(rel)))
                if p.name.endswith(".sh"):
                    info.mode = 0o755
                with open(p, "rb") as fh:
                    tf.addfile(info, fh)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--platform", choices=["win", "linux", "all"], default="all")
    args = ap.parse_args()
    if args.platform in ("win", "all"):
        print(package_windows(args.version))
    if args.platform in ("linux", "all"):
        print(package_linux(args.version))


if __name__ == "__main__":
    main()
