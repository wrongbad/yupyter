"""`yupyter [PATH]` command line entry point."""
from __future__ import annotations

import argparse
import secrets
import socket
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote

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
    p.add_argument(
        "path",
        nargs="?",
        default=None,
        help="a notebook (.md; created if missing) to open, or a directory to serve (default: the current directory)",
    )
    p.add_argument("--port", type=int, default=None, help="default: first free from 8890")
    p.add_argument("--ip", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--token", default=None, help=argparse.SUPPRESS)
    return p


def resolve_target(arg: str | None, cwd: Path | None = None) -> tuple[Path, str | None]:
    """``(root, initial)`` for the CLI argument.

    No argument or a directory: that directory is the root, nothing is opened.
    A file: the root is the current directory and the file is opened; if the file
    lies outside it, its own directory is the root instead.
    """
    cwd = (cwd or Path.cwd()).resolve()
    if arg is None:
        return cwd, None
    p = Path(arg).expanduser()
    p = (cwd / p).resolve()
    if p.is_dir():
        return p, None
    if p.suffix == "":
        p = p.with_name(p.name + ".md")
    if not p.parent.is_dir():
        raise SystemExit(f"yupyter: no such directory: {p.parent}")
    root = cwd if p.is_relative_to(cwd) else p.parent
    return root, p.relative_to(root).as_posix()


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from .server.app import create_app

    args = build_parser().parse_args(argv)
    port = args.port or find_free_port(args.ip)
    token = args.token or secrets.token_urlsafe(24)
    root, initial = resolve_target(args.path)
    app = create_app(root, token, initial, port=port)
    host = "localhost" if args.ip in ("127.0.0.1", "0.0.0.0") else args.ip
    url = f"http://{host}:{port}/" + (f"n/{quote(initial)}" if initial else "") + f"?token={token}"
    print(f"\n  yupyter: {root / initial if initial else root}\n  Open: {url}\n", flush=True)
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
