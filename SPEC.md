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
- Intel Core Ultra 7 155（無獨顯），全程離線（v1；v3 起整理模式可選雲端，見「方案」）
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
參考保哥 ZeroType「辨識 → LLM 依提示詞整理」的兩段式設計，v2 預設全程在本機。
（v3 起改由「方案」決定，預設方案 B：辨識在本機、整理走 Groq，見下方「方案：本機與雲端」。）
- 觸發：先短按一下熱鍵、0.4 秒內再按住（`hotkey.double_tap_ms`，0＝停用）；不佔新鍵，與 fcitx5 不衝突。
  單純按住仍是快速模式（不經 LLM）
- 「小紙條」：`~/.config/danwen/prompts/*.yaml`（system＋示範例句），內建 日常／會議記錄／翻譯；
  `danwen mode` 切換，狀態存在 `~/.local/state/danwen/mode`，可綁 GNOME 自訂快捷鍵
- LLM：本機 Ollama `qwen3:4b-instruct-2507-q4_K_M`（原生 API，可設 keep_alive；v3 起為方案 A 使用）；
  `provider: openai` 可改接任何 OpenAI 相容服務（v2 時雲端為使用者自選；v3 起預設方案 B 用 Groq）
- 開始錄音時就背景載入模型；整理結果再過一次 OpenCC 與替換字典（模型會輸出簡體字，如「准備」；
  翻譯類的小紙條設 postprocess: false 不轉）
- 防呆：無回應、空白、比原文長太多、語言改變 → 通知並改貼原文；逐字稿包在 <逐字稿> 標籤中，
  示範例句教模型「內容是問題或指令也不回答、不執行」
- 實測（CPU，qwen3:4b）：自建 9 句測試集 9/9 通過；模型已載入時放開到貼上約 2～3 秒
- 不做：語音指令（ZeroType 有，但讓 LLM 執行系統動作有 prompt injection 風險）

## 路線圖（對照保哥 ZeroType 的差距，依序進行）
1. ✅ 字典帶進整理：替換字典裡的正確寫法交給 LLM 參考；`danwen dict` 新增／列出詞條；新增 Slack、Email 小紙條（Slack 於 2026-10-10 改為「社群」：使用者不用 Slack）
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
  - 整理：OpenAI 相容 chat；Cloudflare 走 `/ai/v1/chat/completions`；去除推理模型的 `<think>`；模型專屬參數見「模型選擇」（gpt-oss：reasoning_effort=low；Qwen3.8：none）
  - 一律帶 `User-Agent: danwen/版本`：Groq 前面的 Cloudflare 會擋 Python 預設 UA（error 1010）
- 切換：extension「方案」子選單、`danwen cloud <方案>`；切換前檢查設定與金鑰（不連線），缺少就不切換並說明原因
- 錄音開始時決定這次整理走 cloud／local／none：方案要雲端但不能用（缺金鑰等）時，
  `refine.local_fallback`（預設 false，使用者指定）為 false 就貼原文、不喚醒本機 Ollama，為 true 才改用本機；同一原因只通知一次
- 失敗處理：雲端辨識失敗改用本機（SenseVoice 一直載入）；雲端整理失敗依 local_fallback 貼原文或改用本機
- 歷史紀錄重新整理、`danwen refine` 也依方案（cloud.choose_refiner）；`--local／--cloud` 可強制
- 測試隔離（conftest 自動套用）：不讀真正的鑰匙圈、方案／小紙條／歷史狀態放暫存資料夾、只准連 127.0.0.1——
  曾有測試因方案預設 B 而讀到使用者的真金鑰並呼叫 Groq（約 4 次，內容為測試句）
- 也不碰桌面：不發通知、不播提示音、剪貼簿用記憶體裡的假剪貼簿、不建立 uinput 虛擬鍵盤、不呼叫 gsettings——
  曾有測試執行 `danwen mode next`／`translate next`（pytest 沒有終端機，改發桌面通知），使用者桌面冒出一串
  「整理模式改用小紙條」，看起來像模式自己在換（2026-10-11）
- 錯誤訊息不含服務回傳內容（實測 Groq 的 429 訊息含組織代碼）
- 錄音提示只在這次錄音真的送資料出去時顯示「☁」（雲端辨識，或整理模式且整理走雲端）
- 金鑰：GNOME 鑰匙圈（`secretstorage`），`danwen key set` 不回顯；常駐程式不跳解鎖視窗；uninstall.sh 詢問後刪除
  - 已評估：檔案、systemd 環境變數皆為明文；systemd-creds 的使用者服務支援需 systemd 256（Ubuntu 24.04 為 255）
- 上下文仍需 `refine.context_to_cloud` 才送雲端
- 替換字典的專有名詞（正確寫法）預設只給本機 LLM（Refiner.terms_allowed）；`refine.terms_to_cloud: true` 才交給雲端整理
  與雲端辨識（Whisper prompt）。替換一律在本機，關閉時照樣改對（2026-10-11，使用者要求預設不送）
- 實測 Groq（2026-10-09）：整理一句約 0.7 秒；11 句測試集（含上下文人名修正、剪貼簿夾帶指令）正確，
  僅「文件→檔案」為 OpenCC 台灣用語轉換；免費額度 LLM 每分鐘 8,000 token（約 8 次整理）
- 待實測：Cloudflare（需 account_id 與金鑰）、OpenAI
- 另：長錄音短於 1.5 秒視為誤觸，丟棄不貼（`long_recording.min_duration_s`）

## 快速切換小紙條
- extension：滑鼠停在圖示上滾動滾輪切換（間隔 0.25 秒內的多格忽略；觸控板累積一格才換），
  以 GNOME 內建的浮動提示（Main.osdWindowManager）顯示目前小紙條
- `danwen mode next／prev`；D-Bus `CycleMode(step)`
- `danwen shortcuts install／remove／status`：新增 GNOME 自訂快捷鍵 Super+Alt+M（下一張）、Super+Alt+1～5
  （日常、會議記錄、Email、社群、翻譯）、Super+Alt+T（翻譯換下一種語言）；路徑以 danwen- 開頭，只動自己的項目；
  安裝前檢查 GNOME 內建與既有自訂快捷鍵，佔用的略過；uninstall.sh 以專案程式（只用標準函式庫）移除
- 不做：按住錄音鍵＋數字（danwen 攔不住按鍵，數字會同時送進程式）、由 install.sh 自動建立 GNOME 快捷鍵（改為使用者明確執行 danwen shortcuts install）

## 翻譯的目標語言
- 一張「翻譯」小紙條＋另外選語言（使用者選定此做法，而非每種語言一張）：`翻譯.yaml` 的 `languages` 段落列出
  英文／日文／簡體中文，各有 target（填進 system 的 {language}）與該語言的示範；取代原本的「英文」小紙條
- 選擇存在 `~/.local/state/danwen/language`；選語言時一併切到翻譯。指定小紙條的地方都接受「翻譯（日文）」，
  不是小紙條名稱的語言「日文」也視為翻譯（日文（舊快捷鍵的） `mode 英文` 因此仍可用）
- 歷史紀錄、log 記成「翻譯（日文）」；D-Bus 加 ModeLabel、Language 屬性與 ListLanguages、SetLanguage；
  CycleMode 回傳顯示用名稱
- 小紙條可設 `postprocess: false`：整理後不再轉繁體、不套替換字典（OpenCC s2twp 會把日文「学」「会」改成「學」「會」）
- 長度防呆改成比「字／詞」數（中日韓一字一個、其他語言一詞一個）：英文譯文的字母數常是中文原文的三倍以上，
  原本用字元數比，一句 47 字的中文翻成英文就被當成「在回答」而改貼原文
- init-config 會移除使用者沒改過的舊 `英文.yaml`（比對雜湊），改過的保留並提示
- 實測：Groq 三種語言都正確（每句 0.3～0.7 秒，長句約 2 秒）；本機 qwen3:4b 英文、簡體正確，
  日文曾把「不對，是下週四」翻錯，翻日文建議用雲端

## 與 ZeroType 的差距（2026-10-09 盤點）與下一步路線圖
對照保哥 ZeroType（[課程頁](https://zerotype.gh.miniasp.com/)、
[使用者心得](https://www.opasschang.com/blog/i-paid-2800-for-voice-input-class-then-forked-instead)）。

已有：按住說話、長錄音、雲端 Whisper（可選，預設本機）、小紙條（日常／會議記錄／Email／社群／翻譯，可自訂）、
刪贅詞與補標點、台灣繁體、自訂字典、貼上後還原剪貼簿、歷史 20 筆（複製／重聽／換小紙條重新整理）、
上下文（剪貼簿、選取文字）、自選服務商。

做法不同：
- 切換小紙條：ZeroType 是按住錄音鍵時按 0～9；danwen 只監聽不攔截按鍵，數字會同時送進程式，
  改用滾輪、Super+Alt+數字、選單
- 設定：ZeroType 有設定畫面；danwen 用頂列選單切換常用項目，其餘改 YAML

刻意不做：語音指令（讓 LLM 執行系統動作有 prompt injection 風險）

還沒做的（依實用程度排序，括號為工作量）：
1. ✅ 依目前的程式調整（2026-10-10）：
   - extension 在 `io.github.danwen.ShellOverlay` 下匯出 `/io/github/danwen/Shell`（`io.github.danwen.Shell1.FocusedApp`
     → 程式代號、視窗類別），不含視窗標題；danwen 只在開始整理錄音與貼上時查詢（逾時 0.25 秒，沒有 extension 就用預設）
   - 規則檔 `~/.config/danwen/apps.yaml`（沒有時用內建，init-config 會複製；存檔即生效；格式錯誤時當作沒有規則）：
     match 比對程式代號或視窗類別（不分大小寫），第一條符合的規則生效；paste（ctrl+v／ctrl+shift+v／shift+insert／none）、
     mode（小紙條）。內建只有終端機 Ctrl+Shift+V（使用者用網頁版收信、不用 Slack），郵件程式→Email 留作註解範例
   - 小紙條在錄音開始時決定，只在目前是預設小紙條（refine.mode）時才依程式換，使用者手動選的優先；
     D-Bus 屬性 RecordingMode、RecordingModeByApp 讓錄音提示顯示「整理：Email（依目前的程式）」
   - 貼上的按鍵在貼上當下決定（整理期間可能換了視窗）；虛擬鍵盤依序按下、反序放開，跟手按一樣：
     fcitx5 的 Control+Shift_L／R 切換輸入法只在中間沒按其他鍵時觸發，所以 Ctrl+Shift+V 不會切換
   - 不做模擬打字：uinput 只能送按鍵代碼，按鍵會經過注音輸入法；GNOME 沒有提供 virtual-keyboard 協定，
     遠端桌面 portal 送 keysym 也不保證打得出中文。貼不進去的程式改用 paste: none（只放剪貼簿並通知）
   - `danwen apps [--delay N]` 列出規則與目前的程式；`danwen paste-test --keys`；設定 `output.app_rules` 可關閉
   - 目前的程式不送給 LLM（只用來選貼法與小紙條）
2. ✅ 歷史搜尋（2026-10-10）：`danwen history search 關鍵字…`（以空白分隔、每個都要有、不分大小寫，比對貼上的文字與辨識原文）；
   D-Bus `SearchHistory(query, limit)`；extension 選單的搜尋框（輸入後 0.25 秒查詢，關閉選單時清空）
   - 同時加上快捷鍵提示：extension 從 GSettings 讀 danwen- 開頭的自訂快捷鍵（先確認 schema 存在，否則 Gio.Settings 會讓
     GNOME Shell 當掉），在小紙條、子選單標題旁顯示按鍵；還沒設定時選單有「設定切換小紙條的快捷鍵」，執行 danwen shortcuts install
   - install.sh 執行 `danwen shortcuts update`：設定過才更新（新增項目、改過的指令），沒設定過只提示，不動 GNOME 設定
3. ✅ 設定畫面（2026-10-11）：見下方「設定畫面」
4. ✅ 字典自動產生規則（2026-10-11）：`danwen dict suggest [--llm] [--yes]`（見下方「字典建議」）
5. 螢幕內容當上下文（大）：截圖＋文字辨識；送雲端前要讓使用者先確認內容，隱私風險最高，最後再考慮

其他待辦：方案 D（Cloudflare）待使用者填 account_id、設定金鑰後實測，再決定整理用的模型

## 藍牙麥克風（2026-10-10）
- 問題：使用者只有藍牙喇叭（Xiaomi Desktop Speaker，有免持麥克風）沒有其他麥克風；喇叭固定在 A2DP 時，
  系統預設輸入變成喇叭播放聲音的 .monitor，danwen 錄到的是播放的聲音。WirePlumber 0.4 只替
  media-role.applications 名單裡的通話程式（Firefox、Chrome、Zoom、Telegram…）自動切到免持
- `btmic.BluetoothMic`（透過 pactl）：開始錄音前，預設輸入是麥克風就不動；是 .monitor 時先找現成的 bluez_input，
  沒有就把預設輸出所屬的藍牙裝置（其次任何有免持模式的）切到 sources ≥ 1 中優先權最高的模式（通常 mSBC），
  等 bluez_input.<位址> 出現（最多 4 秒）才開始錄；錄完 `audio.bluetooth_restore_s`（預設 5 秒）後切回原本的模式，
  期間再錄就沿用；裝置已斷線或使用者自己換過模式就不切回；danwen 結束時立刻切回
- 錄音以 PULSE_SOURCE 指定來源（ALSA 預設走 pulse 外掛，每次開串流都會讀），不改系統預設輸入
- 切換時 State=recording、Kind=preparing，extension 顯示「切換藍牙麥克風中，聽到提示音再說話」；切好才響提示音
- 沒有麥克風可用（NoMicrophone）時通知並放棄這次錄音；pactl 失敗時照舊用系統預設，不影響聽寫
- 實測 Xiaomi 喇叭：切換 0.1 秒、開頭無聲 0.04 秒、切回 0.04 秒
- 測試隔離：conftest 把 btmic._pactl 換成假的（預設回報有麥克風，其他指令一律失敗）——
  曾因測試呼叫到真正的 pactl，把使用者的喇叭切到免持模式；DBusService._bt_call 也擋掉
- 協調服務（使用者的 bt-audio-profile，另一個專案，選用）：有安裝時 danwen 透過自己的 D-Bus 連線呼叫
  `io.github.shih_ch.BtAudio1.Acquire("danwen")`／`Release(秒)`，借用者以連線計算（danwen 當掉自動歸還）；
  服務沒安裝（ServiceUnknown）或沒回應時改用內建；NoMicrophone 錯誤照常通知。
  規則：預設輸入是麥克風不切；記錄借用者，全部歸還幾秒後才切回；只切回自己切的、且沒有其他程式在用藍牙麥克風；
  手動切到免持不自動切回，借用中手動切回 A2DP 會等借用結束。內建版也會在其他程式還在錄藍牙麥克風時延後切回

## 字典建議（2026-10-11）
- 來源（suggest.py）：
  - 重講：相鄰兩筆 ≤ 90 秒、去標點後相似度 ≥ 0.6、各 ≥ 3 字；以字（中文）／單字（英文）比對，取被替換的詞，
    只換一個中文字時連同相鄰相同的字湊成詞；不知道哪次對，雙向（使用者選）
  - 整理前後：整理過的紀錄 raw 與 text 的替換；排除翻譯（postprocess: false 的小紙條）、
    口誤修正（改成的詞本來就在 raw 裡，或 raw 有「不對／應該是」）
  - LLM（選用）：最近 20 筆 raw 交給 Refiner.chat（依方案，B＝Groq）；要求 JSON [{wrong,right,reason}]，
    wrong 必須逐字出現在紀錄裡才採用（避免 LLM 編造）；送出前說明送到哪裡並詢問，非互動時要 --yes
- 共同過濾：只差標點、大小寫、數字寫法（三點／3 點）的不算；中文一字、英文一詞算一個單位，wrong ≤ 6、right ≤ 8
- 合併重複、去掉字典已有的；「不要再提」存在 ~/.local/state/danwen/dict-ignored.json
- 互動（終端機）：重講 [1]/[2] 選方向、其他 [y]；[s] 跳過、[n] 不要再提；非互動時列出 danwen dict add 指令
- 實測使用者紀錄：重講抓到「月風／嶽風」「籤島／前島」；Groq 對合成句子找出 無邦圖→Ubuntu、酷伯內提斯→Kubernetes
- Refiner 抽出 chat(messages) 給整理與字典建議共用

## 模型選擇（2026-10-11）
- cloud.MODELS：每家服務商的選單（整理 llm／辨識 asr），每個模型附實測說明與專屬參數（ModelChoice.extra）；只列實測可用的
- 選擇存在 ~/.local/state/danwen/models.json（每家分開，切方案不會帶錯）；resolve() 的優先順序：
  設定檔 cloud.llm_model／asr_model（自訂方案用）→ 選單選的 → 預設；llm_extra 依模型（不在清單的 gpt-oss 也設 low）
- 本機 Ollama：選單列出已安裝的模型（/api/tags），選了寫進 config 的 refine.model（馬上生效）
- 預設改為 Groq qwen/qwen3.8-27b（reasoning_effort none）、Cloudflare @cf/meta/llama-3.3-70b-instruct-fp8-fast
- 實測（日常 6 句 × 2 輪）：Groq Qwen3.8 12/12 0.3 秒、gpt-oss-120b 12/12 0.7 秒、gpt-oss-20b 10/12；
  Cloudflare llama-3.3-70b 12/12 0.9 秒、mistral-small 10/12（「好」→ Hello）、qwen3-30b 4/6、gpt-oss-20b 2/6；
  Groq 的 llama-3.3-70b、qwen3-32b 已下架（404）。Qwen3.8 不關思考時有一次 30 秒逾時
- Cloudflare 辨識加 vad_filter：不加時靜音＋提示詞會被「辨識」成「作詞・作曲・編曲 李宗盛」
- `danwen models [list|set|test]`；D-Bus ListModels／SetModel／TestModel；設定畫面「整理」頁的「模型」區（含測試按鈕）

## 辨識前的語音偵測（2026-10-11）
- 使用者回報：麥克風沒收到人聲時常輸出「我。」（藍牙喇叭免持麥克風的雜音高於音量門檻，SenseVoice 吐出「我。」「。」）；
  雲端 Whisper 遇到靜音也會編出「詞曲 李宗盛」「Thank you for watching!」
- speech.SpeechDetector（Silero VAD，min_speech 0.1 秒）：辨識前檢查，偵測不到說話就略過、不上傳；錄音 ≥ 1 秒時通知
  「沒有偵測到說話，沒有貼上（請檢查麥克風）」；偵測失敗時照常辨識
- 實測：全零、白雜音 -45／-30 dBFS、100 Hz 嗡嗡聲都判為 0 秒；10 秒說話判為 6.2 秒；每次 2～35 毫秒
- 設定 audio.speech_check（預設開，設定畫面「錄音」頁可關），說話很小聲常被略過時關閉

## 設定畫面（2026-10-11）
- extension 的 prefs.js（Adw，GNOME 46／libadwaita 1.5），六頁：一般、錄音、整理、字典、快捷鍵、紀錄；
  頂列選單加「設定…」（Extension.openPreferences）
- 與 extension 共用 common.js（D-Bus 介面定義、讀取 danwen- 快捷鍵）；設定畫面在另一個程式執行，不能用 Shell 模組
- 透過 D-Bus：GetSettings（values、熱鍵選項、要重新啟動的項目、哪些服務商有金鑰、LLM 送到哪裡）、
  SetSetting(key, JSON) → 是否要重新啟動、GetDictionary／SetDictionaryEntry（right 空白＝刪除）、
  DictSuggestions(use_llm)、IgnoreSuggestion；方案、小紙條、翻譯語言沿用 SetPlan／SetMode／SetLanguage
- settings.py：可改的項目清單（SETTINGS：型別、能不能馬上生效）；write() 只改 config.yaml 那一行（保留行尾註解，
  沒有就加在區段最後或檔尾），寫完重新載入檢查，有錯還原
- daemon：馬上生效的（提示音、藍牙麥克風、依程式調整；refine.* 與 long_recording.refine 每次使用時才讀）直接套用；
  其他與啟動時的值比較，不同才要求重新啟動（改回原值就不用）
- API 金鑰只顯示有沒有設定；輸入仍在終端機（danwen key set），不經過設定畫面
- 測試：settings 的寫入與還原、daemon 套用、D-Bus 方法；headless 檢查用 DANWEN_PREFS_PAGE 開指定頁面截圖
  （測試環境要用 UpdateActivationEnvironment 把 WAYLAND_DISPLAY 交給 D-Bus，設定畫面才開得出視窗）
- 測試隔離補上 credentials.KeyringStore（讀設定時會查哪些服務商有金鑰）

## 錄音提示的音量條（2026-10-11）
- Recorder.level()：最近 0.1 秒的 dBFS（錄音執行緒同時 append，只讀最後幾段）
- 錄音期間（有 D-Bus 時）每 0.1 秒送 D-Bus 訊號 `Level(d 音量 0～1, b 是否大於 audio.silence_dbfs)`；
  -60 dBFS 以下是 0、-10 dBFS 以上是 1；回報執行緒出錯只記錄，不影響錄音
- extension：錄音提示在紅點後加音量條（48px，有聲音綠色、太小聲灰色）；錄了 3 秒都沒聽到聲音時加上
  「沒有收到聲音，請檢查麥克風」；用訊號而非屬性，避免每 0.1 秒觸發整個選單重算

## 之後再考慮
- fcitx5 addon 直接送字：不經剪貼簿、終端機可用，並可用 preedit 做串流即時顯示
