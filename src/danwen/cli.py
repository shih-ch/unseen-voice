"""命令列入口：danwen run | bench | download | devices | paste-test | init-config"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import shutil
import sys
from pathlib import Path

from . import __version__, config, models, paths
from .asr import BACKENDS, resolve_name

log = logging.getLogger("danwen")

# systemd 遇到這個結束碼不重啟（設定或權限問題，重啟也沒用）
EXIT_CONFIG = 78


def _setup_logging(verbose: bool, to_file: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if to_file:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                paths.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_run(args: argparse.Namespace, cfg: config.Config) -> int:
    from .daemon import Daemon
    from .hotkey import NoKeyboardError

    try:
        daemon = Daemon(cfg)
        daemon.run()
    except (NoKeyboardError, PermissionError, config.ConfigError, ValueError) as e:
        msg = str(e)
        if isinstance(e, PermissionError) and "uinput" in msg:
            msg = "沒有權限使用 /dev/uinput（虛擬鍵盤）。請執行 install.sh，並登出再登入。"
        from .feedback import Feedback

        Feedback(sounds=False, notify_errors=cfg.feedback.notify_errors).error(msg)
        return EXIT_CONFIG
    return 0


def cmd_bench(args: argparse.Namespace, cfg: config.Config) -> int:
    from . import bench

    backends = [resolve_name(b) for b in args.backend] if args.backend else list(BACKENDS)
    return bench.run(cfg, args.inputs or [Path("samples")], backends)


def cmd_download(args: argparse.Namespace, cfg: config.Config) -> int:
    targets = {"sensevoice": [models.SENSEVOICE], "whisper_server": [models.WHISPER_TURBO]}
    names = [resolve_name(b) for b in args.backend] if args.backend else ["sensevoice"]
    for name in names:
        for spec in targets[name]:
            models.ensure(spec)
            print(f"{name}：{spec.dir}")
    return 0


def cmd_devices(args: argparse.Namespace, cfg: config.Config) -> int:
    import evdev
    import sounddevice as sd

    print("麥克風（audio.device 可填編號或名稱的一部分）：")
    default_in = sd.default.device[0]
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = "＊" if i == default_in else "  "
            print(f"  {mark}{i:3d}  {d['name']}")
    print("\n鍵盤（hotkey.key 使用 evdev 按鍵名稱，例如 KEY_RIGHTCTRL）：")
    denied = False
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            denied = True
            continue
        if evdev.ecodes.KEY_A in dev.capabilities().get(evdev.ecodes.EV_KEY, []):
            print(f"  {path}  {dev.name}")
        dev.close()
    if denied:
        print("  （部分裝置沒有權限讀取：需加入 input 群組並登出再登入）")
    return 0


def cmd_paste_test(args: argparse.Namespace, cfg: config.Config) -> int:
    import time

    from .output import Paster

    paster = Paster(cfg.output.restore_clipboard, cfg.output.restore_delay_ms)
    try:
        for i in range(args.delay, 0, -1):
            print(f"{i} 秒後貼上，請點一下要測試的輸入框……", flush=True)
            time.sleep(1)
        paster.paste(args.text)
        print("已送出 Ctrl+V")
        time.sleep(cfg.output.restore_delay_ms / 1000 + 0.3)  # 等剪貼簿還原完成
    finally:
        paster.close()
    return 0


def cmd_init_config(args: argparse.Namespace, cfg: config.Config) -> int:
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("config.yaml", "replacements.yaml"):
        dest = paths.CONFIG_DIR / name
        if dest.exists():
            print(f"已存在，保留：{dest}")
        else:
            shutil.copyfile(paths.DATA_DIR / name, dest)
            print(f"已建立：{dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="danwen", description="但聞人語：GNOME Wayland 地端語音聽寫")
    parser.add_argument("--version", action="version", version=f"danwen {__version__}")
    parser.add_argument("-c", "--config", type=Path, help=f"設定檔（預設 {paths.CONFIG_FILE}）")
    parser.add_argument("-v", "--verbose", action="store_true", help="顯示除錯訊息")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="常駐執行（systemd 服務使用）")
    p = sub.add_parser("bench", help="對 wav 檔跑各 backend，比較耗時與文字")
    p.add_argument("inputs", nargs="*", type=Path, help="wav 檔或資料夾（預設 samples/）")
    p.add_argument("-b", "--backend", action="append", help="只跑指定 backend（sensevoice/A、whisper_server/B），可重複")
    p = sub.add_parser("download", help="預先下載模型")
    p.add_argument("-b", "--backend", action="append", help="sensevoice/A（預設）、whisper_server/B")
    sub.add_parser("devices", help="列出麥克風與鍵盤")
    p = sub.add_parser("paste-test", help="倒數後把一段文字貼到目前的輸入框，測試貼上流程")
    p.add_argument("text", nargs="?", default="但聞人語測試：把這個PR merge到main，台灣繁體中文。")
    p.add_argument("--delay", type=int, default=3, help="倒數秒數（預設 3）")
    sub.add_parser("init-config", help="建立預設設定檔與替換字典（不覆蓋既有檔案）")

    args = parser.parse_args(argv)
    command = args.command or "run"
    _setup_logging(args.verbose, to_file=command == "run")
    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        log.error("%s", e)
        return EXIT_CONFIG
    handler = {
        "run": cmd_run,
        "bench": cmd_bench,
        "download": cmd_download,
        "devices": cmd_devices,
        "paste-test": cmd_paste_test,
        "init-config": cmd_init_config,
    }[command]
    try:
        return handler(args, cfg)
    except config.ConfigError as e:
        log.error("%s", e)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
