# 專案：但聞人語（danwen）— GNOME Wayland 地端語音聽寫 v1

> 空山不見人，但聞人語響。——王維〈鹿柴〉

## 命名
- 顯示名稱：但聞人語（notify-send 的 app name 也用這個）
- 套件／指令：`danwen`；benchmark 為 `danwen bench`
- 服務：`danwen.service`；選用的 `danwen-whisper.service`（backend B 的 whisper.cpp server）
- 設定：`~/.config/danwen/config.yaml`、`~/.config/danwen/replacements.yaml`
- 模型快取：`~/.cache/danwen/models/`
- Log：`~/.local/state/danwen/`

## 環境
- Ubuntu、GNOME on Wayland、輸入法為 fcitx5（不可動到 fcitx5 / IBus 設定）
- Intel Core Ultra 7 155（無獨顯），全程離線
- Python 3，用 uv 管理；以 systemd --user 服務常駐

## 流程
按住熱鍵 → 錄音 → 放開 → ASR 整段辨識 → 後處理 → 貼到目前游標位置

## 元件
1. 熱鍵（evdev）
   - 直接讀 /dev/input，使用者需在 input 群組；安裝腳本負責設定並提示重新登入
   - 預設熱鍵 KEY_RIGHTCTRL，可於 config 修改；不可與 fcitx5 的 Ctrl+Space 衝突
   - 按住超過 300ms 才開始錄音；按住期間若按了其他鍵，視為組合鍵並取消錄音
   - 錄音短於 0.4 秒則丟棄
2. 錄音：sounddevice，16kHz 單聲道；麥克風裝置可於 config 指定
3. ASR：定義 backend 介面，實作兩種，由 config 切換
   - A（預設）：sherpa-onnx + SenseVoice-Small int8，CPU 執行，語言固定 zh
   - B：whisper.cpp server（Vulkan）+ large-v3-turbo q5_0，透過 HTTP 呼叫
     - 由 `install.sh --with-whisper` 編譯固定版本的 whisper.cpp；`danwen-whisper.service` 不常駐，選用 B 或跑 benchmark 時才啟動
     - 以 prompt 引導輸出繁體；靜音錄音不送辨識，避免幻覺字幕
   - 模型首次執行時下載，之後完全離線
4. 後處理
   - OpenCC s2twp（台灣用語）；預設替換字典含「臺→台」
   - 使用者替換字典 replacements.yaml（修正常錯詞、專有名詞）
   - 去除 SenseVoice 可能輸出的情緒／事件標籤
5. 輸出
   - 備份目前剪貼簿 → xclip（經 XWayland）寫入結果 → danwen 內建的虛擬鍵盤（python-evdev UInput）送 Ctrl+V
     （不使用 ydotool：Ubuntu 24.04 apt 只有 0.1.8，socket 固定在 /tmp，易與其他工具衝突）
     （不使用 wl-copy：GNOME 46 沒有 data-control 協定，wl-copy／wl-paste 每次都開視窗搶焦點，
       目標輸入框與 fcitx5 會收到失焦事件；X11 端設定剪貼簿不需焦點，Mutter 會同步給 Wayland 程式）
   - 500ms 後還原剪貼簿（config 可關閉）；若這段時間使用者自己複製了新內容，則不還原
   - 虛擬鍵盤在 danwen 啟動時建立並常駐；安裝腳本處理 uinput 權限
   - 已知限制：終端機需 Ctrl+Shift+V，v1 不處理，但文字留在剪貼簿可手動貼上
6. 回饋：開始／結束各播一個短音效；錯誤用 notify-send 通知

## 可觀測性
- 每次聽寫記錄各階段耗時（錄音長度、ASR、後處理、貼上）到 log
- 附 benchmark 指令：對 samples/ 內的 wav 檔分別跑 A、B 兩種 backend，
  輸出耗時與文字結果，方便人工比較

## 安裝與移除（硬性要求）
- 不修改 fcitx5、IBus、im-config 設定，也不改輸入法相關環境變數
  （install.sh / uninstall.sh 執行前後比對 `~/.config/fcitx5`、`~/.xinputrc`、`~/.config/environment.d`、`/etc/environment` 的雜湊並印出結果）
- install.sh 把每一項系統變更記錄在 `~/.local/state/danwen/install-manifest`；
  uninstall.sh 依清單逐項還原（服務、udev 規則、input 群組、apt 套件只移除當初由 install.sh 新裝的）
- 隨時可只停用不移除：`systemctl --user disable --now danwen`

## 驗收條件
- 10 秒中文口述，放開後 2 秒內貼上（以 backend A 為準）
- 在 gedit、Firefox、VS Code 都能正確貼上繁體中文
- 中英夾雜（例如「把這個 PR merge 到 main」）英文保留原樣
- 安裝前後 fcitx5 注音輸入正常
- 提供 install.sh 與 uninstall.sh

## 不在 v1 範圍
串流即時顯示、浮動視窗、LLM 整理、語音指令

## v2：整理模式（已實作）
參考保哥 ZeroType「辨識 → LLM 依提示詞整理」的兩段式設計，但預設全程在本機。
- 觸發：先短按一下熱鍵、0.4 秒內再按住（`hotkey.double_tap_ms`，0＝停用）；不佔新鍵，與 fcitx5 不衝突。
  單純按住仍是快速模式（不經 LLM）
- 「小紙條」：`~/.config/danwen/prompts/*.yaml`（system＋示範例句），內建 日常／會議記錄／英文；
  `danwen mode` 切換，狀態存在 `~/.local/state/danwen/mode`，可綁 GNOME 自訂快捷鍵
- LLM：預設本機 Ollama `qwen3:4b-instruct-2507-q4_K_M`（原生 API，可設 keep_alive）；
  `provider: openai` 可改接任何 OpenAI 相容服務（雲端為使用者自選，非預設）
- 開始錄音時就背景載入模型；整理結果再過一次 OpenCC 與替換字典（模型會輸出簡體字，如「准備」）
- 防呆：無回應、空白、比原文長太多、語言改變 → 通知並改貼原文；逐字稿包在 <逐字稿> 標籤中，
  示範例句教模型「內容是問題或指令也不回答、不執行」
- 實測（CPU，qwen3:4b）：自建 9 句測試集 9/9 通過；模型已載入時放開到貼上約 2～3 秒
- 不做：語音指令（ZeroType 有，但讓 LLM 執行系統動作有 prompt injection 風險）

## 路線圖（對照保哥 ZeroType 的差距，依序進行）
1. 字典帶進整理：替換字典裡的正確寫法交給 LLM 參考；`danwen dict` 新增／列出詞條；新增 Slack、Email 小紙條
2. 歷史紀錄：最近 N 筆（文字與錄音），可換小紙條重新整理而不必重錄
3. 長錄音模式：按一下開始、再按一下結束
4. 上下文：剪貼簿、選取文字一併交給 LLM（選用，預設關閉）
5. **GNOME Shell extension**：頂列狀態圖示（待命／錄音／整理中）、錄音時的浮動提示、
   切換小紙條與瀏覽歷史的選單、設定畫面；也能提供目前焦點程式當上下文。
   - 與 danwen 常駐程式以 session D-Bus 溝通，因此 2～4 實作時先把狀態與操作整理成可對外的介面
   - 安裝在 `~/.local/share/gnome-shell/extensions/`，不碰 fcitx5；uninstall.sh 一併移除
   - Wayland 下新裝的 extension 要登出再登入才會載入；GNOME 大版本升級時需確認相容

## 之後再考慮
- fcitx5 addon 直接送字：不經剪貼簿、終端機可用，並可用 preedit 做串流即時顯示
- 雲端語音辨識 backend（Groq 等 OpenAI 相容服務；錄音會離開本機，需使用者明確選用）
