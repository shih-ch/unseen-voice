#!/usr/bin/env bash
# 開發用：在看不見的（headless）GNOME Shell 裡自動檢查 extension 的選單，不會在桌面跳出視窗。
#
# 跟 dev/nested-shell.sh 一樣完全隔開（獨立的 dconf、extension 資料夾與 session D-Bus，裡面跑假的 danwen），
# 另外載入 dev/probe@danwen.test：打開選單、印出項目、操作「翻譯成」與滾輪切換，印完就結束。
#
# 用法：dev/headless-check.sh                     印出選單內容與操作結果
#      SHOTS=docs/screenshots dev/headless-check.sh  改為截圖（README 用的圖）
#      SIZE=1600x1000 SCALE=1 dev/headless-check.sh  螢幕大小與縮放（截圖預設 2560x1600、2 倍，介面用繁體中文）
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="danwen@danwen.github.io"
PROBE="probe@danwen.test"
PYTHON="$REPO/.venv/bin/python"
[ -x "$PYTHON" ] || { echo "請先在專案目錄執行 uv sync" >&2; exit 1; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/danwen-headless.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/config" "$TMP/data/gnome-shell/extensions" "$TMP/state"
ln -s "$REPO/gnome-extension/$UUID" "$TMP/data/gnome-shell/extensions/$UUID"
ln -s "$REPO/dev/$PROBE" "$TMP/data/gnome-shell/extensions/$PROBE"

export XDG_CONFIG_HOME="$TMP/config" XDG_DATA_HOME="$TMP/data" XDG_STATE_HOME="$TMP/state"
unset GNOME_SHELL_SESSION_MODE
export XDG_CURRENT_DESKTOP=GNOME
export DANWEN_REPO="$REPO" DANWEN_PYTHON="$PYTHON" DANWEN_UUID="$UUID" DANWEN_PROBE="$PROBE"
if [ -n "${SHOTS:-}" ]; then
    mkdir -p "$SHOTS"
    DANWEN_PROBE_SHOTS="$(cd "$SHOTS" && pwd)"
    export DANWEN_PROBE_SHOTS
    # 截圖用 2 倍縮放（畫面上等於 1280×800）與繁體中文介面
    export DANWEN_SIZE="${SIZE:-2560x1600}" DANWEN_SCALE="${SCALE:-2}"
    export LANG="${SHOT_LANG:-zh_TW.UTF-8}" LANGUAGE="${SHOT_LANG:-zh_TW}" LC_ALL="${SHOT_LANG:-zh_TW.UTF-8}"
else
    export DANWEN_SIZE="${SIZE:-1280x800}" DANWEN_SCALE="${SCALE:-1}"
fi

# shellcheck disable=SC2016
dbus-run-session -- bash -c '
    gsettings set org.gnome.shell disable-user-extensions false
    gsettings set org.gnome.shell enabled-extensions "[\"$DANWEN_UUID\", \"$DANWEN_PROBE\"]"
    gsettings set org.gnome.desktop.interface scaling-factor "$DANWEN_SCALE"
    "$DANWEN_PYTHON" "$DANWEN_REPO/dev/fake_danwen.py" > "$XDG_STATE_HOME/fake.log" 2>&1 &
    FAKE=$!
    timeout 60 gnome-shell --headless --virtual-monitor "$DANWEN_SIZE" --mode=user 2>&1 |
        grep -E "PROBE|JS ERROR|JS WARNING|$DANWEN_UUID" | sed -E "s/^.*PROBE ?//" || true
    kill $FAKE 2>/dev/null || true
    echo "── 假的 danwen 收到的操作 ──"
    grep -E "切換|方案" "$XDG_STATE_HOME/fake.log" || true
' 2>/dev/null
