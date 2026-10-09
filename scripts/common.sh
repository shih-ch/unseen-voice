# 但聞人語（danwen）：install.sh 與 uninstall.sh 共用的函式，由兩者 source，不單獨執行。
# shellcheck shell=bash

# 以下變數由 source 這個檔案的腳本使用
# shellcheck disable=SC2034
EXT_UUID="danwen@danwen.github.io"
# shellcheck disable=SC2034
EXT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$EXT_UUID"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m警告：%s\033[0m\n' "$*" >&2; }

# 輸入法相關檔案的雜湊，只讀不寫。cached_layouts 是 fcitx5 自己產生的快取，不列入。
ime_snapshot() {
    {
        find "$HOME/.config/fcitx5" "$HOME/.config/environment.d" -type f \
            ! -name cached_layouts -print0 2>/dev/null | sort -z | xargs -0 -r sha256sum
        for f in "$HOME/.xinputrc" /etc/environment; do
            if [ -f "$f" ]; then sha256sum "$f"; fi
        done
    } 2>/dev/null || true
}

# GNOME Shell 設定裡的字串清單（enabled-extensions、disabled-extensions）加入或移除一個項目。
# 只讀不寫：有變動時輸出新的清單（GVariant 格式）並回傳 0；已經是想要的狀態則回傳 1。
# 用法：strv_edit add|remove 設定名稱 值
strv_edit() {
    gsettings get org.gnome.shell "$2" | python3 -c '
import ast, sys
op, value = sys.argv[1], sys.argv[2]
raw = sys.stdin.read().strip()
items = ast.literal_eval(raw[3:].strip() if raw.startswith("@as") else raw)
if op == "add" and value not in items:
    items.append(value)
elif op == "remove" and value in items:
    items.remove(value)
else:
    sys.exit(1)
print("[" + ", ".join(repr(i) for i in items) + "]" if items else "@as []")
' "$1" "$3"
}

# 手動執行、不是由 systemd 服務啟動的 danwen（例如 sg input -c "danwen -v run"）
stray_danwen_pids() {
    local service_pid
    service_pid="$(systemctl --user show -p MainPID --value danwen.service 2>/dev/null || true)"
    pgrep -f 'bin/danwen( -v)? run' | grep -vx "${service_pid:-0}" || true
}
