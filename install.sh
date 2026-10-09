#!/usr/bin/env bash
# 但聞人語（danwen）安裝腳本
#
# 用法：./install.sh [--with-whisper]
#   --with-whisper  另外編譯 whisper.cpp（Vulkan）並下載模型，啟用 backend B
#
# 每一項系統變更都記錄在 ~/.local/state/danwen/install-manifest，uninstall.sh 依此逐項還原。
# 不會修改 fcitx5、IBus、im-config 設定或輸入法環境變數；安裝前後會比對並印出結果。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/danwen"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/danwen"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/danwen"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
MANIFEST="$STATE_DIR/install-manifest"
UDEV_RULE=/etc/udev/rules.d/70-danwen-uinput.rules
WHISPER_TAG=v1.9.5
WHISPER_DIR="$DATA_DIR/whisper.cpp"
WHISPER_MODEL="$CACHE_DIR/models/whisper-large-v3-turbo/ggml-large-v3-turbo-q5_0.bin"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m警告：%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m錯誤：%s\033[0m\n' "$*" >&2; exit 1; }

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

WITH_WHISPER=0
for arg in "$@"; do
    case "$arg" in
        --with-whisper) WITH_WHISPER=1 ;;
        -h | --help) usage 0 ;;
        *) warn "不認得的參數：$arg"; usage 1 ;;
    esac
done

# 記錄一項變更（重複執行 install.sh 不會重複記錄）
record() {
    grep -qxF "$1" "$MANIFEST" 2>/dev/null || echo "$1" >>"$MANIFEST"
}

# 目前已安裝（狀態 ii）的套件清單，排序後供 comm 比對
installed_packages() {
    dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package}\n' | awk '$1 == "ii" {print $2}' | sort
}

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

# ---- 前置檢查 ----
[ "$EUID" -ne 0 ] || die "請用一般使用者執行（需要時會自動使用 sudo）"
command -v uv >/dev/null || die "找不到 uv，請先安裝：https://docs.astral.sh/uv/"
systemctl --user show-environment >/dev/null 2>&1 || die "無法使用 systemctl --user"
if [ "${XDG_SESSION_TYPE:-}" != wayland ]; then
    warn "目前不是 Wayland 工作階段（XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-未設定}），本程式是為 GNOME Wayland 設計"
fi

IME_BEFORE="$(ime_snapshot)"
mkdir -p "$STATE_DIR"
touch "$MANIFEST"

# ---- 1. 系統套件（只安裝缺少的，並記錄下來） ----
say "檢查系統套件"
PKGS=(xclip libportaudio2 libnotify-bin)
if [ "$WITH_WHISPER" = 1 ]; then
    # whisper.cpp 的 Vulkan 版需要 glslc 與 SPIRV-Headers 的 CMake 設定
    PKGS+=(git cmake build-essential libvulkan-dev glslc spirv-headers)
fi
MISSING=()
for p in "${PKGS[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
        MISSING+=("$p")
    fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    info "需要安裝：${MISSING[*]}"
    PKGS_BEFORE="$(installed_packages)"
    sudo apt-get install -y "${MISSING[@]}"
    # 比對安裝前後，連同 apt 自動帶進來的相依套件一起記錄，uninstall.sh 才能完整移除
    NEW_PKGS="$(comm -13 <(echo "$PKGS_BEFORE") <(installed_packages))"
    for p in $NEW_PKGS; do record "apt=$p"; done
    info "新安裝的套件（含相依）：$(echo "$NEW_PKGS" | tr '\n' ' ')"
else
    info "都已安裝"
fi

# ---- 2. input 群組（讀取鍵盤熱鍵） ----
say "設定 input 群組（讀取熱鍵用）"
if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    info "$USER 已在 input 群組"
else
    sudo usermod -aG input "$USER"
    record "group=input"
    info "已把 $USER 加入 input 群組"
fi

# ---- 3. uinput 權限（虛擬鍵盤送 Ctrl+V） ----
say "設定 /dev/uinput 權限（虛擬鍵盤用）"
if [ -f "$UDEV_RULE" ]; then
    info "已存在：$UDEV_RULE"
else
    echo 'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' |
        sudo tee "$UDEV_RULE" >/dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger --action=change --sysname-match=uinput
    info "已建立：$UDEV_RULE"
fi
record "udev=$UDEV_RULE"

# ---- 4. 安裝 danwen 指令 ----
say "安裝 danwen（uv tool）"
uv tool install --force --reinstall --quiet "$REPO"
DANWEN_BIN="$(uv tool dir --bin)/danwen"
[ -x "$DANWEN_BIN" ] || die "安裝後找不到 $DANWEN_BIN"
record "uvtool=danwen"
info "$DANWEN_BIN"

# ---- 5. 設定檔與模型 ----
say "建立設定檔"
"$DANWEN_BIN" init-config | sed 's/^/    /'
say "下載 SenseVoice 模型（首次約 230 MB）"
"$DANWEN_BIN" download -b sensevoice || warn "模型下載失敗，danwen 首次啟動時會再試"

# ---- 6. backend B：whisper.cpp（選用） ----
if [ "$WITH_WHISPER" = 1 ]; then
    say "編譯 whisper.cpp $WHISPER_TAG（Vulkan），需要幾分鐘"
    if [ -d "$WHISPER_DIR/.git" ]; then
        git -C "$WHISPER_DIR" fetch --quiet --depth 1 origin tag "$WHISPER_TAG"
        git -C "$WHISPER_DIR" checkout --quiet "$WHISPER_TAG"
    else
        mkdir -p "$DATA_DIR"
        git clone --quiet --depth 1 --branch "$WHISPER_TAG" https://github.com/ggml-org/whisper.cpp "$WHISPER_DIR"
    fi
    cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON \
        -DWHISPER_BUILD_TESTS=OFF >/dev/null
    cmake --build "$WHISPER_DIR/build" --config Release -j "$(nproc)" --target whisper-server >/dev/null
    WHISPER_SERVER="$WHISPER_DIR/build/bin/whisper-server"
    [ -x "$WHISPER_SERVER" ] || die "編譯失敗：找不到 $WHISPER_SERVER"
    record "whisper=$WHISPER_DIR"
    say "下載 Whisper large-v3-turbo q5_0 模型（約 574 MB）"
    "$DANWEN_BIN" download -b whisper_server || warn "模型下載失敗，首次使用 backend B 時會再試"
    mkdir -p "$UNIT_DIR"
    sed -e "s|@WHISPER_SERVER@|$WHISPER_SERVER|" -e "s|@WHISPER_MODEL@|$WHISPER_MODEL|" \
        "$REPO/systemd/danwen-whisper.service" >"$UNIT_DIR/danwen-whisper.service"
    record "unit=danwen-whisper.service"
    info "已安裝 danwen-whisper.service（不常駐，用到時才啟動）"
fi

# ---- 7. systemd 服務 ----
say "安裝 systemd --user 服務"
mkdir -p "$UNIT_DIR"
sed "s|@DANWEN_BIN@|$DANWEN_BIN|" "$REPO/systemd/danwen.service" >"$UNIT_DIR/danwen.service"
record "unit=danwen.service"
systemctl --user daemon-reload
systemctl --user enable danwen.service --quiet
NEED_RELOGIN=0
if id -nG | tr ' ' '\n' | grep -qx input; then
    systemctl --user restart danwen.service
    info "已啟動 danwen.service"
else
    NEED_RELOGIN=1
    info "已設為登入後自動啟動（目前的登入工作階段還沒有 input 群組權限）"
fi

# ---- 8. 確認輸入法設定沒有被動到 ----
say "比對輸入法相關設定（fcitx5、environment.d、~/.xinputrc、/etc/environment）"
IME_AFTER="$(ime_snapshot)"
if [ "$IME_BEFORE" = "$IME_AFTER" ]; then
    info "✓ 安裝前後完全相同，沒有任何變動"
else
    warn "以下檔案在安裝期間有變動（install.sh 不會寫入這些檔案；可能是 fcitx5 自己存檔）："
    diff <(echo "$IME_BEFORE") <(echo "$IME_AFTER") | grep '^[<>]' | awk '{print "    " $3}' | sort -u
fi

say "完成"
if [ "$NEED_RELOGIN" = 1 ]; then
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ]; then
        # linger 開啟時 systemd --user 登出後仍在執行，不會取得新群組
        printf '\033[1;33m    請重新開機，讓 input 群組生效（你的帳號開了 linger，只登出再登入不夠）。\033[0m\n'
    else
        printf '\033[1;33m    請登出再登入（或重新開機），讓 input 群組生效，之後就會自動啟動。\033[0m\n'
    fi
    printf '    不想等的話，可先在終端機前景試用：sg input -c "%s -v run"\n' "$DANWEN_BIN"
fi
cat <<EOF
    使用：按住右 Ctrl 超過 0.3 秒開始錄音，放開後自動貼上
    設定：~/.config/danwen/config.yaml、replacements.yaml
    紀錄：journalctl --user -u danwen -f
    暫停：systemctl --user disable --now danwen
    移除：$REPO/uninstall.sh
EOF
