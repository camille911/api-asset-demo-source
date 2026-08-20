"""Command-line entry point: serve / doctor / version."""
from __future__ import annotations

import argparse
import sys

from git_asset_mcp import __version__


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="git-asset-mcp")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Start the MCP server")
    serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    serve.add_argument("--config", default=None, help="Path to config YAML")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    doctor = sub.add_parser("doctor", help="Check environment and configuration")
    doctor.add_argument("--config", default=None, help="Path to config YAML")

    sub.add_parser("version", help="Print version")

    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"git-asset-mcp {__version__}")
        return 0
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "serve":
        return _serve(args)

    parser.print_help()
    return 0


def _serve(args) -> int:
    from git_asset_mcp.server import mcp

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


def _doctor(args) -> int:
    import platform

    print(f"git-asset-mcp {__version__}")
    print(f"python: {platform.python_version()}")
    print(f"platform: {platform.system()} {platform.machine()}")

    try:
        import mcp  # noqa: F401

        print("mcp: OK")
    except ImportError:
        print("mcp: MISSING")
        return 1

    try:
        from git_asset_mcp.settings import Settings

        Settings.from_config(args.config)
        print("config: OK")
    except Exception as exc:  # pragma: no cover
        print(f"config: ERROR ({exc})")
        return 1

    print("doctor: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
