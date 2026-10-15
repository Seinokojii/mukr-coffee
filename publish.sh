#!/bin/bash
# Выкладка сервера МУКР Кофе на VPS: код → /opt/site15/mukr-coffee, база data/ не трогается.
# Страница на GitHub Pages обновляется сама после git push.
set -e; cd "$(dirname "$0")"
K="ssh -i $HOME/.ssh/manty_vps -o ConnectTimeout=15 root@62.113.109.193"
tar -czf - server.py index.html app.css app.js icon.svg manifest.webmanifest vendor | $K 'set -e; mkdir -p /opt/site15/mukr-coffee/data && cd /opt/site15/mukr-coffee && tar -xzf - && chown -R site15:site15 /opt/site15/mukr-coffee && systemctl restart site15-mukrcoffee && echo выложено'
