"""命令列入口：danwen run | bench | download | devices | paste-test | mode | refine | dict | history | cloud | key | init-config"""

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
    from .dbus_service import AlreadyRunning
    from .hotkey import NoKeyboardError

    try:
        daemon = Daemon(cfg)
        daemon.run()
    except (NoKeyboardError, AlreadyRunning, PermissionError, config.ConfigError, ValueError) as e:
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
    targets = {"sensevoice": [models.SENSEVOICE, models.SILERO_VAD], "whisper_server": [models.WHISPER_TURBO]}
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


def cmd_mode(args: argparse.Namespace, cfg: config.Config) -> int:
    from . import refine

    if args.name:
        if args.name in ("next", "prev"):
            name = refine.cycle_mode(1 if args.name == "next" else -1, cfg.refine.mode)
        else:
            refine.set_mode(args.name)
            name = args.name
        args.name = name
        print(f"整理模式改用小紙條：{name}")
        if not sys.stdout.isatty():
            # 從 GNOME 自訂快捷鍵執行時看不到終端機，改用桌面通知告知
            from .feedback import Feedback

            Feedback(sounds=False).notice(f"整理模式改用小紙條：{args.name}")
        return 0
    current = refine.current_mode(cfg.refine.mode)
    print("小紙條（＊為目前使用中；切換：danwen mode 名稱）：")
    for name, path in refine.available_prompts().items():
        try:
            description = refine.load_prompt(name).description
        except config.ConfigError as e:
            description = f"（格式錯誤：{e}）"
        mark = "＊" if name == current else "  "
        print(f"  {mark}{name:<6} {description}  [{path}]")
    return 0


def _postprocessor(cfg: config.Config):
    from .postprocess import PostProcessor

    replacements = cfg.postprocess.replacements
    return PostProcessor(cfg.postprocess.opencc, Path(replacements).expanduser() if replacements else None)


def cmd_refine(args: argparse.Namespace, cfg: config.Config) -> int:
    import time

    from .refine import RefineError, Refiner, current_mode

    from . import cloud

    post = _postprocessor(cfg)
    try:
        if args.local:
            refiner, where = Refiner(cfg.refine), "本機"
        elif args.cloud:
            active = cloud.resolve_plan(cfg.cloud)
            refiner, where = cloud.make_refiner(cfg, active.cloud), f"雲端 {cloud.label(active.cloud)}"
        else:
            refiner, where = cloud.choose_refiner(cfg)
    except (cloud.CloudError, config.ConfigError) as e:
        print(f"無法整理：{e}", file=sys.stderr)
        return 1
    mode = args.mode or current_mode(cfg.refine.mode)
    text = post(args.text)
    t = time.monotonic()
    try:
        context = {"clipboard": args.context} if args.context else None
        result = post(refiner.refine(text, mode, post.replacements.terms(), context))
    except RefineError as e:
        print(f"整理失敗（實際使用時會改貼原文）：{e}", file=sys.stderr)
        return 1
    print(f"[{mode}，{where}，{time.monotonic() - t:.2f} 秒]\n{result}")
    return 0


def cmd_dict(args: argparse.Namespace, cfg: config.Config) -> int:
    from .postprocess import Replacements, edit_replacements

    path = Path(cfg.postprocess.replacements).expanduser() if cfg.postprocess.replacements else paths.REPLACEMENTS_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths.DATA_DIR / "replacements.yaml", path)
    words = args.words
    if args.action == "add":
        if len(words) not in (1, 2):
            print("用法：danwen dict add 正確寫法　或　danwen dict add 常錯的寫法 正確寫法", file=sys.stderr)
            return 2
        wrong, right = (words[0], words[0]) if len(words) == 1 else words
        existed = edit_replacements(path, wrong, right)
        print(f"{'已更新' if existed else '已新增'}：{wrong} → {right}" if wrong != right else f"已新增專有名詞：{right}")
        return 0
    if args.action == "remove":
        if len(words) != 1:
            print("用法：danwen dict remove 詞條", file=sys.stderr)
            return 2
        if not edit_replacements(path, words[0], None):
            print(f"字典裡沒有「{words[0]}」", file=sys.stderr)
            return 1
        print(f"已刪除：{words[0]}")
        return 0
    table = Replacements(path)
    terms = set(table.terms())
    print(f"替換字典 {path}（＊＝整理模式也會參考的專有名詞）：")
    for wrong, right in table.entries().items():
        mark = "＊" if right in terms else "  "
        print(f"  {mark}{right}" if wrong == right else f"  {mark}{wrong} → {right}")
    return 0


def _pad(text: str, width: int) -> str:
    """依顯示寬度補空白（中文字佔兩格），讓表格對齊。"""
    import unicodedata

    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(width - shown, 0)


def cmd_history(args: argparse.Namespace, cfg: config.Config) -> int:
    from .history import History
    from .output import Clipboard
    from .refine import RefineError, Refiner, current_mode

    history = History(size=cfg.history.size, keep_audio=cfg.history.keep_audio)
    try:
        if args.action == "list":
            entries = history.entries()
            if not entries:
                print("還沒有任何歷史紀錄" + ("（history.size 為 0，未啟用）" if cfg.history.size <= 0 else ""))
                return 0
            for e in entries:
                when = e.time[5:16].replace("T", " ")
                kind = f"整理:{e.mode}" if e.mode else "快速"
                preview = e.text.replace("\n", " ⏎ ")
                preview = preview[:40] + "…" if len(preview) > 40 else preview
                print(f"#{e.id:<4} {when}  {_pad(kind, 14)} {e.duration_s:5.1f}s  {preview}")
            print("\n查看：danwen history show 編號｜複製：danwen history copy 編號｜"
                  "換小紙條重新整理：danwen history redo 編號 -m 小紙條")
            return 0
        if args.action == "clear":
            print(f"已清除 {history.clear()} 筆")
            return 0
        entry = history.get(args.id)
        if args.action == "show":
            print(f"編號：#{entry.id}" + (f"（由 #{entry.redo_of} 重新整理）" if entry.redo_of else ""))
            print(f"時間：{entry.time}　長度：{entry.duration_s}s　辨識：{entry.backend}")
            print(f"模式：{'整理（' + entry.mode + '）' if entry.mode else '快速'}")
            print(f"錄音：{history.audio_path(entry) or '未保存（history.keep_audio 預設關閉）'}")
            print(f"\n── 辨識結果（整理前）──\n{entry.raw}\n\n── 貼上的文字 ──\n{entry.text}")
        elif args.action == "copy":
            Clipboard.set_text(entry.text)
            print(f"已複製第 {entry.id} 筆到剪貼簿")
        elif args.action == "play":
            audio = history.audio_path(entry)
            player = next((p for p in ("pw-play", "paplay", "aplay") if shutil.which(p)), None)
            if audio is None or not audio.exists():
                print("這筆沒有保存錄音（要保存請在設定開啟 history.keep_audio）", file=sys.stderr)
                return 1
            if player is None:
                print("找不到播放程式（pw-play、paplay 或 aplay）", file=sys.stderr)
                return 1
            import subprocess

            subprocess.run([player, str(audio)], check=False)
        elif args.action == "redo":
            from .history import redo_entry

            from . import cloud

            post = _postprocessor(cfg)
            try:
                refiner, _ = cloud.choose_refiner(cfg)  # 依方案
            except cloud.CloudError as e:
                print(f"無法整理：{e}", file=sys.stderr)
                return 1
            mode = args.mode or current_mode(cfg.refine.mode)
            result, new = redo_entry(
                history, entry.id, mode, lambda raw, m: post(refiner.refine(raw, m, post.replacements.terms()))
            )
            Clipboard.set_text(result)
            saved = f"，存成第 {new.id} 筆" if new else ""
            print(f"[{mode}] 已複製到剪貼簿{saved}：\n{result}")
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 1
    except RefineError as e:
        print(f"整理失敗：{e}", file=sys.stderr)
        return 1
    return 0


def cmd_key(args: argparse.Namespace, cfg: config.Config) -> int:
    import getpass

    from . import cloud
    from .credentials import CredentialError, KeyringStore

    store = KeyringStore()
    provider = args.provider or cfg.cloud.provider
    if provider not in cloud.PRESETS:
        print(f"服務商只能是 {'、'.join(cloud.PRESETS)}", file=sys.stderr)
        return 2
    name = cloud.PRESETS[provider].label
    try:
        if args.action == "set":
            key = getpass.getpass(f"請輸入 {name} 的 API Key（輸入時不會顯示）：").strip()
            if not key:
                print("沒有輸入，未變更")
                return 1
            store.set(provider, key)
            print(f"已把 {name} 的 API Key 存進 GNOME 鑰匙圈")
        elif args.action == "delete":
            deleted = store.delete(None if args.all else provider)
            print(f"已從 GNOME 鑰匙圈刪除：{'、'.join(deleted)}" if deleted else "沒有可刪除的金鑰")
        else:
            have = store.providers()
            print("GNOME 鑰匙圈裡的 API Key（只顯示有沒有設定，不顯示內容）：")
            for key_name, preset in cloud.PRESETS.items():
                print(f"  {'✓ 已設定' if key_name in have else '  未設定'}  {preset.label}（{key_name}）")
    except CredentialError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_cloud(args: argparse.Namespace, cfg: config.Config) -> int:
    import time

    import numpy as np

    from . import cloud
    from .asr.cloud import CloudASRBackend
    from .refine import RefineError

    action = args.action
    try:
        if action == "test":
            active = cloud.resolve_plan(cfg.cloud)
            if not active.uses_cloud:
                print(f"目前方案是 {active.title}，沒有用到雲端，不需要測試")
                return 0
            endpoint = cloud.check_ready(active.cloud)
            print(f"測試方案 {active.title}（{endpoint.label}，會實際呼叫，產生極少量費用）")
            if active.asr:
                if args.wav:
                    from .audio import read_wav

                    audio, sr = read_wav(args.wav)
                else:
                    audio, sr = np.zeros(16000, dtype=np.float32), 16000  # 1 秒靜音：只測連線與金鑰
                t = time.monotonic()
                text = CloudASRBackend(active.cloud).transcribe(audio, sr)
                print(f"  語音辨識 {endpoint.asr_model}：{time.monotonic() - t:.2f} 秒 → {text or '（空白）'}")
            if active.refine:
                t = time.monotonic()
                result = cloud.make_refiner(cfg, active.cloud).refine("嗯，那個今天天氣很好，然後我們去散步。", "日常")
                print(f"  整理模式 {endpoint.llm_model}：{time.monotonic() - t:.2f} 秒 → {result}")
            return 0
        if action and action != "status":
            name = {"off": "local", "on": cfg.cloud.plan if cfg.cloud.plan != "local" else cloud.DEFAULT_PLAN}.get(
                action, action
            )
            active = cloud.resolve_plan(cfg.cloud, name)
            cloud.check_plan(active)
            cloud.set_plan(active.plan.name)
            sent = "、".join(p for p, on in (("錄音", active.asr), ("要整理的文字", active.refine)) if on)
            print(f"已切換到方案 {active.title}" + (f"：{sent}會送到雲端" if sent else "：全部在本機"))
            return 0
    except (cloud.CloudError, RefineError, config.ConfigError) as e:
        print(f"失敗：{e}", file=sys.stderr)
        return 1

    current = cloud.resolve_plan(cfg.cloud)
    default_mark = "（設定檔預設）" if current.plan.name == cfg.cloud.plan else ""
    print(f"目前方案：{current.title}{default_mark}")
    for part, on in (("語音辨識", current.asr), ("整理模式", current.refine)):
        if not on:
            print(f"  {part}：本機")
            continue
        try:
            endpoint = cloud.resolve(current.cloud)
            model = endpoint.asr_model if part == "語音辨識" else endpoint.llm_model
            print(f"  {part}：☁ {endpoint.label} {model}")
        except config.ConfigError as e:
            print(f"  {part}：☁ 設定不完整（{e}）")
    print("\n可用方案（danwen cloud <名稱或代號> 切換）：")
    for plan in cloud.PLANS.values():
        active = cloud.resolve_plan(cfg.cloud, plan.name)
        if active.uses_cloud:
            try:
                cloud.check_ready(active.cloud)
                ready = "✓ 可用"
            except (cloud.CloudError, config.ConfigError) as e:
                ready = f"✗ {e}"
        else:
            ready = "✓ 可用"
        mark = "＊" if plan.name == current.plan.name else "  "
        print(f"  {mark}{plan.letter} {plan.name:<10} {_pad(plan.label, 20)} {ready}")
    print("\n實測目前方案：danwen cloud test [錄音.wav]　金鑰：danwen key set <服務商>")
    return 0


def cmd_shortcuts(args: argparse.Namespace, cfg: config.Config) -> int:
    from .shortcuts import SHORTCUTS, ShortcutError, Shortcuts, danwen_command

    shortcuts = Shortcuts()
    try:
        if args.action == "install":
            added, skipped = shortcuts.install(danwen_command())
            for sc in added:
                print(f"  ✓ {sc.binding:<16} {sc.name}")
            for sc, owner in skipped:
                print(f"  ✗ {sc.binding:<16} {sc.name}（略過：已被「{owner}」使用）")
            print("已設定 GNOME 快捷鍵；移除：danwen shortcuts remove")
        elif args.action == "remove":
            removed = shortcuts.remove()
            print(f"已移除：{'、'.join(removed)}" if removed else "沒有 danwen 的快捷鍵")
        else:
            rows = shortcuts.status()
            if not rows:
                print("還沒有設定（danwen shortcuts install 會新增以下快捷鍵）：")
                for sc in SHORTCUTS:
                    print(f"  {sc.binding:<16} {sc.name}")
            for name, binding, command in rows:
                print(f"  {binding:<16} {name}　［{command}］")
    except ShortcutError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_init_config(args: argparse.Namespace, cfg: config.Config) -> int:
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    targets = [(paths.DATA_DIR / n, paths.CONFIG_DIR / n) for n in ("config.yaml", "replacements.yaml")]
    targets += [(f, paths.CONFIG_DIR / "prompts" / f.name) for f in sorted((paths.DATA_DIR / "prompts").glob("*.yaml"))]
    for src, dest in targets:
        if dest.exists():
            print(f"已存在，保留：{dest}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
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
    p = sub.add_parser("mode", help="列出或切換整理模式的小紙條")
    p.add_argument("name", nargs="?", help="要切換到的小紙條名稱；next／prev＝下一張／上一張")
    p = sub.add_parser("refine", help="用整理模式整理一段文字（測試小紙條用）")
    p.add_argument("text", help="要整理的文字")
    p.add_argument("-m", "--mode", help="使用的小紙條（預設為目前模式）")
    p.add_argument("--context", help="模擬剪貼簿內容，測試上下文的效果")
    p.add_argument("--cloud", action="store_true", help="強制用目前方案的雲端 LLM（預設依方案）")
    p.add_argument("--local", action="store_true", help="強制用本機 Ollama（預設依方案）")
    p = sub.add_parser("dict", help="列出、新增、刪除替換字典的詞條")
    p.add_argument("action", nargs="?", choices=("list", "add", "remove"), default="list")
    p.add_argument("words", nargs="*", help="add：[常錯的寫法] 正確寫法；remove：詞條")
    p = sub.add_parser("history", help="歷史紀錄：列出、查看、複製、重聽、換小紙條重新整理")
    p.add_argument("action", nargs="?", choices=("list", "show", "copy", "redo", "play", "clear"), default="list")
    p.add_argument("id", nargs="?", type=int, help="編號（預設為最新一筆）")
    p.add_argument("-m", "--mode", help="redo 使用的小紙條（預設為目前模式）")
    p = sub.add_parser("cloud", help="方案（本機／雲端）：查看、切換、實測連線")
    p.add_argument("action", nargs="?", default="status",
                   help="status、test、on（預設方案）、off（全部本機），或方案名稱／代號：local/A、hybrid/B、groq/C、cloudflare/D、custom/E")
    p.add_argument("wav", nargs="?", type=Path, help="test 時用來測語音辨識的錄音（預設為 1 秒靜音）")
    p = sub.add_parser("key", help="API Key：存進／查看／刪除（GNOME 鑰匙圈）")
    p.add_argument("action", nargs="?", choices=("status", "set", "delete"), default="status")
    p.add_argument("provider", nargs="?", help="groq、openai、cloudflare、custom（預設為設定檔的 cloud.provider）")
    p.add_argument("--all", action="store_true", help="delete 時刪除 danwen 的全部金鑰")
    p = sub.add_parser("shortcuts", help="GNOME 快捷鍵：切換小紙條（Super+Alt+M、Super+Alt+1～5）")
    p.add_argument("action", nargs="?", choices=("status", "install", "remove"), default="status")
    sub.add_parser("init-config", help="建立預設設定檔、替換字典與小紙條（不覆蓋既有檔案）")

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
        "mode": cmd_mode,
        "refine": cmd_refine,
        "dict": cmd_dict,
        "history": cmd_history,
        "cloud": cmd_cloud,
        "key": cmd_key,
        "shortcuts": cmd_shortcuts,
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
