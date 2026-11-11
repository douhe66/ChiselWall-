# Linux：systemd 守护 relay

发行包解压目录下应有：

    start-relay.sh
    chiselwall-relay/
    systemd/
      chiselwall-relay.service.template
      install-relay-service.sh
      README-linux-service.md

## 一键安装

假设解压在 /opt/chiselwall，配置在 /var/lib/chiselwall/relay.json：

    cd /opt/chiselwall/systemd
    sudo ./install-relay-service.sh /opt/chiselwall /var/lib/chiselwall/relay.json

默认创建系统用户 chiselwall，安装到：

    /etc/systemd/system/chiselwall-relay.service

日志：

    journalctl -u chiselwall-relay -f
    tail -f /var/log/chiselwall/relay.log

## 常用命令

    sudo systemctl status chiselwall-relay
    sudo systemctl restart chiselwall-relay
    sudo systemctl stop chiselwall-relay
    sudo systemctl disable chiselwall-relay

## 注意

- relay.json 所在目录必须让服务用户可写，因为组注册表会写到同目录；
- 首次生成配置：

      ./chiselwall-relay/chiselwall-relay --init --config /var/lib/chiselwall/relay.json

- Restart=always 会在异常退出后 5 秒重启；
- relay 只做路由，不存知识；日志不记录问题正文或 token。
