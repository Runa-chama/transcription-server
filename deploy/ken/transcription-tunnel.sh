#!/usr/bin/env bash
set -u

SSH=/usr/bin/ssh
KEY="$HOME/.ssh/xserver"
TARGET=root@runachama.xvps.jp
PORT=55126

while true; do
    "$SSH" -N \
        -i "$KEY" \
        -o BatchMode=yes \
        -o ExitOnForwardFailure=yes \
        -o ConnectTimeout=15 \
        -o ServerAliveInterval=20 \
        -o ServerAliveCountMax=3 \
        -o TCPKeepAlive=yes \
        -R 127.0.0.1:36419:127.0.0.1:36419 \
        -p "$PORT" \
        "$TARGET"
    sleep 10
done
