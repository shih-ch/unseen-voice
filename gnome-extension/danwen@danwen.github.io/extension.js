// 但聞人語（danwen）GNOME Shell extension：頂列狀態圖示與選單。
//
// 透過 session D-Bus（io.github.danwen）與 danwen 常駐程式溝通，本身不錄音也不辨識。
// danwen 沒在跑時圖示變灰，選單可以啟動它。

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
const OBJECT_PATH = '/io/github/danwen';
const IFACE_XML = `
<node>
  <interface name="io.github.danwen.Daemon1">
    <property name="State" type="s" access="read"/>
    <property name="Kind" type="s" access="read"/>
    <property name="Mode" type="s" access="read"/>
    <method name="ListModes"><arg type="a(ss)" direction="out"/></method>
    <method name="SetMode"><arg type="s" name="name" direction="in"/></method>
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
const HISTORY_ITEMS = 5;
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
        this._cancellable = new Gio.Cancellable();

        // GNOME 不會打開空的選單，所以骨架一開始就建好；清單在打開時才向 danwen 讀取
        this._buildMenu();
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._refreshLists();
        });

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

        this._modeMenu = new PopupMenu.PopupSubMenuMenuItem('小紙條');
        this.menu.addMenuItem(this._modeMenu);

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
        for (const part of [this._modeMenu, this._historyHeader, this._history.actor])
            part.visible = running;
        if (!running)
            this._redoMenu.visible = false; // 有紀錄時由 _refreshLists 打開
        this._actions.removeAll();
        if (!running) {
            this._statusItem.label.text = 'danwen 未執行';
            this._addItem(this._actions, '啟動 danwen',
                () => this._spawn(['systemctl', '--user', 'start', 'danwen.service']));
            return;
        }

        const {State: current, Kind: kind, Mode: mode} = this._proxy;
        const status = current === 'recording'
            ? `${STATE_LABELS.recording}（${KIND_LABELS[kind] ?? kind}）`
            : STATE_LABELS[current] ?? current;
        this._statusItem.label.text = `${status}　·　小紙條：${mode}`;
        this._modeMenu.label.text = `小紙條：${mode}`;
        for (const [name, item] of this._modeItems)
            item.setOrnament(name === mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);

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
        let modes, history;
        try {
            [modes] = await this._proxy.ListModesAsync();
            const [json] = await this._proxy.GetHistoryAsync(HISTORY_ITEMS);
            history = JSON.parse(json);
        } catch (e) {
            logError(e, `${APP_NAME}：讀取小紙條或歷史紀錄失敗`);
            return;
        }
        // 讀取期間又觸發了一次更新，或 extension 已停用：放棄這次結果
        if (generation !== this._generation || !this._icon)
            return;

        const mode = this._proxy.Mode;
        this._modeMenu.menu.removeAll();
        this._modeItems.clear();
        for (const [name, description] of modes) {
            const item = this._addItem(this._modeMenu.menu, description ? `${name}　—　${description}` : name,
                () => this._call('SetModeAsync', name));
            item.setOrnament(name === mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            this._modeItems.set(name, item);
        }

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

        this._redoMenu.menu.removeAll();
        for (const [name] of modes)
            this._addItem(this._redoMenu.menu, name, () => this._redo(name));
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
