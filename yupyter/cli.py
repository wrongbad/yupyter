"""`yupyter [PATH]` command line entry point."""
from __future__ import annotations

import argparse
import secrets
import socket
import threading
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8890


def find_free_port(ip: str, start: int = DEFAULT_PORT) -> int:
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((ip, port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port found")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yupyter", description="A zen-mode notebook.")
    p.add_argument("path", nargs="?", default="untitled.md", help="notebook (.md); created if missing")
    p.add_argument("--port", type=int, default=None, help="default: first free from 8890")
    p.add_argument("--ip", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--token", default=None, help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from .server.app import create_app

    args = build_parser().parse_args(argv)
    port = args.port or find_free_port(args.ip)
    token = args.token or secrets.token_urlsafe(24)
    path = Path(args.path).expanduser()
    app = create_app(path, token, port=port)
    host = "localhost" if args.ip in ("127.0.0.1", "0.0.0.0") else args.ip
    url = f"http://{host}:{port}/?token={token}"
    print(f"\n  yupyter: {path}\n  Open: {url}\n", flush=True)
    config = uvicorn.Config(app, host=args.ip, port=port, log_level="warning")
    server = uvicorn.Server(config)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.run()  # SIGINT -> graceful shutdown -> lifespan saves + stops kernels
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
