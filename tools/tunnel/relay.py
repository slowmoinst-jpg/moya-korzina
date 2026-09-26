"""Мост к домашнему выходу: из сети контейнеров — к туннелю на хосте.

Туннель с ноутбука владельца (tools/tunnel/laptop-setup.sh) слушает только
127.0.0.1:1080 на хосте, а приложение живёт в контейнерах с собственной сетью.
Мост слушает адрес хоста в сети docker (172.17.0.1) и перекладывает байты туда и
обратно, ничего в них не читая. Снаружи сервера его не видно: 172.17.0.1 из
интернета не маршрутизируется.

    python tools/tunnel/relay.py --listen 172.17.0.1:1080 --to 127.0.0.1:1080

На сервере живёт контейнером korzina-tunnel-relay с --network host
(tools/server-deploy.sh). Туннеля нет — соединение просто закрывается, и
app/homeexit.alive это видит.
"""
from __future__ import annotations

import argparse
import asyncio


def _pair(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host, int(port)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001 — закрываем то, что уже могло закрыться
            pass


async def serve(listen: tuple[str, int], target: tuple[str, int]) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(*target)
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

    server = await asyncio.start_server(handle, *listen)
    print(f"мост {listen[0]}:{listen[1]} → {target[0]}:{target[1]}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Мост из сети контейнеров к туннелю на хосте")
    parser.add_argument("--listen", default="172.17.0.1:1080")
    parser.add_argument("--to", default="127.0.0.1:1080")
    args = parser.parse_args()
    asyncio.run(serve(_pair(args.listen), _pair(args.to)))


if __name__ == "__main__":
    main()
