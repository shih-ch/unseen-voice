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

- 終端機需要 Ctrl+Shift+V，v1 不處理；文字會留在剪貼簿 0.5 秒，可關閉還原功能後手動貼上
- 貼上後會還原原本的剪貼簿；若這段時間你自己複製了新東西，則不還原

## 設定

`~/.config/danwen/config.yaml`（每個項目的說明都在檔案裡），修改後：

```bash
systemctl --user restart danwen
```

`~/.config/danwen/replacements.yaml` 是替換字典（常錯詞、專有名詞），存檔即生效。

## 指令

```bash
danwen devices            # 列出麥克風與鍵盤
danwen paste-test         # 3 秒後貼一段測試文字，用來確認 gedit／Firefox／VS Code 能貼上
danwen bench              # 對 samples/*.wav 分別跑 backend A、B，比較耗時與文字
danwen bench -b A f.wav   # 只跑 backend A
danwen download -b B      # 預先下載模型
journalctl --user -u danwen -f   # 即時看紀錄（含每次聽寫的各階段耗時）
```

每次聽寫的耗時也記錄在 `~/.local/state/danwen/danwen.log`，例如：

```
聽寫完成 錄音=10.21s ASR=0.281s 後處理=0.001s 貼上=0.062s 放開到貼上=0.402s 字數=48 backend=sensevoice
```

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
熱鍵（evdev，只監聽不攔截）→ 錄音（sounddevice 16 kHz）→ ASR backend → 後處理 → 貼上
                                                      │                     │
                               A：SenseVoice（sherpa-onnx, CPU）     去標籤 → OpenCC s2twp → 替換字典
                               B：whisper.cpp server（Vulkan, HTTP）
貼上：備份剪貼簿 → xclip（經 XWayland，不搶焦點）→ 虛擬鍵盤 Ctrl+V → 0.5 秒後還原
```

| 檔案 | 內容 |
|---|---|
| `src/danwen/hotkey.py` | 按住偵測（純邏輯，可測試）與 /dev/input 監聽 |
| `src/danwen/audio.py` | 錄音、WAV 讀寫 |
| `src/danwen/asr/` | backend 介面與兩種實作 |
| `src/danwen/postprocess.py` | 去標籤、OpenCC、替換字典 |
| `src/danwen/output.py` | 剪貼簿與虛擬鍵盤 |
| `src/danwen/daemon.py` | 常駐流程與耗時紀錄 |
| `src/danwen/bench.py` | benchmark |
