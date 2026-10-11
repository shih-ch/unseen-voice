// 但聞人語（danwen）的設定畫面：在「擴充功能」App 點齒輪，或頂列選單的「設定…」打開。
//
// 透過 D-Bus 跟 danwen 常駐程式溝通：設定寫進 ~/.config/danwen/config.yaml（只改那一行，保留註解），
// 能馬上生效的立刻生效，其他的會提示重新啟動 danwen。API 金鑰只顯示有沒有設定，不在這裡輸入。

import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {BUS_NAME, DanwenProxy, OBJECT_PATH, danwenShortcuts} from './common.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');

const DANWEN = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'danwen']);
const SOURCES = {重講: '重講時不一樣', 整理: '整理時改的', LLM: 'LLM 認為聽錯'};

function errorText(e) {
    if (e instanceof GLib.Error)
        Gio.DBusError.strip_remote_error(e);
    return e.message;
}

function button(label, onClick, classes = ['flat']) {
    const b = new Gtk.Button({label, valign: Gtk.Align.CENTER, css_classes: classes});
    b.connect('clicked', onClick);
    return b;
}

export default class DanwenPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        window.set_default_size(900, 780);
        new SettingsWindow(window).build();
    }
}

class SettingsWindow {
    constructor(window) {
        this.window = window;
        // 沒有 callback＝同步建立；參數依序是 bus、名稱、物件路徑、callback、cancellable、flags
        this.proxy = new DanwenProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, null, null,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START);
    }

    build() {
        if (!this.proxy.g_name_owner) {
            this._notRunning();
            return;
        }
        try {
            [this.info] = this.proxy.GetSettingsSync().map(json => JSON.parse(json));
        } catch (e) {
            // 舊版 danwen 沒有設定功能
            this._notRunning(`讀不到設定：${errorText(e)}（請重新執行 install.sh 更新 danwen）`);
            return;
        }
        this.values = this.info.values;
        for (const page of [this._general(), this._recording(), this._refine(), this._dictionary(),
            this._shortcuts(), this._history()])
            this.window.add(page);
        // 開發用：dev/headless-check.sh 截圖時直接打開指定的頁面
        const page = GLib.getenv('DANWEN_PREFS_PAGE');
        if (page)
            this.window.set_visible_page_name(page);
    }

    _notRunning(detail = 'danwen 的設定要在它執行時才能修改。') {
        const page = new Adw.PreferencesPage({title: '但聞人語'});
        const group = new Adw.PreferencesGroup({title: 'danwen 沒有在執行', description: detail});
        const row = new Adw.ActionRow({title: '啟動 danwen', subtitle: '啟動後關掉這個視窗再打開一次'});
        row.add_suffix(button('啟動', () => this._spawn(['systemctl', '--user', 'start', 'danwen.service'],
            '已啟動 danwen，請重新打開設定'), ['suggested-action']));
        group.add(row);
        page.add(group);
        this.window.add(page);
    }

    // ---- 共用 ----

    _toast(title, buttonLabel = null, onClick = null) {
        const toast = new Adw.Toast({title, timeout: buttonLabel ? 8 : 3});
        if (buttonLabel) {
            toast.button_label = buttonLabel;
            toast.connect('button-clicked', onClick);
        }
        this.window.add_toast(toast);
    }

    _set(key, value) {
        try {
            const [restart] = this.proxy.SetSettingSync(key, JSON.stringify(value));
            this.values[key] = value;
            if (restart) {
                this._toast('這項設定要重新啟動 danwen 才會生效', '重新啟動',
                    () => this._spawn(['systemctl', '--user', 'restart', 'danwen.service'], '已重新啟動 danwen'));
            }
            return true;
        } catch (e) {
            this._toast(`無法儲存：${errorText(e)}`);
            return false;
        }
    }

    _switch(key, title, subtitle = '') {
        const row = new Adw.SwitchRow({title, subtitle, active: Boolean(this.values[key])});
        row.connect('notify::active', () => {
            if (row.active !== this.values[key] && !this._set(key, row.active))
                row.active = this.values[key];
        });
        return row;
    }

    _spin(key, title, subtitle, min, max, step) {
        const row = Adw.SpinRow.new_with_range(min, max, step);
        row.set({title, subtitle, value: this.values[key]});
        row.connect('notify::value', () => {
            const value = Number.isInteger(step) && step >= 1 ? Math.round(row.value) : row.value;
            if (value !== this.values[key] && !this._set(key, value))
                row.value = this.values[key];
        });
        return row;
    }

    // 下拉選單：items＝[[值, 顯示文字]]；onSelect 回傳 false 時改回原本的選項
    _combo(title, subtitle, items, current, onSelect) {
        const row = new Adw.ComboRow({title, subtitle, model: Gtk.StringList.new(items.map(([, label]) => label))});
        let selected = Math.max(items.findIndex(([value]) => value === current), 0);
        row.selected = selected;
        row.connect('notify::selected', () => {
            if (row.selected === selected)
                return;
            if (onSelect(items[row.selected][0])) {
                selected = row.selected;
            } else {
                row.selected = selected;
            }
        });
        return row;
    }

    _call(method, ...args) {
        try {
            return this.proxy[`${method}Sync`](...args);
        } catch (e) {
            this._toast(errorText(e));
            return null;
        }
    }

    _open(path) {
        try {
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path, null), null);
        } catch (e) {
            this._toast(`無法開啟 ${path}：${e.message}`);
        }
    }

    async _spawn(argv, done) {
        try {
            const proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE);
            const [output] = await proc.communicate_utf8_async(null, null);
            this._toast(proc.get_successful() ? done : output.trim().split('\n').pop());
            return proc.get_successful();
        } catch (e) {
            this._toast(e.message);
            return false;
        }
    }

    // ---- 一般 ----

    _general() {
        const page = new Adw.PreferencesPage({title: '一般', icon_name: 'preferences-system-symbolic', name: 'general'});

        const refine = new Adw.PreferencesGroup({
            title: '整理模式',
            description: '先短按一下熱鍵再按住說話：交給 LLM 依小紙條整理。方案決定哪些部分走雲端。',
        });
        const [plans] = this._call('ListPlans') ?? [[]];
        refine.add(this._combo('方案', '走雲端的錄音或文字會送出電腦', plans, this.proxy.Plan,
            name => this._call('SetPlan', name) !== null));
        const [modes] = this._call('ListModes') ?? [[]];
        refine.add(this._combo('小紙條', '整理時用的提示詞', modes.map(([name]) => [name, name]), this.proxy.Mode,
            name => this._call('SetMode', name) !== null));
        const [languages] = this._call('ListLanguages') ?? [[]];
        if (languages.length > 0) {
            refine.add(this._combo('翻譯成', '用「翻譯」小紙條時的目標語言；選了會一併切到翻譯',
                languages.map(l => [l, l]), this.proxy.Language,
                language => this._call('SetLanguage', language) !== null));
        }
        page.add(refine);

        const feedback = new Adw.PreferencesGroup({title: '提示'});
        feedback.add(this._switch('feedback.sounds', '提示音', '開始和結束錄音時的「嘟」聲'));
        feedback.add(this._switch('feedback.notify_errors', '發生錯誤時跳通知'));
        page.add(feedback);
        return page;
    }

    // ---- 錄音 ----

    _recording() {
        const page = new Adw.PreferencesPage({title: '錄音', icon_name: 'audio-input-microphone-symbolic',
            name: 'recording'});

        const hotkey = new Adw.PreferencesGroup({title: '熱鍵'});
        hotkey.add(this._combo('熱鍵', '按住說話的鍵；不要選輸入法會用到的鍵。改了要重新啟動 danwen',
            Object.entries(this.info.hotkeys), this.values['hotkey.key'],
            key => this._set('hotkey.key', key)));
        hotkey.add(this._switch('long_recording.enabled', '長錄音',
            '很快連按兩下熱鍵開始，不必按著，最長 10 分鐘。改了要重新啟動 danwen'));
        hotkey.add(this._switch('long_recording.refine', '長錄音也用整理模式', '關閉時長錄音只辨識、不整理'));
        hotkey.add(this._switch('audio.speech_check', '辨識前先確認有人說話',
            '只有雜音時略過，不會辨識出「我。」；說話很小聲常被略過時可以關閉'));
        page.add(hotkey);

        const bluetooth = new Adw.PreferencesGroup({
            title: '藍牙耳機／喇叭',
            description: '電腦沒有其他麥克風時，錄音期間把藍牙耳機或喇叭切到免持模式（有麥克風，音質較差），錄完再切回高音質。',
        });
        bluetooth.add(this._switch('audio.bluetooth_mic', '使用藍牙耳機或喇叭的麥克風'));
        bluetooth.add(this._spin('audio.bluetooth_restore_s', '錄完幾秒後切回高音質',
            '連續聽寫時不必每次重新切換；0＝立刻切回', 0, 60, 1));
        page.add(bluetooth);
        return page;
    }

    // ---- 整理 ----

    _refine() {
        const page = new Adw.PreferencesPage({title: '整理', icon_name: 'document-edit-symbolic', name: 'refine'});

        page.add(this._models());

        const cloud = new Adw.PreferencesGroup({title: '雲端'});
        cloud.add(this._switch('refine.local_fallback', '雲端不能用時改用本機 Ollama',
            '關閉時貼上原文，不喚醒本機 Ollama（省記憶體與等待時間）'));
        const keys = this.info.api_keys;
        for (const [provider, label] of Object.entries(this.info.providers)) {
            const has = keys?.includes(provider);
            cloud.add(new Adw.ActionRow({
                title: `${label}${/[A-Za-z]$/.test(label) ? ' ' : ''}的 API Key`,
                subtitle: keys === null ? '讀不到 GNOME 鑰匙圈'
                    : has ? '✓ 已設定（存在 GNOME 鑰匙圈）' : `未設定：在終端機執行 danwen key set ${provider}`,
            }));
        }
        page.add(cloud);

        const context = new Adw.PreferencesGroup({
            title: '上下文',
            description: '把剪貼簿、選取的文字當參考資料交給 LLM，只用來判斷人名與專有名詞的寫法。',
        });
        context.add(this._switch('refine.context_clipboard', '剪貼簿'));
        context.add(this._switch('refine.context_selection', '最近選取的文字'));
        context.add(this._switch('refine.context_to_cloud', '也送到雲端的 LLM',
            '關閉時只在用本機 Ollama 時才附上；開啟後剪貼簿內容會送到雲端'));
        page.add(context);

        const apps = new Adw.PreferencesGroup({title: '依目前的程式調整'});
        apps.add(this._switch('output.app_rules', '依目前的程式調整',
            '例如終端機改用 Ctrl+Shift+V 貼上；也可以讓特定程式自動換小紙條'));
        const rules = new Adw.ActionRow({title: '規則檔', subtitle: this.info.apps_path});
        rules.add_suffix(button('開啟', () => this._open(this.info.apps_path)));
        apps.add(rules);
        const prompts = new Adw.ActionRow({title: '小紙條資料夾', subtitle: '修改或新增小紙條（YAML 檔）'});
        prompts.add_suffix(button('開啟', () => this._open(
            GLib.build_filenamev([GLib.get_user_config_dir(), 'danwen', 'prompts']))));
        apps.add(prompts);
        page.add(apps);
        return page;
    }

    _models() {
        const group = new Adw.PreferencesGroup({
            title: '模型',
            description: '整理用的模型，每家分開記；只列實測可用的。按「測試」會用一句固定的句子實際整理一次。',
        });
        const [json] = this._call('ListModels') ?? ['{}'];
        for (const [provider, info] of Object.entries(JSON.parse(json))) {
            const menu = info.kinds.llm;
            if (!menu)
                continue;
            const note = id => menu.choices.find(m => m.id === id)?.note ?? '';
            const row = this._combo(`${info.label} 的整理模型`, note(menu.current),
                menu.choices.map(m => [m.id, m.label]), menu.current, id => {
                    if (this._call('SetModel', provider, 'llm', id) === null)
                        return false;
                    row.subtitle = note(id);
                    return true;
                });
            row.add_suffix(button('測試', async () => {
                this._toast(`${info.label} 測試中…`);
                try {
                    const [result] = await this.proxy.TestModelAsync(provider);
                    const {seconds, output} = JSON.parse(result);
                    this._toast(`${info.label}：${seconds} 秒｜${output.split('\n')[0]}`);
                } catch (e) {
                    this._toast(`${info.label}：${errorText(e)}`);
                }
            }));
            group.add(row);
        }
        return group;
    }

    // ---- 字典 ----

    _dictionary() {
        const page = new Adw.PreferencesPage({title: '字典', icon_name: 'accessories-dictionary-symbolic',
            name: 'dictionary'});

        this.suggestGroup = new Adw.PreferencesGroup({
            title: '建議',
            description: '從最近的聽寫找出可能聽錯的詞，按了才會加進字典。',
        });
        const llm = button('請 LLM 一起找…', () => this._askLlm());
        llm.sensitive = !this.info.llm_target.startsWith('無法使用');
        llm.tooltip_text = llm.sensitive ? `會送到${this.info.llm_target}` : this.info.llm_target;
        this.suggestGroup.set_header_suffix(llm);
        this.suggestRows = [];
        page.add(this.suggestGroup);

        this.dictGroup = new Adw.PreferencesGroup({
            title: '字典',
            description: '辨識結果裡「常錯的寫法」會換成「正確寫法」；正確寫法也會交給 LLM 參考。',
        });
        const wrong = new Adw.EntryRow({title: '常錯的寫法'});
        const right = new Adw.EntryRow({title: '正確寫法（只加專有名詞時兩格填一樣）'});
        right.add_suffix(button('加入', () => {
            if (this._call('SetDictionaryEntry', wrong.text, right.text || wrong.text) !== null) {
                wrong.text = right.text = '';
                this._refreshDictionary();
            }
        }, ['suggested-action']));
        this.dictGroup.add(wrong);
        this.dictGroup.add(right);
        this.dictRows = [];
        page.add(this.dictGroup);

        this._refreshDictionary();
        this._showSuggestions(this._call('DictSuggestions', false)?.[0] ?? '[]');
        return page;
    }

    _refreshDictionary() {
        for (const row of this.dictRows)
            this.dictGroup.remove(row);
        this.dictRows = [];
        const [json] = this._call('GetDictionary') ?? ['{}'];
        for (const [from, to] of Object.entries(JSON.parse(json))) {
            const row = new Adw.ActionRow({title: from === to ? to : `${from} → ${to}`,
                subtitle: from === to ? '專有名詞' : ''});
            const remove = new Gtk.Button({icon_name: 'user-trash-symbolic', valign: Gtk.Align.CENTER,
                css_classes: ['flat'], tooltip_text: '刪除'});
            remove.connect('clicked', () => {
                if (this._call('SetDictionaryEntry', from, '') !== null)
                    this._refreshDictionary();
            });
            row.add_suffix(remove);
            this.dictGroup.add(row);
            this.dictRows.push(row);
        }
    }

    _showSuggestions(json) {
        for (const row of this.suggestRows)
            this.suggestGroup.remove(row);
        this.suggestRows = [];
        const items = JSON.parse(json);
        if (items.length === 0) {
            const empty = new Adw.ActionRow({title: '目前沒有建議'});
            this.suggestGroup.add(empty);
            this.suggestRows.push(empty);
        }
        for (const s of items) {
            const where = s.entries.map(n => `#${n}`).join('、');
            const why = s.source === 'LLM' && s.reason ? `LLM：${s.reason}` : SOURCES[s.source] ?? s.source;
            const row = new Adw.ActionRow({
                title: s.two_way ? `${s.wrong}　↔　${s.right}` : `${s.wrong}　→　${s.right}`,
                subtitle: `${why}（${where}）${s.examples.length ? `\n${s.examples.join('\n')}` : ''}`,
            });
            const add = (from, to) => {
                if (this._call('SetDictionaryEntry', from, to) !== null) {
                    this._toast(`已加進字典：${from} → ${to}`);
                    this._removeSuggestion(row);
                    this._refreshDictionary();
                }
            };
            if (s.two_way) {
                row.add_suffix(button(`用「${s.right}」`, () => add(s.wrong, s.right)));
                row.add_suffix(button(`用「${s.wrong}」`, () => add(s.right, s.wrong)));
            } else {
                row.add_suffix(button('加入', () => add(s.wrong, s.right)));
            }
            row.add_suffix(button('不要再提', () => {
                if (this._call('IgnoreSuggestion', s.wrong, s.right, s.source) !== null)
                    this._removeSuggestion(row);
            }));
            this.suggestGroup.add(row);
            this.suggestRows.push(row);
        }
    }

    _removeSuggestion(row) {
        this.suggestGroup.remove(row);
        this.suggestRows = this.suggestRows.filter(r => r !== row);
    }

    _askLlm() {
        const dialog = new Adw.MessageDialog({
            transient_for: this.window,
            heading: '請 LLM 一起找？',
            body: `會把最近 20 筆聽寫的文字送到${this.info.llm_target}的 LLM，只用來找聽錯的詞。`,
        });
        dialog.add_response('cancel', '取消');
        dialog.add_response('send', '送出');
        dialog.set_response_appearance('send', Adw.ResponseAppearance.SUGGESTED);
        dialog.connect('response', async (_d, response) => {
            if (response !== 'send')
                return;
            this._toast('LLM 找詞中…');
            try {
                const [json] = await this.proxy.DictSuggestionsAsync(true);
                this._showSuggestions(json);
            } catch (e) {
                this._toast(errorText(e));
            }
        });
        dialog.present();
    }

    // ---- 快捷鍵 ----

    _shortcuts() {
        const page = new Adw.PreferencesPage({title: '快捷鍵',
            icon_name: 'preferences-desktop-keyboard-shortcuts-symbolic', name: 'shortcuts'});

        this.keyGroup = new Adw.PreferencesGroup({
            title: '切換小紙條的快捷鍵',
            description: 'GNOME 自訂快捷鍵；想換按鍵可在「設定 → 鍵盤 → 自訂快捷鍵」修改。',
        });
        const actions = new Gtk.Box({spacing: 6});
        actions.append(button('設定', () => this._shortcutCommand('install', '已設定快捷鍵')));
        actions.append(button('移除', () => this._shortcutCommand('remove', '已移除快捷鍵')));
        this.keyGroup.set_header_suffix(actions);
        this.keyRows = [];
        page.add(this.keyGroup);
        this._refreshShortcuts();
        return page;
    }

    // ---- 紀錄 ----

    _history() {
        const page = new Adw.PreferencesPage({title: '紀錄', icon_name: 'document-open-recent-symbolic',
            name: 'history'});
        const history = new Adw.PreferencesGroup({title: '歷史紀錄', description: '只存在這台電腦，只有你能讀。'});
        history.add(this._spin('history.size', '保留最近幾筆', '0＝不保留。改了要重新啟動 danwen', 0, 200, 1));
        history.add(this._switch('history.keep_audio', '也保存錄音',
            '可以重聽；錄音比文字更私密。改了要重新啟動 danwen'));
        const clear = new Adw.ActionRow({title: '清除全部紀錄'});
        clear.add_suffix(button('清除…', () => this._confirmClear(), ['destructive-action']));
        history.add(clear);
        page.add(history);
        return page;
    }

    _refreshShortcuts() {
        for (const row of this.keyRows)
            this.keyGroup.remove(row);
        this.keyRows = [];
        const found = danwenShortcuts();
        if (found.length === 0)
            found.push({name: '還沒設定', binding: '按右上角的「設定」，例如 Super+Alt+1～5 切換小紙條'});
        for (const s of found) {
            const row = new Adw.ActionRow({title: s.name.replace(/^但聞人語：/, ''), subtitle: s.binding});
            this.keyGroup.add(row);
            this.keyRows.push(row);
        }
    }

    async _shortcutCommand(action, done) {
        await this._spawn([DANWEN, 'shortcuts', action], done);
        this._refreshShortcuts();
    }

    _confirmClear() {
        const dialog = new Adw.MessageDialog({
            transient_for: this.window,
            heading: '清除全部歷史紀錄？',
            body: '清除後無法復原。',
        });
        dialog.add_response('cancel', '取消');
        dialog.add_response('clear', '清除');
        dialog.set_response_appearance('clear', Adw.ResponseAppearance.DESTRUCTIVE);
        dialog.connect('response', (_d, response) => {
            if (response === 'clear')
                this._spawn([DANWEN, 'history', 'clear'], '已清除歷史紀錄');
        });
        dialog.present();
    }
}
