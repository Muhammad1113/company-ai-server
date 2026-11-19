#!/usr/bin/env bash
# Ubuntu firewall: default-deny inbound, sirf SSH + 80/443; llama (8081/8082) sirf Docker subnet se.
#   sudo ./deploy/ubuntu/ufw.sh            (SSH_FROM=10.0.0.0/8 ./ufw.sh se SSH ko sirf apne LAN tak rokho)
# Docker ka PITFALL: published ports (docker-compose `ports:`) ufw ko bypass karte hain. Is liye production compose
# mein sirf web (80/443) ports publish karta hai; postgres/api ke `ports:` kabhi mat lagao.
set -euo pipefail
SSH_FROM="${SSH_FROM:-any}"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$SSH_FROM" to any port 22 proto tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow from 172.16.0.0/12 to any port 8081 proto tcp   # Docker containers -> chat model
ufw allow from 172.16.0.0/12 to any port 8082 proto tcp   # Docker containers -> embedding model
ufw --force enable
ufw status verbose
echo "Ab DOOSRI machine se check karo: 8081/8082/5432/8000 fail, 443 pass (nmap -p 22,80,443,5432,8000,8081,8082 <server-ip>)."
