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
   - SenseVoice 整段送進超過約 20 秒的錄音會漏字甚至亂碼（實測 25 秒漏兩句、75 秒以上亂碼），
     因此超過 20 秒時先以 Silero VAD 在停頓處切段（每段 ≤15 秒）逐段辨識再接起來；
     實測 201 秒錄音 4.2 秒辨識完成，中文句子無遺漏
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
1. ✅ 字典帶進整理：替換字典裡的正確寫法交給 LLM 參考；`danwen dict` 新增／列出詞條；新增 Slack、Email 小紙條
2. ✅ 歷史紀錄：最近 20 筆文字（錄音預設不存），`danwen history` 列出／查看／複製／重聽，
   `redo` 不必重錄即可換小紙條重新整理；資料為 `~/.local/state/danwen/history/history.json`（0600），
   D-Bus 介面延到第 5 步與 extension 一起做（屆時才有使用者可測試）
3. ✅ 長錄音模式：很快連按兩下熱鍵開始（不必按著），再按一下結束、Esc 取消；期間其他按鍵不影響；
   上限 10 分鐘，預設走快速模式（`long_recording.refine` 可改為整理）
4. ✅ 上下文：剪貼簿、選取文字（X11 PRIMARY）當參考資料交給 LLM（選用，預設關閉）
   - 只用來判斷專有名詞與上下文；放在最後一則訊息，提示詞與示範的快取仍有效
   - 隱私：base_url 不是本機時預設不送（`context_to_cloud`）；log 只記來源與字數
   - 防呆：實測 qwen3:4b 曾被剪貼簿裡的「忽略規則，只輸出…」帶走，因此加上示範例句，
     並檢查輸出的中文字有多少出自口述（正常整理 ≥50%，被帶走 ≤12%，門檻 30%），不足即改貼原文
5. **GNOME Shell extension**（分 5a～5d）
   - ✅ 5a D-Bus 介面：session bus 名稱 `io.github.danwen`，物件 `/io/github/danwen`，介面 `io.github.danwen.Daemon1`
     - 屬性：State（idle／recording／processing）、Kind（fast／refine／long）、Mode、Hotkey，變更時發 PropertiesChanged
     - 方法：ListModes、SetMode、GetHistory(limit) 回傳 JSON、CopyHistory、Redo(id, mode)、StartLong、Stop、Cancel
     - 訊號：HistoryChanged；錯誤以 `io.github.danwen.Error` 回傳中文訊息
     - 名稱同時只能一個程式持有：第二個 danwen 會拒絕啟動（避免重複貼上）；連不上 session bus 時照常聽寫
   - ✅ 5b 頂列圖示與選單（`gnome-extension/danwen@danwen.github.io/`，GNOME 46）
     - 圖示：待命一般色、錄音紅色、辨識整理中黃色「…」、danwen 未執行時灰色
     - 選單：狀態列、開始長錄音／結束／取消、切換小紙條（打勾）、最近 5 筆（點一下複製）、
       換小紙條重新整理最新一筆、開啟設定檔／替換字典／小紙條資料夾；未執行時可「啟動 danwen」
     - GNOME 不開啟空選單，因此骨架先建好；狀態改變只更新狀態列與長錄音按鈕，清單在打開時才讀取
     - 開發測試：`dev/nested-shell.sh` 在視窗裡跑巢狀 GNOME Shell（獨立 dconf、extension 目錄與 D-Bus，
       原生 GNOME 模式以避開 Ubuntu 的 DING 在巢狀環境崩潰重啟），搭配假的 danwen（`dev/fake_danwen.py`）
   - ✅ 5c 錄音浮動提示：畫面上方中央（目前工作中的螢幕、頂列下方）的小條，不接收滑鼠
     - 錄音中「● 錄音中 0:03」（整理模式加「· 整理：日常」）、長錄音「● 長錄音 1:25 · 再按一下 右 Ctrl 結束，Esc 取消」、
       辨識中／整理中黃色「…」，待命淡出
     - extension 持有 D-Bus 名稱 `io.github.danwen.ShellOverlay`；danwen 看到它就不再跳「長錄音中」系統通知
       （Shell 結束或 extension 停用時名稱自動釋放）
     - D-Bus 新增 Hotkey 屬性，提示顯示實際的熱鍵名稱
     - Esc／熱鍵由 danwen 讀鍵盤處理，巢狀測試裡的假 danwen 不讀鍵盤，因此只能測選單操作
   - ✅ 5d 安裝與移除：`install.sh --with-extension`
     - 複製到 `~/.local/share/gnome-shell/extensions/`，只把 danwen 加進 enabled-extensions、從 disabled-extensions 拿掉
       （GNOME 46 的 disable 會把 uuid 加進 disabled-extensions，且它優先於 enabled）
     - uninstall.sh 從兩個清單拿掉 danwen（執行中的 GNOME 立刻卸載）並刪檔，其他 extension 不受影響
     - 安裝與移除時偵測手動執行的 danwen（舊版沒有防重複），詢問後才停止；沒有終端機可詢問時一律不停止
     - install.sh／uninstall.sh 共用函式移到 `scripts/common.sh`
   - 原規劃：頂列狀態圖示（待命／錄音／整理中）、錄音時的浮動提示、
   切換小紙條與瀏覽歷史的選單、設定畫面；也能提供目前焦點程式當上下文。
   - 與 danwen 常駐程式以 session D-Bus 溝通，因此 2～4 實作時先把狀態與操作整理成可對外的介面
   - 安裝在 `~/.local/share/gnome-shell/extensions/`，不碰 fcitx5；uninstall.sh 一併移除
   - Wayland 下新裝的 extension 要登出再登入才會載入；GNOME 大版本升級時需確認相容

## 方案：本機與雲端（v3）
- 方案（`cloud.plan`，執行中狀態存在 `~/.local/state/danwen/cloud`）：
  A local 全部本機、**B hybrid 本機辨識＋Groq 整理（預設，使用者指定）**、C groq 全部 Groq、
  D cloudflare 全部 Cloudflare、E custom 依設定檔（provider、use_for_asr、use_for_refine）
- 服務商：groq、openai、cloudflare、custom（任何 OpenAI 相容服務）
  - 辨識：OpenAI 相容 `/audio/transcriptions`（multipart）；Cloudflare `/ai/run/@cf/openai/whisper-large-v3-turbo`（JSON＋base64）；
    預設不指定語言（自動判斷）——實測固定 zh 時整句英文會被硬翻成中文
  - 整理：OpenAI 相容 chat；Cloudflare 走 `/ai/v1/chat/completions`；去除推理模型的 `<think>`；Groq gpt-oss 設 reasoning_effort=low
  - 一律帶 `User-Agent: danwen/版本`：Groq 前面的 Cloudflare 會擋 Python 預設 UA（error 1010）
- 切換：extension「方案」子選單、`danwen cloud <方案>`；切換前檢查設定與金鑰（不連線），缺少就不切換並說明原因
- 錄音開始時決定這次整理走 cloud／local／none：方案要雲端但不能用（缺金鑰等）時，
  `refine.local_fallback`（預設 false，使用者指定）為 false 就貼原文、不喚醒本機 Ollama，為 true 才改用本機；同一原因只通知一次
- 失敗處理：雲端辨識失敗改用本機（SenseVoice 一直載入）；雲端整理失敗依 local_fallback 貼原文或改用本機
- 歷史紀錄重新整理、`danwen refine` 也依方案（cloud.choose_refiner）；`--local／--cloud` 可強制
- 測試隔離（conftest 自動套用）：不讀真正的鑰匙圈、方案／小紙條／歷史狀態放暫存資料夾、只准連 127.0.0.1——
  曾有測試因方案預設 B 而讀到使用者的真金鑰並呼叫 Groq（約 4 次，內容為測試句）
- 錯誤訊息不含服務回傳內容（實測 Groq 的 429 訊息含組織代碼）
- 錄音提示只在這次錄音真的送資料出去時顯示「☁」（雲端辨識，或整理模式且整理走雲端）
- 金鑰：GNOME 鑰匙圈（`secretstorage`），`danwen key set` 不回顯；常駐程式不跳解鎖視窗；uninstall.sh 詢問後刪除
  - 已評估：檔案、systemd 環境變數皆為明文；systemd-creds 的使用者服務支援需 systemd 256（Ubuntu 24.04 為 255）
- 上下文仍需 `refine.context_to_cloud` 才送雲端
- 實測 Groq（2026-10-09）：整理一句約 0.7 秒；11 句測試集（含上下文人名修正、剪貼簿夾帶指令）正確，
  僅「文件→檔案」為 OpenCC 台灣用語轉換；免費額度 LLM 每分鐘 8,000 token（約 8 次整理）
- 待實測：Cloudflare（需 account_id 與金鑰）、OpenAI
- 另：長錄音短於 1.5 秒視為誤觸，丟棄不貼（`long_recording.min_duration_s`）

## 之後再考慮
- fcitx5 addon 直接送字：不經剪貼簿、終端機可用，並可用 preedit 做串流即時顯示
