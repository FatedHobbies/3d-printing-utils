"""Command line entry point for the Slicer machine.

    python -m threedpu.slicer serve        run the HTTP service (default)
    python -m threedpu.slicer tray         tray icon: starts the service, Start/Stop from its menu
    python -m threedpu.slicer info         window with this PC's address, token and service status
    python -m threedpu.slicer stop         stop the service running on this PC
    python -m threedpu.slicer show-token   print the access token to paste into Manager
    python -m threedpu.slicer find-bambu   show which Bambu Studio executable will be used
    python -m threedpu.slicer config       print the config file path and contents
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


def _setup_logging(log_file: Path | None) -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if log_file is None and sys.stderr is None:
        # Started with pythonw.exe (no console): there is nowhere to print, so log to a file.
        log_file = slicer_config_path().parent / "slicer.log"
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        stream = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        # Anything printed (tracebacks, warnings) goes to the log as well.
        sys.stdout = sys.stderr = stream
        logging.basicConfig(level=logging.INFO, format=fmt, stream=stream, force=True)
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from threedpu.slicer.jobqueue import JobQueue
    from threedpu.slicer.runner import SliceRunner
    from threedpu.slicer.service import create_app

    _setup_logging(args.log_file)
    cfg = load_slicer_config(args.config)
    try:
        exe, version = _bambu(cfg)
    except bambu.BambuStudioNotFound as exc:
        logging.error("%s", exc)
        return 2
    logging.info("Using Bambu Studio %s at %s", version or "(unknown version)", exe)
    logging.info("Config: %s", args.config or slicer_config_path())

    jobs = JobQueue(
        Path(cfg.work_dir),
        lambda: SliceRunner(exe, cfg.slice_timeout_seconds, cfg.extra_cli_args),
        slicer_bambu_version=version,
        keep_work_days=cfg.keep_work_days,
    )
    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True

    app = create_app(jobs, cfg.token, exe, version, request_shutdown)
    # log_config=None: use the logging set up above (works without a console too).
    server = uvicorn.Server(uvicorn.Config(app, host=args.host or cfg.host,
                                           port=args.port or cfg.port,
                                           log_config=None, log_level="info"))
    server.run()
    return 0 if server.started else 1


def cmd_tray(args: argparse.Namespace) -> int:
    from threedpu.slicer.tray import main as tray_main

    return tray_main(args.config, show_info=args.show_info)


def cmd_info(args: argparse.Namespace) -> int:
    from threedpu.slicer.infogui import main as info_main

    return info_main(args.config)


def cmd_stop(args: argparse.Namespace) -> int:
    """Ask a running service on this PC to stop (used by the installer before upgrading)."""
    from threedpu.manager.client import SlicerClient, SlicerError

    cfg = load_slicer_config(args.config)
    tray_stopped = False
    if args.tray:
        try:
            from PySide6.QtCore import QCoreApplication

            from threedpu.slicer.tray import notify_running_instance

            _app = QCoreApplication.instance() or QCoreApplication([])
            tray_stopped = notify_running_instance("quit")  # the tray stops the server too
        except ImportError:
            pass
    try:
        with SlicerClient(f"http://127.0.0.1:{cfg.port}", cfg.token, timeout=3) as c:
            c.shutdown()
    except SlicerError as exc:
        if not tray_stopped:
            print(f"Service not stopped: {exc}", file=sys.stderr)
            return 1
    print("Service stopping.")
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
    parser = argparse.ArgumentParser(prog="python -m threedpu.slicer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=None,
                        help=f"config file (default: {slicer_config_path()})")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--log-file", type=Path, help="write the log here instead of the console")
    sub.add_parser("info", help="show address, token and status in a window")
    stop = sub.add_parser("stop", help="stop the service running on this PC")
    stop.add_argument("--tray", action="store_true", help="also close the tray icon")
    tray = sub.add_parser("tray", help="tray icon that starts/stops the service (starts it now)")
    tray.add_argument("--show-info", action="store_true",
                      help="also open the window with the address and token")
    sub.add_parser("show-token", help="print the access token")
    sub.add_parser("find-bambu", help="show the Bambu Studio executable in use")
    sub.add_parser("config", help="print config path and contents")
    args = parser.parse_args(argv)

    handlers = {
        None: cmd_serve,
        "serve": cmd_serve,
        "tray": cmd_tray,
        "info": cmd_info,
        "stop": cmd_stop,
        "show-token": cmd_show_token,
        "find-bambu": cmd_find_bambu,
        "config": cmd_config,
    }
    if args.cmd is None:
        args.host = args.port = args.log_file = None
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
