"""User-facing local launcher and interactive, owner-local credential setup."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

from . import __version__
from .client import HubClient, PortalError
from .config import ConfigError, load_config, save_config
from .server import PortalServer


def _port(raw: str) -> int:
    try:
        value = int(raw)
        if 0 <= value <= 65535:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("port must be between 0 and 65535")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local GameDevUE build downloader")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    configure = commands.add_parser("configure", help="store DevHub origin and Portal key in your user profile")
    configure.add_argument("--hub-url", help="HTTPS origin or numeric loopback HTTP origin")
    serve = commands.add_parser("serve", help="serve the local browser page (loopback only)")
    serve.add_argument("--port", type=_port, default=0, help="loopback port (default: random free port)")
    serve.add_argument("--downloads", type=Path, default=Path.home() / "Downloads" / "DevPortal", help="local download directory")
    args = parser.parse_args(argv)
    if args.command is None:
        from .desktop import run_desktop
        return run_desktop()
    try:
        if args.command == "configure":
            hub = args.hub_url or input("DevHub origin (HTTPS or numeric loopback HTTP): ").strip()
            key = getpass.getpass("Per-device Portal key (input hidden): ")
            save_config(hub, key)
            print("Private configuration saved in the user profile.")
            return 0
        hub, key = load_config()
        server = PortalServer(HubClient(hub, key), args.downloads, port=args.port)
        try:
            print(f"Open {server.origin} on this machine; downloads: {args.downloads.expanduser()}", flush=True)
            server.serve_forever(poll_interval=0.3)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (ConfigError, PortalError) as exc:
        print(f"DevPortal: {exc}", file=sys.stderr)
        return 2
    except (OSError, EOFError):
        # Filesystem errors can contain a user path; never report credential-bearing network exceptions.
        print("DevPortal: local configuration or listener unavailable", file=sys.stderr)
        return 2
