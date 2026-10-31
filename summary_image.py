"""总结出图：把 AI 总结排成 SVG，再用容器内已有的 cairosvg 转成 PNG。

SVG 不会自动换行，所以用与渲染相同的字体文件（PIL 量宽）自行排版。
容器内没有彩色 emoji 字体，emoji 一律去掉，避免画成方框。
"""
import datetime as dt
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import ImageFont

TZ = dt.timezone(dt.timedelta(hours=8))
FAMILY = "Noto Sans CJK SC"
W, M, CP, GAP = 1080, 40, 38, 24      # 画布宽、外边距、卡片内边距、卡片间距
CW = W - 2 * M
INNER = CW - 2 * CP
PAGE_MAX = 3200                        # 单页内容高度上限，超出切下一页
SIZE, REF_SIZE = 30, 20
LH = int(SIZE * 1.62)
BG, CARD, INK, SUB, MUTED, LINK = "#f1f4f9", "#ffffff", "#1a222e", "#465262", "#8a94a2", "#2460e2"
SECTION = {"速览": "#2862ef", "主要话题": "#129466", "结论与待办": "#d68814", "资源链接": "#7c4dd6"}
SPECIAL = re.compile(r"\[消息[^\]\n]{0,80}\]|(?:https?://|www\.)[^\s\]）)，。；、]+")
URL = re.compile(r"(?:https?://|www\.)[^\s\]）)，。；、]+")
TOKEN = re.compile(r"[A-Za-z0-9_\-./:%?=&#+@~]+|\s+|.")
NO_LINE_START = set("，。！？；：、）》」』”’,.!?;:)")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF\uFE0E\uFE0F\u200D\u20E3\U000E0020-\U000E007F]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@lru_cache(None)
def font_file(bold):
    """返回 (字体文件, ttc 内索引)。必须和 cairosvg 实际用的同一套字形，否则量宽不准。"""
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}|%{index}", FAMILY + (":bold" if bold else "")],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        path, _, index = out.partition("|")
        if "CJK" in path and Path(path).exists():
            return path, int(index or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # 本地开发机没有 Noto CJK 时用雅黑量宽，仅用于测试排版
    for path in (["C:/Windows/Fonts/msyhbd.ttc"] if bold else []) + ["C:/Windows/Fonts/msyh.ttc"]:
        if Path(path).exists():
            return path, 0
    raise RuntimeError("未找到中文字体")


@lru_cache(None)
def font(size, bold=False):
    path, index = font_file(bold)
    return ImageFont.truetype(path, size, index=index)


def measure(text, size, bold=False):
    return font(size, bold).getlength(text)


def clean(text):
    return CONTROL.sub("", EMOJI.sub("", str(text or "")))


def fit(text, size, width, bold=False):
    """超宽时截断并加省略号。"""
    if measure(text, size, bold) <= width:
        return text
    while text and measure(text + "…", size, bold) > width:
        text = text[:-1]
    return text + "…"


def short_link(url, width):
    shown = re.sub(r"^https?://", "", url).rstrip("/")
    return fit(shown, SIZE, width)


# —— 解析 ——
def parse(text):
    """拆成区块：【标题】、“1. 话题”、“• 要点”和普通段落；无法识别的整体按段落处理。"""
    sections = []
    for raw in clean(text).replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line).strip()
        if not line or line.startswith(("```", "~~~")):
            continue
        m = re.match(r"^【(.+?)】\s*(.*)$", line)
        if m:
            sections.append({"title": m[1].strip(), "items": []})
            line = m[2].strip()
            if not line:
                continue
        if not sections:
            sections.append({"title": "", "items": []})
        items = sections[-1]["items"]
        if re.match(r"^\d{1,2}[.、]\s*\S", line):
            items.append(("topic", re.sub(r"^\d{1,2}[.、]\s*", "", line)))
        elif line[0] in "•-*·●":
            items.append(("bullet", line[1:].strip()))
        else:
            items.append(("para", line))
    return [s for s in sections if s["items"] or s["title"]]


def links(text):
    """图里的链接点不了，另发文字：优先用“资源链接”区块的整行，其余链接补在后面。"""
    lines, seen = [], set()
    for sec in parse(text):
        if sec["title"] == "资源链接":
            for _, item in sec["items"]:
                found = URL.findall(item)
                if found:
                    lines.append(item)
                    seen.update(found)
    for url in URL.findall(clean(text)):
        if url not in seen:
            seen.add(url)
            lines.append(url)
    return lines


# —— 排版 ——
def tokens(text, size=SIZE, bold=False, width=INNER):
    out, pos = [], 0
    kind = "bold" if bold else "text"

    def plain(t):
        for piece in TOKEN.findall(t):
            w = measure(piece, size, bold)
            if w > width:  # 超长英文按字符断开
                out.extend((c, kind, measure(c, size, bold)) for c in piece)
            else:
                out.append((piece, kind, w))

    for m in SPECIAL.finditer(text):
        plain(text[pos:m.start()])
        if m[0].startswith("["):
            ref = m[0][1:-1]
            out.append((ref, "ref", measure(ref, REF_SIZE) + 22))
        else:
            shown = short_link(m[0], width * .9)
            out.append((shown, "link", measure(shown, size)))
        pos = m.end()
    plain(text[pos:])
    return out


def wrap(toks, width):
    lines, w = [[]], 0
    for tok in toks:
        text, kind, tw = tok
        if lines[-1] and w + tw > width and text not in NO_LINE_START:
            if text.isspace():
                continue
            carry = []
            if kind == "ref":  # 消息编号不单独掉行，带上前面两个字一起换
                while (lines[-1] and len(carry) < 2 and lines[-1][-1][1] in ("text", "bold")
                       and not lines[-1][-1][0].isspace()):
                    carry.insert(0, lines[-1].pop())
                if not lines[-1]:
                    lines[-1], carry = carry, []
            lines.append(carry)
            w = sum(c[2] for c in carry)
        if not lines[-1] and text.isspace():
            continue
        lines[-1].append(tok)
        w += tw
    return [line for line in lines if line]


def svg_text(x, y, text, size, fill, bold=False, anchor="start", opacity=1):
    attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}"'
    if bold:
        attrs += ' font-weight="bold"'
    if anchor != "start":
        attrs += f' text-anchor="{anchor}"'
    if opacity != 1:
        attrs += f' fill-opacity="{opacity}"'
    return f'<text {attrs} xml:space="preserve">{escape(text)}</text>'


def draw_lines(x, top, lines, size, color, bold=False):
    out = []
    for i, line in enumerate(lines):
        base, cx, run = top + i * LH + size * 1.12, x, None
        for text, kind, tw in line + [("", "end", 0)]:
            if run and kind != run[0]:
                fill = LINK if run[0] == "link" else color
                out.append(svg_text(run[1], base, run[2], size, fill, run[0] == "bold"))
                run = None
            if kind == "ref":
                rt = base - REF_SIZE - 3
                out.append(f'<rect x="{cx + 8:.1f}" y="{rt:.1f}" width="{tw - 10:.1f}" height="{REF_SIZE + 12}" rx="9" fill="#ecf0f6"/>')
                out.append(svg_text(cx + 8 + (tw - 10) / 2, rt + (REF_SIZE + 12) / 2 + REF_SIZE * .36, text, REF_SIZE, MUTED, anchor="middle"))
            elif kind != "end":
                if run is None:
                    run = [kind, cx, ""]
                run[2] += text
            cx += tw
    return "".join(out)


def elements(sec):
    """每个元素：(高度, 绘制函数(x, y) -> svg)。"""
    els, number, color = [], 0, SECTION.get(sec["title"], LINK)
    for kind, text in sec["items"]:
        if kind == "topic":
            if number:
                els.append((34, "sep", lambda x, y: f'<rect x="{x}" y="{y + 16}" width="{INNER}" height="1.5" fill="#e6eaf0"/>'))
            number += 1
            lines = wrap(tokens(text, bold=True, width=INNER - 50), INNER - 50)

            def draw(x, y, n=number, lines=lines):
                return (f'<circle cx="{x + 18}" cy="{y + SIZE * .78:.1f}" r="18" fill="{SECTION["主要话题"]}" fill-opacity=".12"/>'
                        + svg_text(x + 18, y + SIZE * .78 + 8, str(n), 22, SECTION["主要话题"], True, "middle")
                        + draw_lines(x + 50, y, lines, SIZE, INK, True))
            els.append((len(lines) * LH + 8, "topic", draw))
        elif kind == "bullet":
            indent = 50 if number else 0
            width = INNER - indent - 26
            lines = wrap(tokens(text, width=width), width)

            def draw(x, y, indent=indent, lines=lines):
                return (f'<circle cx="{x + indent + 8}" cy="{y + SIZE * .78:.1f}" r="5" fill="{color}"/>'
                        + draw_lines(x + indent + 26, y, lines, SIZE, SUB))
            els.append((len(lines) * LH + 6, "bullet", draw))
        else:
            lines = wrap(tokens(text), INNER)
            els.append((len(lines) * LH + 8, "para", lambda x, y, lines=lines: draw_lines(x, y, lines, SIZE, INK)))
    return els


def paginate(sections, first_header, other_header):
    pages, used = [[]], first_header
    for sec in sections:
        title, started = sec["title"], False
        card = {"title": title, "cont": False, "els": []}
        frame = GAP + CP + (66 if title else 0) + CP - 6
        for el in elements(sec):
            if el[1] == "sep" and not card["els"]:
                continue
            extra = el[0] + (frame if not card["els"] else 0)
            if used + extra > PAGE_MAX and (pages[-1] or card["els"]):
                if card["els"]:
                    pages[-1].append(card)
                pages.append([])
                used = other_header
                card = {"title": title, "cont": started, "els": []}
                if el[1] == "sep":
                    continue
                extra = el[0] + frame
            card["els"].append(el)
            used += extra
            started = True
        if card["els"] or (title and not started):
            pages[-1].append(card)
    return [p for p in pages if p] or [[]]


def card_svg(card, y):
    color = SECTION.get(card["title"], LINK)
    head = 66 if card["title"] else 0
    h = CP + head + sum(e[0] for e in card["els"]) + CP - 6
    out = [f'<rect x="{M}" y="{y + 4}" width="{CW}" height="{h}" rx="22" fill="#1a2b4a" fill-opacity=".05"/>',
           f'<rect x="{M}" y="{y}" width="{CW}" height="{h}" rx="22" fill="{CARD}"/>']
    cy = y + CP
    if card["title"]:
        out.append(f'<rect x="{M + CP}" y="{cy + 4}" width="7" height="34" rx="3.5" fill="{color}"/>')
        out.append(svg_text(M + CP + 22, cy + 32, card["title"] + ("（续）" if card["cont"] else ""), 31, INK, True))
        cy += head
    for height, _, draw in card["els"]:
        out.append(draw(M + CP, cy))
        cy += height
    return "".join(out), h


def header_svg(job, rows, sections, link_count):
    top, h = 36, 234
    start, end = min(r["source_time"] for r in rows), max(r["source_time"] for r in rows)
    fmt = "%m-%d %H:%M"
    span = (dt.datetime.fromtimestamp(start, TZ).strftime(fmt) + " ～ " +
            dt.datetime.fromtimestamp(end, TZ).strftime(fmt if (end - start) > 86400 * .5 else "%H:%M"))
    topics = sum(1 for s in sections for k, _ in s["items"] if k == "topic")
    todos = sum(len(s["items"]) for s in sections if s["title"] == "结论与待办")
    chips = [c for c in (f"{topics} 个话题" if topics else "", f"{todos} 项结论 / 待办" if todos else "",
                         f"{link_count} 个链接" if link_count else "") if c]
    x, right = M + 34, M + CW - 34
    out = [f'<clipPath id="hc"><rect x="{M}" y="{top}" width="{CW}" height="{h}" rx="26"/></clipPath>',
           f'<rect x="{M}" y="{top}" width="{CW}" height="{h}" rx="26" fill="url(#hg)"/>',
           f'<g clip-path="url(#hc)"><circle cx="{right - 60}" cy="{top + 40}" r="150" fill="#fff" fill-opacity=".08"/>'
           f'<circle cx="{right + 10}" cy="{top + h - 10}" r="110" fill="#fff" fill-opacity=".06"/></g>',
           svg_text(x, top + 50, f"群聊简报  ·  任务 #{job['id']}", 22, "#fff", True, opacity=.82),
           svg_text(x, top + 108, fit(clean(job["group_name"]), 44, CW - 34 - 280, True), 44, "#fff", True),
           svg_text(x, top + 150, span + "（北京时间）", 24, "#fff", opacity=.88),
           svg_text(right, top + 118, str(len(rows)), 66, "#fff", True, "end"),
           svg_text(right, top + 156, "条消息", 22, "#fff", anchor="end", opacity=.8)]
    cx = x
    for chip in chips:
        cw = measure(chip, 22) + 32
        out.append(f'<rect x="{cx:.1f}" y="{top + 176}" width="{cw:.1f}" height="40" rx="20" fill="#fff" fill-opacity=".18"/>')
        out.append(svg_text(cx + 16, top + 204, chip, 22, "#fff"))
        cx += cw + 12
    return "".join(out), top + h


def cont_header_svg(job, index, total):
    top, h = 36, 70
    title = fit(f"群聊简报 #{job['id']} · {clean(job['group_name'])}", 24, CW - 220, True)
    return (f'<rect x="{M}" y="{top}" width="{CW}" height="{h}" rx="18" fill="url(#hg)"/>'
            + svg_text(M + 30, top + 44, title, 24, "#fff", True)
            + svg_text(M + CW - 30, top + 44, f"第 {index}/{total} 页", 22, "#fff", anchor="end", opacity=.85)), top + h


def build_svgs(job, rows):
    """返回每页 SVG 字符串和需另发的链接行；不依赖 cairosvg，便于测试。"""
    sections = parse(job["result"])
    if not sections:
        raise ValueError("总结为空")
    link_lines = links(job["result"])
    pages = paginate(sections, 36 + 234, 36 + 70)
    made = dt.datetime.fromtimestamp(job.get("created_at") or rows[-1]["source_time"], TZ).strftime("%m-%d %H:%M")
    svgs = []
    for i, page in enumerate(pages, 1):
        head, y = header_svg(job, rows, sections, len(link_lines)) if i == 1 else cont_header_svg(job, i, len(pages))
        body = [head]
        for card in page:
            y += GAP
            part, h = card_svg(card, y)
            body.append(part)
            y += h
        y += 46
        if i == len(pages) and link_lines:
            body.append(svg_text(W / 2, y, "图中链接无法点击，已另发一条文字消息", 20, MUTED, anchor="middle"))
            y += 32
        body.append(svg_text(W / 2, y, f"群聊情报站 · 任务 #{job['id']} · 生成于 {made}"
                             + (f" · 第 {i}/{len(pages)} 页" if len(pages) > 1 else ""), 20, MUTED, anchor="middle", opacity=.8))
        height = int(y + 36)
        svgs.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}" '
                    f'font-family="{FAMILY}, sans-serif"><defs><linearGradient id="hg" x1="0" y1="0" x2="1" y2="1">'
                    '<stop offset="0" stop-color="#2156e8"/><stop offset="1" stop-color="#5b8cff"/></linearGradient></defs>'
                    f'<rect width="{W}" height="{height}" fill="{BG}"/>' + "".join(body) + "</svg>")
    return svgs, link_lines


def render_summary(job, rows):
    """返回 {"pages": [PNG 字节], "links": [链接行]}；出错时抛异常，由调用方退回文字版。"""
    import cairosvg  # 只在容器内可用，延迟导入
    svgs, link_lines = build_svgs(job, rows)
    return {"pages": [cairosvg.svg2png(bytestring=s.encode("utf-8")) for s in svgs], "links": link_lines}
