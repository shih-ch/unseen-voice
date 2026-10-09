#!/usr/bin/env bash
# 但聞人語（danwen）移除腳本
#
# 用法：./uninstall.sh [-y] [--dry-run]
#   -y         不逐項詢問：連同設定檔、install.sh 當初安裝的系統套件一起移除
#   --dry-run  只列出會做哪些事，不做任何變更
#
# 依 ~/.local/state/danwen/install-manifest 逐項還原 install.sh（含 --with-whisper、--with-extension）做過的變更；
# 只移除當初由 install.sh 新增的東西（例如原本就在 input 群組，就不會把你移出）。
set -euo pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/danwen"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/danwen"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/danwen"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/danwen"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
MANIFEST="$STATE_DIR/install-manifest"
UDEV_RULE=/etc/udev/rules.d/70-danwen-uinput.rules
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$REPO/scripts/common.sh"

YES=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        -y | --yes) YES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h | --help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) warn "不認得的參數：$arg"; exit 1 ;;
    esac
done
[ "$EUID" -ne 0 ] || { warn "請用一般使用者執行（需要時會自動使用 sudo）"; exit 1; }

# 執行會造成變更的指令；--dry-run 時只印出來
run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '    \033[2m[dry-run] %s\033[0m\n' "$*"
    else
        "$@"
    fi
}

# 問是／否；-y 時一律回答「是」；--dry-run 時不問，當作「是」以便列出完整動作
ask() {
    local prompt="$1" default="$2" reply
    if [ "$DRY_RUN" = 1 ]; then
        printf '    \033[2m[dry-run] 實際執行時會先詢問：%s\033[0m\n' "$prompt"
        return 0
    fi
    if [ "$YES" = 1 ]; then return 0; fi
    read -r -p "    $prompt " reply || reply=""
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[Yy] ]]
}

manifest_values() {
    if [ -f "$MANIFEST" ]; then grep "^$1=" "$MANIFEST" | cut -d= -f2- || true; fi
}

[ "$DRY_RUN" = 0 ] || printf '\033[1;33m（dry-run：只列出動作，不做任何變更）\033[0m\n'
IME_BEFORE="$(ime_snapshot)"
[ -f "$MANIFEST" ] || warn "找不到安裝紀錄 $MANIFEST，只移除已知的 danwen 檔案；群組與系統套件不會變動"
NEED_RELOGIN=0

# ---- 0. GNOME Shell extension：從啟用／停用清單拿掉（執行中的 GNOME 會立刻卸載），再刪檔 ----
say "GNOME Shell extension"
EXT_DIRS=("$EXT_DIR")
# 安裝紀錄裡的位置（以防安裝時的 XDG 路徑不同）；只接受 danwen 自己的 extension 目錄
while read -r d; do
    case "$d" in */gnome-shell/extensions/"$EXT_UUID") EXT_DIRS+=("$d") ;; esac
done < <(manifest_values extension)
EXT_FOUND=0
if command -v gsettings >/dev/null; then
    for key in enabled-extensions disabled-extensions; do
        if NEW="$(strv_edit remove "$key" "$EXT_UUID")"; then
            run gsettings set org.gnome.shell "$key" "$NEW"
            info "從 $key 移除 $EXT_UUID"
            EXT_FOUND=1
        fi
    done
fi
for d in "${EXT_DIRS[@]}"; do
    if [ -d "$d" ]; then
        run rm -rf "$d"
        info "刪除 $d"
        EXT_FOUND=1
    fi
done
[ "$EXT_FOUND" = 1 ] || info "沒有安裝"

# ---- 手動執行的 danwen（例如 sg input -c "danwen -v run"） ----
mapfile -t STRAY < <(stray_danwen_pids)
if [ "${#STRAY[@]}" -gt 0 ]; then
    say "手動執行的 danwen"
    ps -o pid,lstart,cmd -p "$(IFS=,; echo "${STRAY[*]}")" | sed 's/^/    /'
    if ask "要停掉它們嗎？[Y/n]" Y; then
        run kill "${STRAY[@]}" || true
    fi
fi

# ---- 1. 停止並移除服務（含 backend B 的 danwen-whisper.service） ----
say "停止並移除 systemd --user 服務"
for unit in danwen.service danwen-whisper.service; do
    if [ -f "$UNIT_DIR/$unit" ]; then
        run systemctl --user disable --now "$unit" --quiet || true
        run rm -f "$UNIT_DIR/$unit"
        info "移除 $unit"
    else
        info "$unit 不存在"
    fi
done
run systemctl --user daemon-reload
run systemctl --user reset-failed danwen.service danwen-whisper.service 2>/dev/null || true

# ---- 2. 移除 danwen 指令 ----
say "移除 danwen 指令"
if uv tool list 2>/dev/null | grep -q '^danwen '; then
    run uv tool uninstall danwen
    info "移除 uv tool：danwen"
else
    info "沒有安裝"
fi

# ---- 3. uinput 權限規則 ----
say "移除 /dev/uinput 權限規則"
if [ -f "$UDEV_RULE" ]; then
    run sudo rm -f "$UDEV_RULE"
    run sudo udevadm control --reload-rules
    run sudo udevadm trigger --action=change --sysname-match=uinput
    info "移除 $UDEV_RULE"
else
    info "沒有這條規則"
fi

# ---- 4. input 群組（只在當初由 install.sh 加入時才移出） ----
say "input 群組"
if [ -n "$(manifest_values group)" ]; then
    if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
        run sudo gpasswd -d "$USER" input
        NEED_RELOGIN=1
        info "把 $USER 移出 input 群組"
    else
        info "$USER 已不在 input 群組"
    fi
else
    info "不是 install.sh 加入的，保持不變"
fi

# ---- 5. 系統套件（只處理 install.sh 當初新裝的，含相依套件） ----
say "系統套件"
PKGS=()
while read -r p; do
    [ -n "$p" ] || continue
    if dpkg-query -W -f='${db:Status-Abbrev}' "$p" 2>/dev/null | grep -q '^ii'; then
        PKGS+=("$p")
    fi
done < <(manifest_values apt)
if [ "${#PKGS[@]}" -gt 0 ]; then
    info "install.sh 當初安裝、目前仍在的套件：${PKGS[*]}"
    if ask "要移除這些套件嗎？apt 會先列出要移除的內容讓你確認 [Y/n]" Y; then
        # 不加 -y：若之後有其他套件依賴它們，apt 會列出連帶移除的套件並再次詢問
        run sudo apt-get remove --purge "${PKGS[@]}" || warn "套件未移除"
    else
        info "保留"
    fi
else
    info "沒有需要移除的套件"
fi

# ---- 6. 設定檔 ----
say "設定檔與替換字典（$CONFIG_DIR）"
if [ -d "$CONFIG_DIR" ]; then
    if ask "要刪除嗎？裡面可能有你整理的替換字典 [y/N]" N; then
        run rm -rf "$CONFIG_DIR"
        info "刪除 $CONFIG_DIR"
    else
        info "保留"
    fi
else
    info "不存在"
fi

# ---- 7. 模型、whisper.cpp 原始碼與編譯結果、紀錄檔 ----
say "刪除模型、whisper.cpp 與紀錄檔"
DIRS=("$CACHE_DIR" "$DATA_DIR")
# 安裝紀錄裡的 whisper.cpp 位置（以防安裝時的 XDG 路徑不同）；只接受 danwen 自己的目錄
while read -r d; do
    case "$d" in */danwen/whisper.cpp) DIRS+=("$d") ;; esac
done < <(manifest_values whisper)
DIRS+=("$STATE_DIR")  # 安裝紀錄最後刪
for d in "${DIRS[@]}"; do
    if [ -e "$d" ]; then
        info "刪除 $d（$(du -sh "$d" 2>/dev/null | cut -f1)）"
        run rm -rf "$d"
    fi
done

# ---- 8. 確認輸入法設定沒有被動到 ----
say "比對輸入法相關設定（fcitx5、environment.d、~/.xinputrc、/etc/environment）"
IME_AFTER="$(ime_snapshot)"
if [ "$IME_BEFORE" = "$IME_AFTER" ]; then
    info "✓ 前後完全相同，沒有任何變動"
else
    warn "以下檔案在移除期間有變動（uninstall.sh 不會寫入這些檔案；可能是 fcitx5 自己存檔）："
    diff <(echo "$IME_BEFORE") <(echo "$IME_AFTER") | grep '^[<>]' | awk '{print "    " $3}' | sort -u
fi

say "完成"
if [ "$NEED_RELOGIN" = 1 ] && [ "$DRY_RUN" = 0 ]; then
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ]; then
        info "請重新開機，讓 input 群組的變更生效（你的帳號開了 linger，只登出再登入不夠）。"
    else
        info "請登出再登入，讓 input 群組的變更生效。"
    fi
fi
info "共用快取不會刪除（其他程式也在用，內容會自動汰換）：uv 套件快取 ~/.cache/uv、Mesa shader 快取 ~/.cache/mesa_shader_cache"
