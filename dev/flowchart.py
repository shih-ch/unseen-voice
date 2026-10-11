"""產生 README 的流程圖（地端／雲端用的模型與方法）：docs/flow-light.svg、docs/flow-dark.svg。

GitHub 的 README 用 <picture> 依佈景主題選亮色或暗色版本。改了方案或預設模型時，
改下面的 BOXES 再執行：python3 dev/flowchart.py
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
FONT = "'Noto Sans CJK TC','Noto Sans TC','PingFang TC','Microsoft JhengHei',system-ui,sans-serif"

# 每種方塊的 填色、框線、標題、副標題；亮色用色階的 50／600／800／600，暗色用 800／200／100／200
THEMES = {
    "light": {
        "local": ("#E1F5EE", "#0F6E56", "#085041", "#0F6E56"),
        "cloud": ("#FAECE7", "#993C1D", "#712B13", "#993C1D"),
        "neutral": ("#F1EFE8", "#5F5E5A", "#444441", "#5F5E5A"),
        "text": "#5F5E5A", "arrow": "#73726C", "frame": "#B4B2A9",
    },
    "dark": {
        "local": ("#085041", "#5DCAA5", "#9FE1CB", "#5DCAA5"),
        "cloud": ("#712B13", "#F0997B", "#F5C4B3", "#F0997B"),
        "neutral": ("#444441", "#B4B2A9", "#D3D1C7", "#B4B2A9"),
        "text": "#B4B2A9", "arrow": "#9C9A92", "frame": "#5F5E5A",
    },
}

# (種類, x, y, 寬, 高, 標題, 副標題)
BOXES = (
    ("neutral", 220, 50, 240, 56, "按住右 Ctrl 說話", "錄音（藍牙自動切免持）"),
    ("local", 220, 136, 240, 56, "語音偵測 Silero VAD", "沒說話就不送辨識"),
    ("local", 60, 236, 260, 56, "SenseVoice Small", "方案 A、B・sherpa-onnx CPU"),
    ("cloud", 360, 236, 260, 56, "whisper-large-v3-turbo", "方案 C Groq・D Cloudflare"),
    ("local", 200, 336, 280, 56, "轉台灣繁體＋替換字典", "OpenCC s2twp・一律本機"),
    ("local", 60, 438, 170, 56, "Ollama qwen3:4b", "方案 A"),
    ("cloud", 255, 438, 170, 56, "Groq qwen3.8-27b", "方案 B（預設）、C"),
    ("cloud", 450, 438, 170, 56, "llama-3.3-70b", "方案 D Cloudflare"),
    ("neutral", 200, 540, 280, 56, "貼到目前的程式", "剪貼簿＋模擬 Ctrl+V"),
)

# (路徑, 有沒有箭頭, 虛線)
ARROWS = (
    ("M340 106 L340 134", True, False),
    ("M340 192 L340 212 L190 212 L190 234", True, False),
    ("M340 212 L490 212 L490 234", True, False),
    ("M190 292 L190 314 L340 314 L340 334", True, False),
    ("M490 292 L490 314 L340 314", False, False),
    ("M480 364 L655 364 L655 568 L484 568", True, True),
    ("M340 392 L340 402", True, False),
    ("M340 510 L340 538", True, False),
)

# (x, y, 文字)
LABELS = (
    (500, 350, "一般聽寫直接貼上"),
    (56, 421, "整理模式才做（短按再按住）：小紙條＋LLM"),
)

WIDTH, HEIGHT = 680, 616


def text(x, y, s, fill, size=12, weight=400, anchor="start") -> str:
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" dominant-baseline="central">{escape(s)}</text>')


def render(theme: dict) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" font-family="{FONT}">',
        "<title>但聞人語的聽寫流程：地端與雲端</title>",
        "<desc>錄音、Silero VAD 語音偵測、語音辨識（本機 SenseVoice 或雲端 whisper-large-v3-turbo）、"
        "OpenCC 與替換字典、整理模式的 LLM（本機 Ollama 或雲端 Groq、Cloudflare），最後貼上。</desc>",
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
        f'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="{theme["arrow"]}" '
        'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>',
    ]
    # 圖例
    for x, kind, label in ((190, "local", "地端（這台電腦）"), (370, "cloud", "雲端（要連網）")):
        fill, stroke, _, _ = theme[kind]
        out.append(f'<rect x="{x}" y="12" width="14" height="14" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="0.75"/>')
        out.append(text(x + 22, 19, label, theme["text"]))
    out.append(f'<rect x="40" y="404" width="600" height="106" rx="12" fill="none" stroke="{theme["frame"]}" '
               'stroke-width="0.75" stroke-dasharray="5 4"/>')
    for d, head, dashed in ARROWS:
        extra = (' marker-end="url(#arrow)"' if head else "") + (' stroke-dasharray="4 4"' if dashed else "")
        out.append(f'<path d="{d}" fill="none" stroke="{theme["arrow"]}" stroke-width="1.5"{extra}/>')
    for x, y, s in LABELS:
        out.append(text(x, y, s, theme["text"]))
    for kind, x, y, w, h, title, subtitle in BOXES:
        fill, stroke, title_color, sub_color = theme[kind]
        cx = x + w // 2
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="0.75"/>')
        out.append(text(cx, y + 18, title, title_color, 14, 500, "middle"))
        out.append(text(cx, y + 38, subtitle, sub_color, 12, 400, "middle"))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    for name, theme in THEMES.items():
        path = ROOT / "docs" / f"flow-{name}.svg"
        path.write_text(render(theme), encoding="utf-8")
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
