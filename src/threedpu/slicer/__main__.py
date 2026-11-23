"""Command line entry point for the Slicer machine.

    3dpu-slicer serve        run the HTTP service (default)
    3dpu-slicer show-token   print the access token to paste into Manager
    3dpu-slicer find-bambu   show which Bambu Studio executable will be used
    3dpu-slicer config       print the config file path and contents
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from threedpu.common import bambu
from threedpu.common.config import load_slicer_config, slicer_config_path


def _bambu(cfg) -> tuple[Path, str | None]:
    exe = bambu.find_bambu_studio(cfg.bambu_studio_path or None)
    return exe, bambu.exe_version(exe)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from threedpu.slicer.jobqueue import JobQueue
    from threedpu.slicer.runner import SliceRunner
    from threedpu.slicer.service import create_app

    cfg = load_slicer_config(args.config)
    try:
        exe, version = _bambu(cfg)
    except bambu.BambuStudioNotFound as exc:
        print(exc, file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Using Bambu Studio %s at %s", version or "(unknown version)", exe)
    logging.info("Config: %s", args.config or slicer_config_path())

    jobs = JobQueue(
        Path(cfg.work_dir),
        lambda: SliceRunner(exe, cfg.slice_timeout_seconds, cfg.extra_cli_args),
        slicer_bambu_version=version,
        keep_work_days=cfg.keep_work_days,
    )
    app = create_app(jobs, cfg.token, exe, version)
    uvicorn.run(app, host=args.host or cfg.host, port=args.port or cfg.port, log_level="info")
    return 0


def cmd_show_token(args: argparse.Namespace) -> int:
    print(load_slicer_config(args.config).token)
    return 0


def cmd_find_bambu(args: argparse.Namespace) -> int:
    cfg = load_slicer_config(args.config)
    try:
        exe, version = _bambu(cfg)
    except bambu.BambuStudioNotFound as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"{exe}  (version {version or 'unknown'})")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    path = args.config or slicer_config_path()
    cfg = load_slicer_config(path)
    print(path)
    print(json.dumps(asdict(cfg), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="3dpu-slicer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, help="config file (default: %(default)s)",
                        default=None)
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    sub.add_parser("show-token", help="print the access token")
    sub.add_parser("find-bambu", help="show the Bambu Studio executable in use")
    sub.add_parser("config", help="print config path and contents")
    args = parser.parse_args(argv)

    handlers = {
        None: cmd_serve,
        "serve": cmd_serve,
        "show-token": cmd_show_token,
        "find-bambu": cmd_find_bambu,
        "config": cmd_config,
    }
    if args.cmd is None:
        args.host = args.port = None
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
