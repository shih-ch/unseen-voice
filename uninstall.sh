#!/usr/bin/env bash
# 但聞人語（danwen）移除腳本
#
# 用法：./uninstall.sh [-y]
#   -y  不逐項詢問：連同設定檔、install.sh 當初安裝的系統套件一起移除
#
# 依 ~/.local/state/danwen/install-manifest 逐項還原 install.sh 做過的變更；
# 只移除當初由 install.sh 新增的東西（例如原本就在 input 群組，就不會把你移出）。
set -euo pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/danwen"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/danwen"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/danwen"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/danwen"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
MANIFEST="$STATE_DIR/install-manifest"
UDEV_RULE=/etc/udev/rules.d/70-danwen-uinput.rules

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m警告：%s\033[0m\n' "$*" >&2; }

YES=0
case "${1:-}" in
    -y | --yes) YES=1 ;;
    -h | --help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) warn "不認得的參數：$1"; exit 1 ;;
esac
[ "$EUID" -ne 0 ] || { warn "請用一般使用者執行（需要時會自動使用 sudo）"; exit 1; }

# 問是／否；-y 時一律回答「是」
ask() {
    local prompt="$1" default="$2" reply
    if [ "$YES" = 1 ]; then return 0; fi
    read -r -p "    $prompt " reply || reply=""
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[Yy] ]]
}

manifest_values() {
    if [ -f "$MANIFEST" ]; then grep "^$1=" "$MANIFEST" | cut -d= -f2- || true; fi
}

# 與 install.sh 相同：輸入法相關檔案的雜湊，只讀不寫
ime_snapshot() {
    {
        find "$HOME/.config/fcitx5" "$HOME/.config/environment.d" -type f \
            ! -name cached_layouts -print0 2>/dev/null | sort -z | xargs -0 -r sha256sum
        for f in "$HOME/.xinputrc" /etc/environment; do
            if [ -f "$f" ]; then sha256sum "$f"; fi
        done
    } 2>/dev/null || true
}

IME_BEFORE="$(ime_snapshot)"
[ -f "$MANIFEST" ] || warn "找不到安裝紀錄 $MANIFEST，只移除已知的 danwen 檔案；群組與系統套件不會變動"
NEED_RELOGIN=0

# ---- 1. 停止並移除服務 ----
say "停止並移除 systemd --user 服務"
for unit in danwen.service danwen-whisper.service; do
    systemctl --user disable --now "$unit" --quiet 2>/dev/null || true
    if [ -f "$UNIT_DIR/$unit" ]; then
        rm -f "$UNIT_DIR/$unit"
        info "已移除 $unit"
    fi
done
systemctl --user daemon-reload
systemctl --user reset-failed danwen.service danwen-whisper.service 2>/dev/null || true

# ---- 2. 移除 danwen 指令 ----
say "移除 danwen 指令"
if uv tool list 2>/dev/null | grep -q '^danwen '; then
    uv tool uninstall danwen >/dev/null
    info "已移除"
else
    info "沒有安裝"
fi

# ---- 3. uinput 權限規則 ----
say "移除 /dev/uinput 權限規則"
if [ -f "$UDEV_RULE" ]; then
    sudo rm -f "$UDEV_RULE"
    sudo udevadm control --reload-rules
    sudo udevadm trigger --action=change --sysname-match=uinput
    info "已移除 $UDEV_RULE"
else
    info "沒有這條規則"
fi

# ---- 4. input 群組（只在當初由 install.sh 加入時才移出） ----
say "input 群組"
if [ -n "$(manifest_values group)" ]; then
    if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
        sudo gpasswd -d "$USER" input >/dev/null
        NEED_RELOGIN=1
        info "已把 $USER 移出 input 群組（登出再登入後生效）"
    fi
else
    info "不是 install.sh 加入的，保持不變"
fi

# ---- 5. 系統套件（只處理 install.sh 當初新裝的） ----
say "系統套件"
mapfile -t PKGS < <(manifest_values apt)
if [ "${#PKGS[@]}" -gt 0 ]; then
    info "install.sh 當初安裝了：${PKGS[*]}"
    if ask "要移除這些套件嗎？apt 會先列出要移除的內容讓你確認 [Y/n]" Y; then
        # 不加 -y：如果有其他套件依賴它們，apt 會列出來並再次詢問
        sudo apt-get remove "${PKGS[@]}" || warn "套件未移除"
    else
        info "保留"
    fi
else
    info "install.sh 沒有安裝任何系統套件"
fi

# ---- 6. 設定檔 ----
say "設定檔與替換字典（$CONFIG_DIR）"
if [ -d "$CONFIG_DIR" ]; then
    if ask "要刪除嗎？裡面可能有你整理的替換字典 [y/N]" N; then
        rm -rf "$CONFIG_DIR"
        info "已刪除"
    else
        info "保留"
    fi
else
    info "不存在"
fi

# ---- 7. 模型、whisper.cpp、紀錄檔 ----
say "刪除模型、whisper.cpp 與紀錄檔"
for d in "$CACHE_DIR" "$DATA_DIR" "$STATE_DIR"; do
    if [ -e "$d" ]; then
        rm -rf "$d"
        info "已刪除 $d"
    fi
done

# ---- 8. 確認輸入法設定沒有被動到 ----
say "比對輸入法相關設定（fcitx5、environment.d、~/.xinputrc、/etc/environment）"
IME_AFTER="$(ime_snapshot)"
if [ "$IME_BEFORE" = "$IME_AFTER" ]; then
    info "✓ 移除前後完全相同，沒有任何變動"
else
    warn "以下檔案在移除期間有變動（uninstall.sh 不會寫入這些檔案；可能是 fcitx5 自己存檔）："
    diff <(echo "$IME_BEFORE") <(echo "$IME_AFTER") | grep '^[<>]' | awk '{print "    " $3}' | sort -u
fi

say "完成"
if [ "$NEED_RELOGIN" = 1 ]; then
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ]; then
        info "請重新開機，讓 input 群組的變更生效（你的帳號開了 linger，只登出再登入不夠）。"
    else
        info "請登出再登入，讓 input 群組的變更生效。"
    fi
fi
