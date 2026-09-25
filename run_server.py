"""启动服务端：在同一个端口上同时监听 IPv4 与 IPv6。

为什么不用 `uvicorn --host ::`：Windows 上 Python 创建 AF_INET6 套接字时
IPV6_V6ONLY 默认为 1，uvicorn 绑 `::` 就只收 IPv6 连接，IPv4（含 127.0.0.1）
会被直接拒掉。这里手动建套接字并显式关掉该选项，让一个进程同时服务两种协议。
IPv4 连接会以 ::ffff:127.0.0.1 这样的映射地址进来，对业务无影响。

用法：  .venv\\Scripts\\python.exe run_server.py
"""
import socket

import uvicorn

from server.main import app

HOST = "::"
PORT = 8000


def create_dual_stack_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # 关键：0 表示同一个套接字也接受 IPv4 连接
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind((HOST, port))
    sock.listen(128)
    return sock


def main() -> None:
    sock = create_dual_stack_socket(PORT)
    uvicorn.Server(uvicorn.Config(app)).run(sockets=[sock])


if __name__ == "__main__":
    main()