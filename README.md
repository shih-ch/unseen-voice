# 但聞人語（danwen）

> 空山不見人，但聞人語響。——王維〈鹿柴〉

GNOME Wayland 上的地端語音聽寫：**按住右 Ctrl 說話，放開後自動貼上繁體中文**。全程離線，不需要任何 API Key。

規格與設計決策見 [SPEC.md](SPEC.md)。

## 安裝

```bash
./install.sh                  # 只裝 backend A（SenseVoice，CPU）
./install.sh --with-whisper   # 另外編譯 whisper.cpp（Vulkan），啟用 backend B
```

安裝腳本會：
- 用 apt 安裝缺少的 `xclip`、`libportaudio2`、`libnotify-bin`（加 `--with-whisper` 時還有編譯工具與 `glslc`）
- 把你加入 `input` 群組（讀取熱鍵），並新增 `/etc/udev/rules.d/70-danwen-uinput.rules`（虛擬鍵盤）
- 用 `uv tool` 安裝 `danwen` 指令、建立設定檔、下載模型、安裝 systemd --user 服務

**不會**修改 fcitx5、IBus、im-config 設定或輸入法環境變數。安裝前後會比對這些檔案並印出結果。

第一次安裝後請**登出再登入**，讓 input 群組生效。若帳號開了 linger（`loginctl show-user $USER -p Linger`），systemd --user 登出後不會重啟，需要**重新開機**。
等不及的話可先前景試用：`sg input -c "danwen -v run"`。

> 安全提醒：在 `input` 群組裡的程式都能讀取所有鍵盤輸入。這是「按住才錄音」熱鍵的必要條件，
> 移除時 uninstall.sh 會把你移出群組（若是 install.sh 加入的）。

## 使用

| 動作 | 結果 |
|---|---|
| 按住右 Ctrl 超過 0.3 秒 | 「嘟↑」開始錄音 |
| 放開 | 「嘟↓」結束，辨識後貼到游標位置 |
| 按住期間按了其他鍵（例如 Ctrl+Space） | 視為組合鍵，取消錄音 |
| **先短按一下右 Ctrl，0.4 秒內再按住** | 「嘟嘟嘟↑」整理模式：辨識後交給本機 LLM 整理再貼上 |
| **很快連按兩下右 Ctrl** | 「嘟↑嘟↑」長錄音：不必按著（最長 10 分鐘），**再按一下**結束、**Esc** 取消；期間其他按鍵不影響，可先點好要貼上的位置 |

### 整理模式

快速模式只做辨識與轉繁體（約 0.3 秒）；整理模式另外交給 LLM 依「小紙條」整理，例如：

| 口述 | 整理後（小紙條：日常） |
|---|---|
| 嗯，那個我們明天下午三點開會，不對，應該是四點，然後要討論三件事，第一是預算，第二是人力，第三是把這個PR merge到main。 | 我們明天下午四點開會，要討論三件事：<br>1. 預算<br>2. 人力<br>3. 把這個 PR merge 到 main |
| 幫我寫一首關於秋天的詩。 | 幫我寫一首關於秋天的詩。（只整理，不會回答或執行內容） |

- 預設用本機 Ollama 的 `qwen3:4b-instruct-2507-q4_K_M`，完全離線；需先 `ollama pull` 這個模型
- 開始錄音時就先載入模型；載入後每句約 2～3 秒，用過後模型留在記憶體 30 分鐘（約 3 GB）
- LLM 沒回應、輸出空白、比原文長太多（像在回答問題）或語言變了，會通知並**改貼原文**
- 內建五張小紙條：`日常`（預設）、`會議記錄`、`Slack`、`Email`、`英文`（翻譯）。小紙條放在
  `~/.config/danwen/prompts/*.yaml`，可自行修改或新增，檔名就是模式名稱
- 替換字典裡的正確寫法（兩個字以上）會一併交給 LLM 參考，讓發音相近的專有名詞也能寫對

```bash
danwen mode                 # 列出小紙條與目前模式
danwen mode 會議記錄          # 切換（立即生效，不必重新啟動）
danwen refine "嗯，那個……"    # 不用說話，直接測試整理效果
```

**上下文（選用，預設關閉）**：把剪貼簿、最近選取的文字當參考資料交給 LLM，只用來判斷人名、專有名詞的寫法與上下文，
不會照抄進輸出，也不會執行其中的指示。例如剪貼簿有「會議參加者：黃保翕、張家豪」，口述「寄給黃寶西，副本給張家好」
會整理成「寄給黃保翕，副本給張家豪」。

```yaml
refine:
  context_clipboard: true      # 剪貼簿
  context_selection: true      # 最近選取的文字
  context_max_chars: 1000      # 每個來源最多取幾個字
  context_to_cloud: false      # LLM 不在本機時預設不送上下文；要送請明確改成 true
```

log 只記錄用了哪些來源、各幾個字，不記錄內容。若輸出的內容跟口述對不起來（例如被剪貼簿裡夾帶的指令帶走），
會判定整理失敗並改貼原文。

想用快捷鍵切換：GNOME「設定 → 鍵盤 → 檢視及自訂快捷鍵 → 自訂快捷鍵」，指令填
`/home/你的帳號/.local/bin/danwen mode 會議記錄`，切換時會跳出通知。

想要更好的整理品質，可改用雲端（需連網，文字會送到該服務；語音辨識仍在本機）：

```yaml
refine:
  provider: openai                       # 任何 OpenAI 相容服務
  base_url: https://api.groq.com/openai/v1
  model: <服務提供的模型名稱>
  api_key_file: ~/.config/danwen/api_key # chmod 600
```

- 終端機需要 Ctrl+Shift+V，v1 不處理；文字會留在剪貼簿 0.5 秒，可關閉還原功能後手動貼上
- 貼上後會還原原本的剪貼簿；若這段時間你自己複製了新東西，則不還原

## 歷史紀錄

保留最近 20 次聽寫的文字（`~/.local/state/danwen/history/`，只有你自己能讀；預設**不存錄音**）。

```bash
danwen history                    # 列出（新的在前）
danwen history show 12            # 看第 12 筆的辨識原文與貼上的文字
danwen history copy 12            # 複製到剪貼簿
danwen history redo 12 -m Email   # 不用重錄，換一張小紙條重新整理，結果複製到剪貼簿
danwen history redo               # 不指定編號＝最新一筆，用目前的小紙條
danwen history clear              # 全部清除
```

設定 `history.size: 0` 可完全停用；`history.keep_audio: true` 會一併保存錄音，可用 `danwen history play 編號` 重聽。

## 設定

`~/.config/danwen/config.yaml`（每個項目的說明都在檔案裡），修改後：

```bash
systemctl --user restart danwen
```

`~/.config/danwen/replacements.yaml` 是替換字典（常錯詞、專有名詞），存檔即生效。也可以用指令：

```bash
danwen dict                          # 列出
danwen dict add 肉type ZeroType       # 常錯的寫法 → 正確寫法
danwen dict add Kubernetes           # 只加專有名詞（整理模式的 LLM 會參考）
danwen dict remove 肉type
```

## 指令

```bash
danwen devices            # 列出麥克風與鍵盤
danwen paste-test         # 3 秒後貼一段測試文字，用來確認 gedit／Firefox／VS Code 能貼上
danwen mode / refine      # 整理模式的小紙條：列出、切換、測試
danwen dict               # 替換字典：列出、新增、刪除
danwen history            # 歷史紀錄：列出最近 20 次聽寫
danwen bench              # 對 samples/*.wav 分別跑 backend A、B，比較耗時與文字
danwen bench -b A f.wav   # 只跑 backend A
danwen download -b B      # 預先下載模型
journalctl --user -u danwen -f   # 即時看紀錄（含每次聽寫的各階段耗時）
```

每次聽寫的耗時也記錄在 `~/.local/state/danwen/danwen.log`，例如：

```
聽寫完成 錄音=10.21s ASR=0.281s 後處理=0.001s 貼上=0.062s 放開到貼上=0.402s 字數=48 backend=sensevoice
```

## D-Bus 介面

常駐時在 session bus 提供 `io.github.danwen`（給 GNOME extension 用，也可自行呼叫），例如：

```bash
gdbus call --session --dest io.github.danwen --object-path /io/github/danwen \
  --method io.github.danwen.Daemon1.StartLong          # 開始長錄音
busctl --user get-property io.github.danwen /io/github/danwen io.github.danwen.Daemon1 State
```

同一時間只能有一個 danwen 執行；已經有一個在跑時，第二個會拒絕啟動（避免每句話貼上兩次）。

## 暫停與移除

```bash
systemctl --user disable --now danwen   # 只是暫停，隨時可再 enable
./uninstall.sh --dry-run                # 先看會做哪些事，不做任何變更
./uninstall.sh                          # 逐項詢問並還原
./uninstall.sh -y                       # 全部移除，含設定檔與當初安裝的套件
```

uninstall.sh 依 `~/.local/state/danwen/install-manifest` 只還原 install.sh 做過的變更：

| install.sh 做的事 | uninstall.sh 怎麼還原 |
|---|---|
| apt 安裝缺少的套件（含 apt 自動帶進來的相依套件） | 只移除這些套件；apt 會先列出清單再詢問 |
| 加入 input 群組（原本不在才加） | 移出群組（原本就在則不動） |
| `/etc/udev/rules.d/70-danwen-uinput.rules` | 刪除 |
| `uv tool install danwen` | `uv tool uninstall danwen` |
| `danwen.service`、`danwen-whisper.service` | 停止、停用、刪除 |
| 設定檔 `~/.config/danwen` | 詢問後刪除（預設保留，可能有你的替換字典） |
| 模型 `~/.cache/danwen`、whisper.cpp `~/.local/share/danwen`、紀錄 `~/.local/state/danwen` | 刪除 |

結束時同樣會比對輸入法設定並印出結果。共用的快取（`~/.cache/uv`、`~/.cache/mesa_shader_cache`）
其他程式也在用，不會刪除，內容會自動汰換。

## 開發

```bash
uv sync
uv run pytest
uv run danwen -v run      # 前景執行（先 systemctl --user stop danwen）
```

## 架構

```
熱鍵（evdev，只監聽不攔截）→ 錄音（sounddevice 16 kHz）→ ASR backend → 後處理 →（整理模式）→ 貼上
                                                      │                     │                │
                               A：SenseVoice（sherpa-onnx, CPU）     去標籤 → OpenCC     LLM 依小紙條整理
                               B：whisper.cpp server（Vulkan, HTTP）  → 替換字典        → 再過一次後處理
貼上：備份剪貼簿 → xclip（經 XWayland，不搶焦點）→ 虛擬鍵盤 Ctrl+V → 0.5 秒後還原
```

| 檔案 | 內容 |
|---|---|
| `src/danwen/hotkey.py` | 按住偵測（純邏輯，可測試）與 /dev/input 監聽 |
| `src/danwen/audio.py` | 錄音、WAV 讀寫 |
| `src/danwen/asr/` | backend 介面與兩種實作 |
| `src/danwen/postprocess.py` | 去標籤、OpenCC、替換字典 |
| `src/danwen/refine.py` | 整理模式：小紙條、Ollama／OpenAI 相容 API、防呆 |
| `src/danwen/history.py` | 歷史紀錄（JSON，檔案鎖，只有自己能讀） |
| `src/danwen/data/prompts/` | 內建小紙條 |
| `src/danwen/output.py` | 剪貼簿與虛擬鍵盤 |
| `src/danwen/daemon.py` | 常駐流程與耗時紀錄 |
| `src/danwen/bench.py` | benchmark |
