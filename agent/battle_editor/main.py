"""Standalone service entry point used by both CMD and the Agent launcher."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Direct execution with the embedded Python requires no installed project package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from battle_editor.server import create_server
else:
    from .server import create_server


def main(argv=None):
    parser = argparse.ArgumentParser(description="MaaFGO 本地编队与战斗编辑服务")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--owner-pid", type=int)
    parser.add_argument("--owner-created", type=float)
    parser.add_argument("--idle-timeout", type=float, default=3600)
    parser.add_argument("--require-project-binding", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    try:
        server = create_server(root, args.port, owner_pid=args.owner_pid, owner_created=args.owner_created, idle_timeout=args.idle_timeout, require_project_binding=args.require_project_binding)
        log_dir = server.storage.editor_dir
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=[logging.FileHandler(log_dir / "service.log", encoding="utf-8")], force=True)
        logging.info("编队编辑器已启动：%s", server.health())
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            logging.info("编队编辑器服务已正常停止")
        return 0
    except Exception:
        logging.exception("无法启动编队编辑器服务")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
