// 開發用：由 dev/headless-check.sh 載入。等 danwen extension 連上假的 danwen 後，
// 打開選單、印出所有項目，再操作幾個項目並印出狀態列，最後結束 GNOME Shell。輸出的每一行以 PROBE 開頭。
// 設了 DANWEN_PROBE_SHOTS（資料夾）時改為截圖：選單、長錄音提示、滾輪切換的提示，存成 PNG。

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const UUID = 'danwen@danwen.github.io';

function out(...args) {
    console.log('PROBE', ...args);
}

function wait(ms) {
    return new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    }));
}

function dump(menu, indent = '') {
    for (const item of menu._getMenuItems()) {
        if (!(item.actor ?? item).visible) // 選單區塊（PopupMenuSection）本身不是 actor
            continue;
        if (item instanceof PopupMenu.PopupMenuSection) {
            dump(item, indent);
            continue;
        }
        const mark = item._ornament === PopupMenu.Ornament.CHECK ? '✓' : ' ';
        // 項目右邊的快捷鍵提示；搜尋框印出目前的文字
        const hints = item.get_children().filter(c => c instanceof St.Label && c !== item.label && c.visible && c.text)
            .map(c => `〔${c.text}〕`).join('');
        const entry = item.get_children().find(c => c instanceof St.Entry);
        const text = entry ? `［搜尋框：${entry.get_text() || entry.hint_text}］` : item.label?.text ?? '──';
        out(`${indent}${mark} ${text}${hints}`);
        if (item instanceof PopupMenu.PopupSubMenuMenuItem)
            dump(item.menu, `${indent}    `);
    }
}

// 經 D-Bus 呼叫 extension 的 FocusedApp（跟 danwen 查詢的方式一樣）
function focusedApp() {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call('io.github.danwen.ShellOverlay', '/io/github/danwen/Shell',
            'io.github.danwen.Shell1', 'FocusedApp', null, null, Gio.DBusCallFlags.NONE, 2000, null,
            (connection, result) => {
                try {
                    resolve(connection.call_finish(result).deepUnpack());
                } catch (e) {
                    reject(e);
                }
            });
    });
}

async function openAndDump(indicator, title) {
    indicator.menu.open();
    await wait(1500); // 等選單向 danwen 讀取清單
    out(`== ${title} ==`);
    dump(indicator.menu);
    indicator.menu.close();
    await wait(300);
}

// 截下 actor 周圍（含上方頂列）的畫面
async function shot(name, actors, {pad = 24, fromTop = true} = {}) {
    const boxes = actors.map(a => a.get_transformed_extents());
    const left = Math.min(...boxes.map(b => b.origin.x)) - pad;
    const right = Math.max(...boxes.map(b => b.origin.x + b.size.width)) + pad;
    const top = fromTop ? 0 : Math.min(...boxes.map(b => b.origin.y)) - pad;
    const bottom = Math.max(...boxes.map(b => b.origin.y + b.size.height)) + pad;
    const [x, y] = [Math.max(0, Math.floor(left)), Math.max(0, Math.floor(top))];
    const width = Math.min(global.stage.width, Math.ceil(right)) - x;
    const height = Math.min(global.stage.height, Math.ceil(bottom)) - y;
    const path = GLib.build_filenamev([GLib.getenv('DANWEN_PROBE_SHOTS'), `${name}.png`]);
    const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await new Shell.Screenshot().screenshot_area(x, y, width, height, stream);
    stream.close(null);
    out(`已存 ${path}（${width}×${height}）`);
}

async function screenshots(indicator) {
    Main.overview.hide(); // 啟動時會停在「活動」畫面
    await wait(1000);
    await indicator._call('SetLanguageAsync', '日文');
    await wait(500);

    indicator.menu.open();
    await wait(1500);
    indicator._modeMenu.setSubmenuShown(true);
    await wait(800);
    await shot('menu-modes', [indicator.menu.actor]);
    indicator._modeMenu.setSubmenuShown(false);
    indicator._languageMenu.setSubmenuShown(true);
    await wait(800);
    await shot('menu-translate', [indicator.menu.actor]);
    indicator.menu.close();
    await wait(500);

    await indicator._call('StartLongAsync');
    await wait(3300);
    indicator._osd._stopTimer(); // 計時每秒重畫，截圖時剛好跳秒會疊字
    indicator._osd._render();
    await wait(300);
    await shot('overlay-long', [indicator._osd], {pad: 120});
    await indicator._call('CancelAsync');
    await wait(800);

    // 從「翻譯」的上一張往下滾一格，畫面顯示「翻譯（日文）」
    const [modes] = await indicator._proxy.ListModesAsync();
    const names = modes.map(([name]) => name);
    await indicator._call('SetModeAsync', names[(names.indexOf('翻譯') - 1 + names.length) % names.length]);
    await wait(500);
    indicator._lastCycle = 0;
    await indicator._cycleMode(1);
    await wait(600);
    const osd = Main.osdWindowManager._osdWindows.find(w => w.visible);
    await shot('osd-switch', [osd], {pad: 40, fromTop: false});
}

// 設定畫面：從頂列選單的「設定…」打開（DANWEN_PREFS_PAGE 指定頁面），等視窗出現後截圖或印出標題
async function prefsWindow(indicator) {
    // 設定畫面由 D-Bus 啟動；測試環境沒有 gnome-session 幫忙，要自己告訴 D-Bus 畫面在哪裡
    const env = {};
    for (const name of ['WAYLAND_DISPLAY', 'DISPLAY', 'XDG_CURRENT_DESKTOP', 'GDK_DEBUG', 'ADW_DISABLE_PORTAL']) {
        const value = GLib.getenv(name);
        if (value)
            env[name] = value;
    }
    Gio.DBus.session.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'UpdateActivationEnvironment', new GLib.Variant('(a{ss})', [env]), null, Gio.DBusCallFlags.NONE, -1, null);
    Main.overview.hide();
    await wait(800);
    indicator.menu.open();
    await wait(1200);
    const item = indicator.menu._getMenuItems().find(i => i.label?.text === '設定…');
    if (!item) {
        out('選單裡沒有「設定…」');
        return;
    }
    item.activate(null);
    let win = null;
    const started = GLib.get_monotonic_time();
    for (let i = 0; i < 120 && !win; i++) {
        await wait(500);
        win = global.get_window_actors().map(a => a.meta_window)
            .find(w => (w.get_wm_class() ?? '').includes('Extensions'));
    }
    if (!win) {
        out('設定視窗沒有出現；目前的視窗：',
            JSON.stringify(global.get_window_actors().map(a => a.meta_window.get_wm_class())));
        return;
    }
    out(`設定視窗 ${((GLib.get_monotonic_time() - started) / 1e6).toFixed(1)} 秒後出現`);
    await wait(3000); // 等設定畫面向 danwen 讀完資料
    out(`設定視窗：${win.get_title()}`);
    if (GLib.getenv('DANWEN_PROBE_SHOTS'))
        await shot(`prefs-${GLib.getenv('DANWEN_PREFS_PAGE')}`, [win.get_compositor_private()], {pad: 0, fromTop: false});
}

async function run() {
    await wait(4000);
    const indicator = Main.panel.statusArea[UUID];
    if (!indicator) {
        out('找不到 danwen 的頂列圖示');
        return;
    }
    if (GLib.getenv('DANWEN_PREFS_PAGE')) {
        await prefsWindow(indicator);
        return;
    }
    if (GLib.getenv('DANWEN_PROBE_SHOTS')) {
        await screenshots(indicator);
        return;
    }
    await openAndDump(indicator, '剛啟動');

    // 還沒設定快捷鍵時，選單裡有「設定切換小紙條的快捷鍵」：點下去後再打開選單，提示要出現
    // 每次打開選單都會重建清單，所以要打開後再找
    indicator.menu.open();
    await wait(1500);
    const setup = indicator._modeMenu.menu._getMenuItems().find(i => i.label?.text.startsWith('設定切換小紙條的快捷鍵'));
    if (!setup) {
        indicator.menu.close();
        await wait(300);
    } else {
        setup.activate(null);
        await wait(4000);
        await openAndDump(indicator, '點了「設定快捷鍵」之後');
    }

    indicator.menu.open();
    await wait(1500);
    indicator._languageItems.get('日文')?.activate(null);
    await wait(800);
    out('選「翻譯成 → 日文」後，狀態列：', indicator._statusItem.label.text, '｜浮動提示用的名稱：', indicator._osd._mode);

    indicator._lastCycle = 0;
    await indicator._cycleMode(1);
    await wait(800);
    out('滾輪下一張後，狀態列：', indicator._statusItem.label.text);
    indicator._lastCycle = 0;
    await indicator._cycleMode(-1);
    await wait(800);
    out('滾輪上一張後，狀態列：', indicator._statusItem.label.text);

    indicator.menu.open();
    await wait(1500);
    indicator._searchEntry.text = '會議 下午';
    await wait(1200);
    out('搜尋「會議 下午」：', indicator._historyHeader.label.text,
        JSON.stringify(indicator._history._getMenuItems().map(i => i.label.text)));
    indicator._searchEntry.text = '找不到的字';
    await wait(1200);
    out('搜尋「找不到的字」：', JSON.stringify(indicator._history._getMenuItems().map(i => i.label.text)));
    indicator.menu.close();
    await wait(500);

    await indicator._redo('翻譯（簡體中文）');
    out('換小紙條重新整理（翻譯（簡體中文））完成');

    await openAndDump(indicator, '操作後');

    // 音量條：假的 danwen 長錄音時會送出模擬的音量
    await indicator._call('StartLongAsync');
    await wait(1200);
    const osd = indicator._osd;
    out('錄音中的音量條：', osd._meter.visible ? '顯示' : '隱藏', `寬 ${osd._meterFill.width}／48`,
        osd._meterFill.has_style_class_name('quiet') ? '灰色' : '綠色');
    osd._everHeard = false; // 模擬錄了 4 秒都沒收到聲音
    osd._since -= 4_000_000;
    osd._render();
    out('沒收到聲音時的提示：', osd._label.text);
    await indicator._call('CancelAsync');
    await wait(500);
    out('取消後的錄音提示：', osd.visible ? '顯示' : '隱藏');

    Main.overview.hide();
    await wait(800);
    out('目前的程式（沒有視窗）：', JSON.stringify(await focusedApp()));
    // 測試環境沒有桌面服務（portal），不關掉的話 GTK 程式要等很久才開出視窗
    GLib.spawn_command_line_async('env GDK_DEBUG=no-portals GTK_USE_PORTAL=0 gnome-calculator');
    for (let i = 0; i < 40 && !global.display.focus_window; i++)
        await wait(500);
    await wait(500);
    out('目前的程式（開了計算機）：', JSON.stringify(await focusedApp()));
}

export default class ProbeExtension extends Extension {
    enable() {
        run().catch(e => out('錯誤：', e.message, e.stack)).finally(() => {
            out('DONE');
            global.context.terminate();
        });
    }

    disable() {
    }
}
