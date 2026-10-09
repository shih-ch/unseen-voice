#!/usr/bin/env bash
# 開發用：在完全隔離的 GNOME 鑰匙圈裡測試 danwen 的金鑰存取（存入、讀回、覆寫、刪除）。
#
# 隔離方式：獨立的 session D-Bus、鑰匙圈檔案（XDG_DATA_HOME）與 runtime 目錄，並移除畫面相關的環境變數，
# 不會碰到你真正的鑰匙圈，也不會跳出任何視窗；結束時比對真正的鑰匙圈檔案並清除暫存。
# runtime 目錄放在 /run/user/$UID 底下的短路徑：socket 路徑超過約 108 字元時 gnome-keyring 會無法啟動。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
[ -x "$PYTHON" ] || { echo "請先在專案目錄執行 uv sync" >&2; exit 1; }

snapshot() { (cd "$HOME/.local/share/keyrings" 2>/dev/null && sha256sum ./* 2>/dev/null) || true; }
BEFORE="$(snapshot)"
TMP="$(mktemp -d)"
RUN="$(mktemp -d "/run/user/$(id -u)/dk.XXXX")"
chmod 700 "$RUN"
cleanup() {
    for p in $(pgrep -f gnome-keyring-daemon || true); do
        if tr '\0' '\n' <"/proc/$p/environ" 2>/dev/null | grep -qx "XDG_RUNTIME_DIR=$RUN"; then kill "$p"; fi
    done
    rm -rf "$TMP" "$RUN"
}
trap cleanup EXIT

export DANWEN_PYTHON="$PYTHON"
# 內層指令刻意用單引號，變數經由環境變數傳入
# shellcheck disable=SC2016
OUTPUT="$(timeout 45 env -u GNOME_KEYRING_CONTROL -u SSH_AUTH_SOCK -u DISPLAY -u WAYLAND_DISPLAY \
    XDG_DATA_HOME="$TMP/data" XDG_CONFIG_HOME="$TMP/config" XDG_RUNTIME_DIR="$RUN" \
    dbus-run-session -- bash -c '
        printf "test-only-password" | timeout 10 gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1
        for _ in $(seq 25); do [ -e "$XDG_DATA_HOME/keyrings/login.keyring" ] && break; sleep 0.2; done
        timeout 20 "$DANWEN_PYTHON" -c "
from danwen.credentials import KeyringStore
s = KeyringStore()
assert s.providers() == []
s.set(\"groq\", \"k1\"); s.set(\"cloudflare\", \"k2\")
assert s.providers() == [\"cloudflare\", \"groq\"] and s.get(\"groq\") == \"k1\"
s.set(\"groq\", \"k3\")
assert s.providers() == [\"cloudflare\", \"groq\"] and s.get(\"groq\") == \"k3\"
assert s.delete(\"groq\") == [\"groq\"] and s.providers() == [\"cloudflare\"]
assert s.delete(None) == [\"cloudflare\"] and s.providers() == []
print(\"✓ 存入、讀回、覆寫、刪除都正確\")
"
    ' 2>&1 || true)"
grep -vE 'dbus-daemon\[|Gtk-WARNING|^\*\* Message|discover_other_daemon' <<<"$OUTPUT" || true
grep -q "✓ 存入、讀回、覆寫、刪除都正確" <<<"$OUTPUT" || { echo "✗ 鑰匙圈測試失敗" >&2; exit 1; }

if [ "$BEFORE" = "$(snapshot)" ]; then
    echo "✓ 真正的鑰匙圈檔案前後完全相同"
else
    echo "✗ 真正的鑰匙圈有變動" >&2
    exit 1
fi
