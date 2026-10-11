// 但聞人語：頂列選單（extension.js）與設定畫面（prefs.js）共用的 D-Bus 介面與快捷鍵讀取。
// 這裡不能用 GNOME Shell 的模組（設定畫面在另一個程式裡執行）。

import Gio from 'gi://Gio';

export const APP_NAME = '但聞人語';
export const BUS_NAME = 'io.github.danwen';
export const OBJECT_PATH = '/io/github/danwen';
export const IFACE_XML = `
<node>
  <interface name="io.github.danwen.Daemon1">
    <property name="State" type="s" access="read"/>
    <property name="Kind" type="s" access="read"/>
    <property name="Mode" type="s" access="read"/>
    <property name="ModeLabel" type="s" access="read"/>
    <property name="Language" type="s" access="read"/>
    <property name="RecordingMode" type="s" access="read"/>
    <property name="RecordingModeByApp" type="b" access="read"/>
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
    <method name="SearchHistory">
      <arg type="s" name="query" direction="in"/><arg type="i" name="limit" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="CopyHistory">
      <arg type="i" name="entry_id" direction="in"/><arg type="s" direction="out"/>
    </method>
    <method name="Redo">
      <arg type="i" name="entry_id" direction="in"/><arg type="s" name="mode" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="GetSettings"><arg type="s" direction="out"/></method>
    <method name="SetSetting">
      <arg type="s" name="key" direction="in"/><arg type="s" name="value_json" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="ListModels"><arg type="s" direction="out"/></method>
    <method name="SetModel">
      <arg type="s" name="provider" direction="in"/><arg type="s" name="kind" direction="in"/>
      <arg type="s" name="model" direction="in"/>
    </method>
    <method name="TestModel">
      <arg type="s" name="provider" direction="in"/><arg type="s" direction="out"/>
    </method>
    <method name="GetDictionary"><arg type="s" direction="out"/></method>
    <method name="SetDictionaryEntry">
      <arg type="s" name="wrong" direction="in"/><arg type="s" name="right" direction="in"/>
    </method>
    <method name="DictSuggestions">
      <arg type="b" name="use_llm" direction="in"/><arg type="s" direction="out"/>
    </method>
    <method name="IgnoreSuggestion">
      <arg type="s" name="wrong" direction="in"/><arg type="s" name="right" direction="in"/>
      <arg type="s" name="source" direction="in"/>
    </method>
    <method name="StartLong"/>
    <method name="Stop"/>
    <method name="Cancel"/>
    <signal name="HistoryChanged"/>
    <signal name="Level"><arg type="d" name="level"/><arg type="b" name="heard"/></signal>
  </interface>
</node>`;
export const DanwenProxy = Gio.DBusProxy.makeProxyWrapper(IFACE_XML);

export const MEDIA_KEYS = 'org.gnome.settings-daemon.plugins.media-keys';

// 「<Super><Alt>m」→「Super+Alt+M」
export function bindingLabel(binding) {
    const mods = [...binding.matchAll(/<(\w+)>/g)].map(m => ({Primary: 'Ctrl', Control: 'Ctrl'})[m[1]] ?? m[1]);
    const key = binding.replace(/<\w+>/g, '');
    return [...mods, key.length === 1 ? key.toUpperCase() : key].join('+');
}

// danwen shortcuts install 建立的 GNOME 快捷鍵：[{name, binding（給人看的）, key（mode:日常、translate:next）}]
export function danwenShortcuts() {
    const found = [];
    const source = Gio.SettingsSchemaSource.get_default();
    // 找不到 schema 時建立 Gio.Settings 會讓整個 GNOME Shell 當掉，先確認
    if (!source?.lookup(MEDIA_KEYS, true) || !source.lookup(`${MEDIA_KEYS}.custom-keybinding`, true))
        return found;
    for (const path of new Gio.Settings({schema_id: MEDIA_KEYS}).get_strv('custom-keybindings')) {
        if (!/\/danwen-[^/]+\/$/.test(path))
            continue;
        const settings = new Gio.Settings({schema_id: `${MEDIA_KEYS}.custom-keybinding`, path});
        const binding = settings.get_string('binding');
        // 指令最後是「mode 日常」「mode '會議記錄'」「translate next」（中文會加上引號）
        const match = settings.get_string('command').match(/\s(mode|translate)\s+(?:'([^']*)'|(\S+))\s*$/);
        if (binding && match) {
            found.push({name: settings.get_string('name'), binding: bindingLabel(binding),
                key: `${match[1]}:${match[2] ?? match[3]}`});
        }
    }
    return found;
}

// 「mode:日常」→「Super+Alt+1」
export function shortcutHints() {
    return new Map(danwenShortcuts().map(s => [s.key, s.binding]));
}
