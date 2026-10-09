// 但聞人語（danwen）GNOME Shell extension：頂列狀態圖示與選單，以及錄音時畫面上方的浮動提示。
//
// 透過 session D-Bus（io.github.danwen）與 danwen 常駐程式溝通，本身不錄音也不辨識。
// danwen 沒在跑時圖示變灰，選單可以啟動它。

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const APP_NAME = '但聞人語';
const BUS_NAME = 'io.github.danwen';
// 持有這個名稱表示畫面上會顯示錄音提示，danwen 就不再另外跳系統通知
const OVERLAY_BUS_NAME = 'io.github.danwen.ShellOverlay';
const OBJECT_PATH = '/io/github/danwen';
const IFACE_XML = `
<node>
  <interface name="io.github.danwen.Daemon1">
    <property name="State" type="s" access="read"/>
    <property name="Kind" type="s" access="read"/>
    <property name="Mode" type="s" access="read"/>
    <property name="ModeLabel" type="s" access="read"/>
    <property name="Language" type="s" access="read"/>
    <property name="Hotkey" type="s" access="read"/>
    <property name="Plan" type="s" access="read"/>
    <property name="PlanTitle" type="s" access="read"/>
    <property name="Cloud" type="b" access="read"/>
    <property name="CloudAsr" type="b" access="read"/>
    <property name="CloudRefine" type="b" access="read"/>
    <property name="CloudProvider" type="s" access="read"/>
    <method name="ListPlans"><arg type="a(ss)" direction="out"/></method>
    <method name="SetPlan"><arg type="s" name="name" direction="in"/></method>
    <method name="ListModes"><arg type="a(ss)" direction="out"/></method>
    <method name="SetMode"><arg type="s" name="name" direction="in"/></method>
    <method name="CycleMode">
      <arg type="i" name="step" direction="in"/><arg type="s" direction="out"/>
    </method>
    <method name="ListLanguages"><arg type="as" direction="out"/></method>
    <method name="SetLanguage"><arg type="s" name="language" direction="in"/></method>
    <method name="GetHistory">
      <arg type="i" name="limit" direction="in"/><arg type="s" direction="out"/>
    </method>
    <method name="CopyHistory">
      <arg type="i" name="entry_id" direction="in"/><arg type="s" direction="out"/>
    </method>
    <method name="Redo">
      <arg type="i" name="entry_id" direction="in"/><arg type="s" name="mode" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="StartLong"/>
    <method name="Stop"/>
    <method name="Cancel"/>
    <signal name="HistoryChanged"/>
  </interface>
</node>`;
const DanwenProxy = Gio.DBusProxy.makeProxyWrapper(IFACE_XML);

const STATE_LABELS = {idle: '待命中', recording: '錄音中', processing: '辨識、整理中…'};
const KIND_LABELS = {fast: '快速', refine: '整理', long: '長錄音'};
const ICONS = {
    offline: 'microphone-disabled-symbolic',
    idle: 'audio-input-microphone-symbolic',
    recording: 'audio-input-microphone-symbolic',
    processing: 'content-loading-symbolic',
};
const KEY_NAMES = {
    KEY_RIGHTCTRL: '右 Ctrl', KEY_LEFTCTRL: '左 Ctrl', KEY_RIGHTALT: '右 Alt', KEY_LEFTALT: '左 Alt',
    KEY_RIGHTSHIFT: '右 Shift', KEY_LEFTSHIFT: '左 Shift', KEY_RIGHTMETA: '右 Super', KEY_LEFTMETA: '左 Super',
    KEY_CAPSLOCK: 'Caps Lock', KEY_SCROLLLOCK: 'Scroll Lock', KEY_PAUSE: 'Pause', KEY_COMPOSE: 'Menu',
};
const HISTORY_ITEMS = 5;
// 可以另外選目標語言的小紙條（與 danwen 的 refine.TRANSLATE 相同）
const TRANSLATE = '翻譯';

// 把 evdev 按鍵名稱轉成好讀的名字（KEY_F9 → F9）
function keyName(evdevName) {
    if (!evdevName)
        return '熱鍵';
    return KEY_NAMES[evdevName] ?? evdevName.replace(/^KEY_/, '');
}
const PREVIEW_CHARS = 28;

function preview(text) {
    const flat = text.replace(/\s*\n\s*/g, ' ⏎ ');
    return flat.length > PREVIEW_CHARS ? `${flat.slice(0, PREVIEW_CHARS)}…` : flat;
}

function errorMessage(error) {
    if (error instanceof GLib.Error)
        Gio.DBusError.strip_remote_error(error);
    return error.message;
}

function formatElapsed(microseconds) {
    const total = Math.floor(microseconds / 1_000_000);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

// 錄音與辨識時顯示在畫面上方中央的小提示；待命時淡出隱藏。不接收滑鼠，不影響底下的視窗。
const DanwenOsd = GObject.registerClass(
class DanwenOsd extends St.BoxLayout {
    _init() {
        super._init({style_class: 'danwen-osd', reactive: false, visible: false, opacity: 0});
        this._dot = new St.Label({style_class: 'danwen-osd-dot', y_align: Clutter.ActorAlign.CENTER});
        this._label = new St.Label({style_class: 'danwen-osd-label', y_align: Clutter.ActorAlign.CENTER});
        this.add_child(this._dot);
        this.add_child(this._label);
        this._state = 'idle';
        this._since = 0;
        this._timerId = 0;
        Main.layoutManager.uiGroup.add_child(this);
    }

    update(state, kind, mode, hotkey, cloud) {
        this._hotkey = hotkey;
        this._cloud = cloud;
        if (state === 'recording' && this._state !== 'recording')
            this._since = GLib.get_monotonic_time();
        this._state = state;
        this._kind = kind;
        this._mode = mode;
        if (state === 'recording') {
            if (!this._timerId) {
                this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                    this._render();
                    return GLib.SOURCE_CONTINUE;
                });
            }
        } else {
            this._stopTimer();
        }
        if (state === 'recording' || state === 'processing') {
            this._render();
            this._show();
        } else {
            this._hide();
        }
    }

    _render() {
        const recording = this._state === 'recording';
        this._dot.text = recording ? '●' : '…';
        if (recording)
            this._dot.remove_style_class_name('processing');
        else
            this._dot.add_style_class_name('processing');

        const elapsed = formatElapsed(GLib.get_monotonic_time() - this._since);
        const where = this._cloud ? '☁ ' : '';
        if (!recording)
            this._label.text = where + (this._kind === 'refine' ? `整理中（${this._mode}）` : '辨識中');
        else if (this._kind === 'long')
            this._label.text = `${where}長錄音 ${elapsed}　·　再按一下 ${keyName(this._hotkey)} 結束，Esc 取消`;
        else if (this._kind === 'refine')
            this._label.text = `${where}錄音中 ${elapsed}　·　整理：${this._mode}`;
        else
            this._label.text = `${where}錄音中 ${elapsed}`;
        this._position();
    }

    // 放在目前工作中的螢幕上方中央；頂列在上方時排在它下面
    _position() {
        const monitor = Main.layoutManager.currentMonitor ?? Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const panelBox = Main.layoutManager.panelBox;
        const panelOnTop = panelBox.visible && Math.abs(panelBox.y - monitor.y) < 2 &&
            panelBox.x >= monitor.x && panelBox.x < monitor.x + monitor.width;
        const [, width] = this.get_preferred_width(-1);
        this.set_position(
            Math.floor(monitor.x + (monitor.width - width) / 2),
            monitor.y + (panelOnTop ? panelBox.height : 0) + 12);
    }

    _show() {
        if (this.visible && this.opacity === 255)
            return;
        this.remove_all_transitions();
        this.visible = true;
        this.ease({opacity: 255, duration: 150, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    _hide() {
        if (!this.visible)
            return;
        this.remove_all_transitions();
        this.ease({
            opacity: 0,
            duration: 200,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                this.visible = false;
            },
        });
    }

    _stopTimer() {
        if (this._timerId) {
            GLib.Source.remove(this._timerId);
            this._timerId = 0;
        }
    }

    destroy() {
        this._stopTimer();
        super.destroy();
    }
});

const DanwenIndicator = GObject.registerClass(
class DanwenIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, APP_NAME);
        this._icon = new St.Icon({style_class: 'system-status-icon'});
        this.add_child(this._icon);

        this._proxy = null;
        this._proxyIds = [];
        this._historySignalId = 0;
        this._generation = 0;
        this._modeItems = new Map();
        this._scrollDelta = 0;
        this._lastCycle = 0;
        this._cancellable = new Gio.Cancellable();
        this._osd = new DanwenOsd();
        this._overlayNameId = Gio.bus_own_name(Gio.BusType.SESSION, OVERLAY_BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null, null);

        // GNOME 不會打開空的選單，所以骨架一開始就建好；清單在打開時才向 danwen 讀取
        this._buildMenu();
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._refreshLists();
        });
        // 滑鼠停在圖示上滾動滾輪：切換小紙條
        this.connect('scroll-event', (_actor, event) => this._onScroll(event));

        new DanwenProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, (proxy, error) => {
            if (error) {
                if (!error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                    logError(error, `${APP_NAME}：無法建立 D-Bus 連線`);
                return;
            }
            this._proxy = proxy;
            this._proxyIds = [
                proxy.connect('notify::g-name-owner', () => {
                    this._sync();
                    if (this.menu.isOpen)
                        this._refreshLists();
                }),
                proxy.connect('g-properties-changed', () => this._sync()),
            ];
            this._historySignalId = proxy.connectSignal('HistoryChanged', () => {
                if (this.menu.isOpen)
                    this._refreshLists();
            });
            this._sync();
        }, this._cancellable, Gio.DBusProxyFlags.DO_NOT_AUTO_START);

        this._sync();
    }

    get _running() {
        return Boolean(this._proxy?.g_name_owner);
    }

    get _state() {
        return this._running ? this._proxy.State ?? 'idle' : 'offline';
    }

    _buildMenu() {
        this._statusItem = new PopupMenu.PopupMenuItem('', {reactive: false, can_focus: false});
        this._statusItem.label.add_style_class_name('danwen-status');
        this.menu.addMenuItem(this._statusItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._actions = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._actions);

        // 方案：哪些部分走雲端（切換失敗時維持原方案並說明原因，例如還沒設定金鑰）
        this._planMenu = new PopupMenu.PopupSubMenuMenuItem('方案');
        this._planItems = new Map();
        this.menu.addMenuItem(this._planMenu);

        this._modeMenu = new PopupMenu.PopupSubMenuMenuItem('小紙條');
        this.menu.addMenuItem(this._modeMenu);

        // 翻譯的目標語言（選了就一併切到「翻譯」）；danwen 沒有提供語言時隱藏
        this._languageMenu = new PopupMenu.PopupSubMenuMenuItem('翻譯成');
        this._languageItems = new Map();
        this.menu.addMenuItem(this._languageMenu);

        this._historyHeader = new PopupMenu.PopupSeparatorMenuItem('最近的聽寫（點一下複製）');
        this.menu.addMenuItem(this._historyHeader);
        this._history = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._history);
        this._redoMenu = new PopupMenu.PopupSubMenuMenuItem('換小紙條重新整理最新一筆');
        this.menu.addMenuItem(this._redoMenu);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const dir = GLib.build_filenamev([GLib.get_user_config_dir(), 'danwen']);
        this._addItem(this.menu, '開啟設定檔', () => this._open(GLib.build_filenamev([dir, 'config.yaml'])));
        this._addItem(this.menu, '開啟替換字典', () => this._open(GLib.build_filenamev([dir, 'replacements.yaml'])));
        this._addItem(this.menu, '開啟小紙條資料夾', () => this._open(GLib.build_filenamev([dir, 'prompts'])));
    }

    // 依目前狀態更新圖示、狀態列與長錄音按鈕（只用快取的屬性，不呼叫 D-Bus）
    _sync() {
        if (!this._icon)
            return;
        const state = this._state;
        this._icon.icon_name = ICONS[state] ?? ICONS.idle;
        for (const name of ['offline', 'recording', 'processing']) {
            if (name === state)
                this._icon.add_style_class_name(`danwen-${name}`);
            else
                this._icon.remove_style_class_name(`danwen-${name}`);
        }

        const running = this._running;
        if (running) {
            // 只有這次錄音真的會送資料出去時才標 ☁：雲端辨識，或整理模式且整理走雲端
            const {Kind: kind, CloudAsr: asr, CloudRefine: refine} = this._proxy;
            this._osd.update(this._proxy.State, kind, this._proxy.ModeLabel || this._proxy.Mode, this._proxy.Hotkey,
                Boolean(asr || (kind === 'refine' && refine)));
        } else {
            this._osd.update('offline', '', '', '', false);
        }
        for (const part of [this._modeMenu, this._historyHeader, this._history.actor, this._planMenu])
            part.visible = running;
        this._languageMenu.visible = running && this._languageItems.size > 0;
        if (!running)
            this._redoMenu.visible = false; // 有紀錄時由 _refreshLists 打開
        this._actions.removeAll();
        if (!running) {
            this._statusItem.label.text = 'danwen 未執行';
            this._addItem(this._actions, '啟動 danwen',
                () => this._spawn(['systemctl', '--user', 'start', 'danwen.service']));
            return;
        }

        const {State: current, Kind: kind, Mode: mode, Plan: plan, PlanTitle: planTitle, Cloud: cloud} = this._proxy;
        const {ModeLabel: modeLabel, Language: language} = this._proxy;
        const status = current === 'recording'
            ? `${STATE_LABELS.recording}（${KIND_LABELS[kind] ?? kind}）`
            : STATE_LABELS[current] ?? current;
        const planShort = `${cloud ? '☁ ' : ''}方案 ${(planTitle ?? '').split(' ')[0]}`;
        this._statusItem.label.text = `${status}　·　小紙條：${modeLabel || mode}　·　${planShort}`;
        this._planMenu.label.text = `方案：${planTitle ?? ''}`;
        for (const [name, item] of this._planItems)
            item.setOrnament(name === plan ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
        this._modeMenu.label.text = `小紙條：${modeLabel || mode}`;
        for (const [name, item] of this._modeItems)
            item.setOrnament(name === mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
        this._languageMenu.label.text = `翻譯成：${language ?? ''}`;
        for (const [name, item] of this._languageItems)
            item.setOrnament(name === language ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);

        if (current === 'recording') {
            this._addItem(this._actions, '結束錄音', () => this._call('StopAsync'));
            this._addItem(this._actions, '取消錄音', () => this._call('CancelAsync'));
        } else {
            this._addItem(this._actions, '開始長錄音', () => this._call('StartLongAsync'))
                .setSensitive(current === 'idle');
        }
    }

    // 向 danwen 讀取小紙條與最近的聽寫（選單打開時、有新紀錄時）
    async _refreshLists() {
        if (!this._running)
            return;
        const generation = ++this._generation;
        let modes, history, plans, languages = [];
        try {
            [plans] = await this._proxy.ListPlansAsync();
            [modes] = await this._proxy.ListModesAsync();
            const [json] = await this._proxy.GetHistoryAsync(HISTORY_ITEMS);
            history = JSON.parse(json);
        } catch (e) {
            logError(e, `${APP_NAME}：讀取小紙條或歷史紀錄失敗`);
            return;
        }
        try {
            [languages] = await this._proxy.ListLanguagesAsync();
        } catch {
            // 舊版 danwen 沒有翻譯語言：不顯示「翻譯成」
        }
        // 讀取期間又觸發了一次更新，或 extension 已停用：放棄這次結果
        if (generation !== this._generation || !this._icon)
            return;

        const plan = this._proxy.Plan;
        this._planMenu.menu.removeAll();
        this._planItems.clear();
        for (const [name, title] of plans) {
            const item = this._addItem(this._planMenu.menu, title, () => this._setPlan(name, title));
            item.setOrnament(name === plan ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            this._planItems.set(name, item);
        }

        const mode = this._proxy.Mode;
        this._modeMenu.menu.removeAll();
        this._modeItems.clear();
        for (const [name, description] of modes) {
            const item = this._addItem(this._modeMenu.menu, description ? `${name}　—　${description}` : name,
                () => this._call('SetModeAsync', name));
            item.setOrnament(name === mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            this._modeItems.set(name, item);
        }

        const language = this._proxy.Language;
        this._languageMenu.menu.removeAll();
        this._languageItems.clear();
        for (const name of languages) {
            const item = this._addItem(this._languageMenu.menu, name, () => this._call('SetLanguageAsync', name));
            item.setOrnament(name === language ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            this._languageItems.set(name, item);
        }
        this._languageMenu.visible = languages.length > 0;

        this._history.removeAll();
        if (history.length === 0) {
            this._history.addMenuItem(new PopupMenu.PopupMenuItem('（還沒有紀錄）', {reactive: false}));
        }
        for (const entry of history) {
            const tag = entry.mode ? `［${entry.mode}］` : '';
            this._addItem(this._history, `${tag}${preview(entry.text)}`, async () => {
                if (await this._call('CopyHistoryAsync', entry.id) !== null)
                    Main.notify(APP_NAME, '已複製到剪貼簿');
            });
        }

        // 翻譯展開成每一種語言，方便把同一句話翻成好幾種
        this._redoMenu.menu.removeAll();
        for (const [name] of modes) {
            const choices = name === TRANSLATE && languages.length > 0
                ? languages.map(lang => `${name}（${lang}）`) : [name];
            for (const choice of choices)
                this._addItem(this._redoMenu.menu, choice, () => this._redo(choice));
        }
        this._redoMenu.visible = history.length > 0 && modes.length > 0;
    }

    async _call(method, ...args) {
        try {
            return await this._proxy[method](...args);
        } catch (e) {
            Main.notifyError(APP_NAME, errorMessage(e));
            return null;
        }
    }

    _onScroll(event) {
        if (!this._running)
            return Clutter.EVENT_PROPAGATE;
        let step = 0;
        switch (event.get_scroll_direction()) {
        case Clutter.ScrollDirection.UP:
            step = -1;
            break;
        case Clutter.ScrollDirection.DOWN:
            step = 1;
            break;
        case Clutter.ScrollDirection.SMOOTH: {
            // 觸控板的連續滾動：累積到一格才換
            const [, dy] = event.get_scroll_delta();
            this._scrollDelta += dy;
            if (Math.abs(this._scrollDelta) >= 1) {
                step = Math.sign(this._scrollDelta);
                this._scrollDelta = 0;
            }
            break;
        }
        }
        if (step)
            this._cycleMode(step);
        return Clutter.EVENT_STOP;
    }

    async _cycleMode(step) {
        // 滾輪一次常會送出好幾格，間隔太短的忽略，免得一次跳過好幾張
        const now = GLib.get_monotonic_time();
        if (now - this._lastCycle < 250_000)
            return;
        this._lastCycle = now;
        const result = await this._call('CycleModeAsync', step);
        if (result !== null) {
            Main.osdWindowManager.show(-1, Gio.ThemedIcon.new('document-edit-symbolic'),
                `小紙條：${result[0]}`, null, null);
        }
    }

    async _setPlan(name, title) {
        if (await this._call('SetPlanAsync', name) === null)
            return; // 失敗原因已由 _call 通知，維持原方案
        const {CloudAsr: asr, CloudRefine: refine} = this._proxy;
        const sent = [asr ? '錄音' : '', refine ? '要整理的文字' : ''].filter(Boolean).join('、');
        Main.notify(APP_NAME, `已切換到方案 ${title}`, sent ? `${sent}會送到雲端` : '全部在這台電腦上處理');
    }

    async _redo(mode) {
        Main.notify(APP_NAME, `用「${mode}」重新整理中…`);
        const result = await this._call('RedoAsync', 0, mode);
        if (result !== null)
            Main.notify(APP_NAME, `已用「${mode}」重新整理，結果已複製到剪貼簿`, preview(result[0]));
    }

    _addItem(menu, text, callback) {
        const item = new PopupMenu.PopupMenuItem(text);
        item.connect('activate', () => callback());
        menu.addMenuItem(item);
        return item;
    }

    _open(path) {
        if (!GLib.file_test(path, GLib.FileTest.EXISTS)) {
            Main.notifyError(APP_NAME, `還沒有 ${path}，請先在終端機執行：danwen init-config`);
            return;
        }
        try {
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path, null), null);
        } catch (e) {
            Main.notifyError(APP_NAME, `無法開啟 ${path}：${e.message}`);
        }
    }

    _spawn(argv) {
        try {
            Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
        } catch (e) {
            Main.notifyError(APP_NAME, e.message);
        }
    }

    destroy() {
        this._cancellable.cancel();
        Gio.bus_unown_name(this._overlayNameId);
        this._osd.destroy();
        this._osd = null;
        if (this._proxy) {
            for (const id of this._proxyIds)
                this._proxy.disconnect(id);
            if (this._historySignalId)
                this._proxy.disconnectSignal(this._historySignalId);
            this._proxy = null;
        }
        this._icon = null;
        super.destroy();
    }
});

export default class DanwenExtension extends Extension {
    enable() {
        this._indicator = new DanwenIndicator();
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
