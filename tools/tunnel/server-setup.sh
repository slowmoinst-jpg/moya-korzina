#!/usr/bin/env bash
# Сервер: пустить ноутбук владельца держать туннель — и больше ничего.
#
# Ноутбук входит отдельным пользователем без оболочки, и его ключ умеет ровно одно:
# открыть на сервере 127.0.0.1:1080 — выход в интернет через дом владельца
# (tools/tunnel/laptop-setup.sh). restrict снимает с ключа всё, port-forwarding
# возвращает только пересылку, permitlisten сужает её до одного порта, а
# permitopen закрывает обратную дорогу — с ноутбука в сеть сервера (-L и -D).
# Команд ключ не выполняет. Слушает туннель только внутри сервера, снаружи его не видно.
#
#   bash server-setup.sh 'ssh-ed25519 AAAA… korzina-tunnel@ноутбук'
set -euo pipefail

KEY="${1:?нужен открытый ключ ноутбука: строка «ssh-ed25519 …»}"
case "$KEY" in
    "ssh-ed25519 "*) ;;
    *) echo "это не открытый ключ ssh-ed25519" >&2; exit 1 ;;
esac
NAME=korzina-tunnel
PORT=1080

id "$NAME" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$NAME"
# «*» вместо «!»: пароля нет и войти по нему нельзя, но запертой учётка не считается —
# иначе sshd без PAM отказал бы и ключу.
usermod -p '*' "$NAME"
install -d -m 700 -o "$NAME" -g "$NAME" "/home/$NAME/.ssh"
printf 'restrict,port-forwarding,permitlisten="127.0.0.1:%s",permitopen="127.0.0.1:1",command="/bin/false" %s\n' \
    "$PORT" "$KEY" > "/home/$NAME/.ssh/authorized_keys"
chown "$NAME:$NAME" "/home/$NAME/.ssh/authorized_keys"
chmod 600 "/home/$NAME/.ssh/authorized_keys"
echo "ключ ноутбука добавлен: пользователь $NAME, туннель будет на 127.0.0.1:$PORT"
