#!/usr/bin/env bash
# Ноутбук владельца — выход «Моей корзины» к сетям, которые не пускают сервер.
#
# ЗАЧЕМ. Пятёрочка и Дикси отказывают серверу по его адресу: это адрес дата-центра.
# С домашнего интернета те же сети показывают витрину или, самое большее, проверку
# «я не робот», которую проходит человек (замер 23.09.2026). Ноутбук держит ОДНО
# исходящее ssh-соединение с сервером, и сервер ходит через него к этим сетям так,
# как ходил бы владелец из дома. Входящих портов дома не открывается.
#
# ВПН на ноутбуке быть не должно: иначе сети увидят адрес ВПН, а не дом.
#
# СЛУЖБА ПОЛЬЗОВАТЕЛЯ, А НЕ СИСТЕМЫ, и потому без sudo: скрипт ставится и по ssh,
# без пароля. Живёт она, пока пользователь вошёл в систему; чтобы туннель
# поднимался и после перезагрузки без входа, нужен один раз
# `sudo loginctl enable-linger <пользователь>` — скрипт скажет, если это не сделано.
#
# Что делает: заводит ключ только для туннеля, ставит службу, которая держит
# соединение и поднимает его после обрыва, и печатает открытый ключ — его
# добавляют на сервер (tools/tunnel/server-setup.sh).
#
#   bash laptop-setup.sh <адрес сервера>
set -euo pipefail

SERVER="${1:?укажите адрес сервера: bash laptop-setup.sh <адрес>}"
KEY="$HOME/.ssh/korzina_tunnel"
PORT=1080
UNITS="$HOME/.config/systemd/user"

mkdir -p "$HOME/.ssh" "$UNITS"
chmod 700 "$HOME/.ssh"
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "korzina-tunnel@$(hostname)" -f "$KEY"

# -R без адреса назначения — это SOCKS-выход на стороне сервера (OpenSSH 7.6+):
# сервер отдаёт запрос в туннель, а в интернет он уходит отсюда, из дома.
cat > "$UNITS/korzina-tunnel.service" <<EOF
[Unit]
Description=Moya korzina: exit to closed chains via home internet

[Service]
ExecStart=/usr/bin/ssh -N -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -R 127.0.0.1:$PORT korzina-tunnel@$SERVER
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload

echo
echo "Готово. Этот ОТКРЫТЫЙ ключ не секрет — его добавляют на сервер:"
echo
cat "$KEY.pub"
echo
echo "Когда ключ добавлен, включите туннель:  systemctl --user enable --now korzina-tunnel"
echo "Проверить, что он держится:            systemctl --user status korzina-tunnel"
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "Чтобы туннель поднимался и без входа в систему: sudo loginctl enable-linger $USER"
fi
