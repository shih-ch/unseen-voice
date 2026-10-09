#!/usr/bin/env bash
# 開發用：在視窗裡跑一個巢狀的 GNOME Shell 測試 extension。
#
# 與目前的桌面完全隔開：獨立的設定資料夾（dconf）、extension 資料夾與 session D-Bus，
# 裡面跑假的 danwen（dev/fake_danwen.py）。不會改到真正桌面的設定，也不會碰到真正的 danwen 或 fcitx5。
#
# 用法：dev/nested-shell.sh                    關掉視窗即結束，暫存資料自動刪除
#      SIZE=1600x1000 dev/nested-shell.sh
#      DANWEN_FAKE_CYCLE=1 dev/nested-shell.sh   假 danwen 自動輪流切換狀態（看圖示變色）
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="danwen@danwen.github.io"
PYTHON="$REPO/.venv/bin/python"
[ -x "$PYTHON" ] || { echo "請先在專案目錄執行 uv sync" >&2; exit 1; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/danwen-nested.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/config" "$TMP/data/gnome-shell/extensions" "$TMP/state"
# 用連結指向專案裡的 extension：改完程式重開巢狀 Shell 就能看到
ln -s "$REPO/gnome-extension/$UUID" "$TMP/data/gnome-shell/extensions/$UUID"

export XDG_CONFIG_HOME="$TMP/config" XDG_DATA_HOME="$TMP/data" XDG_STATE_HOME="$TMP/state"
# Ubuntu 會設定 GNOME_SHELL_SESSION_MODE=ubuntu，巢狀 Shell 繼承後會載入 Ubuntu 預設的 extension
# （其中桌面圖示 DING 在巢狀環境裡會不斷崩潰重啟、搶走焦點），所以改用原生 GNOME 模式
unset GNOME_SHELL_SESSION_MODE
export XDG_CURRENT_DESKTOP=GNOME
export MUTTER_DEBUG_DUMMY_MODE_SPECS="${SIZE:-1280x800}"
export DANWEN_REPO="$REPO" DANWEN_PYTHON="$PYTHON" DANWEN_UUID="$UUID"

# 內層指令刻意用單引號，變數經由上面 export 的環境變數傳入
# shellcheck disable=SC2016
dbus-run-session -- bash -c '
    gsettings set org.gnome.shell disable-user-extensions false
    gsettings set org.gnome.shell enabled-extensions "[\"$DANWEN_UUID\"]"
    "$DANWEN_PYTHON" "$DANWEN_REPO/dev/fake_danwen.py" &
    FAKE=$!
    gnome-shell --nested --wayland --mode=user
    kill $FAKE 2>/dev/null || true
'
