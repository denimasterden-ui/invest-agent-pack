#!/usr/bin/env python3
"""Лёгкий md -> styled HTML -> PDF через headless Chrome. Кириллица-safe, без зависимостей.

Использование: /usr/bin/python3 md2pdf.py input.md [output.pdf]
Поддерживает подмножество markdown: # h1-h6, **bold**, `code`, [link](url),
таблицы, маркированные/нумерованные списки, > цитаты, --- разделители.
"""
import sys, os, re, html, subprocess, tempfile

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.5; color: #1a1a1a; max-width: 100%; }
h1 { font-size: 20pt; margin: 0 0 4px; color: #111; }
h2 { font-size: 14pt; margin: 22px 0 6px; padding-bottom: 4px; border-bottom: 2px solid #e2e2e2; color: #111; }
h3 { font-size: 11.5pt; margin: 16px 0 4px; color: #222; }
p { margin: 6px 0; }
em { color: #666; }
strong { color: #000; }
code { font-family: "SF Mono", Menlo, monospace; font-size: 9pt;
       background: #f3f3f3; padding: 1px 4px; border-radius: 3px; }
a { color: #1257a8; text-decoration: none; }
hr { border: none; border-top: 1px solid #d8d8d8; margin: 18px 0; }
ul, ol { margin: 6px 0 6px 0; padding-left: 22px; }
li { margin: 3px 0; }
blockquote { margin: 8px 0; padding: 6px 12px; border-left: 3px solid #c8c8c8;
             background: #fafafa; color: #444; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9.5pt; }
th { background: #f0f2f5; text-align: left; font-weight: 600; }
th, td { border: 1px solid #d5d8dc; padding: 5px 9px; vertical-align: top; }
tr:nth-child(even) td { background: #fafbfc; }
"""


def inline(text):
    text = html.escape(text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text


def render_table(rows):
    def cells(r):
        return [c.strip() for c in r.strip().strip('|').split('|')]
    header = cells(rows[0])
    body = rows[2:] if len(rows) > 1 else []
    th = ''.join(f'<th>{inline(c)}</th>' for c in header)
    trs = ''.join('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in cells(r)) + '</tr>' for r in body)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def convert(md):
    lines = md.split('\n')
    out, i, n = [], 0, len(md.split('\n'))
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1; continue
        if s.startswith('|'):
            tbl = []
            while i < n and lines[i].strip().startswith('|'):
                tbl.append(lines[i].strip()); i += 1
            out.append(render_table(tbl)); continue
        if re.match(r'^([-*_])\1{2,}$', s):
            out.append('<hr>'); i += 1; continue
        m = re.match(r'^(#{1,6})\s+(.*)$', s)
        if m:
            lvl = len(m.group(1))
            out.append(f'<h{lvl}>{inline(m.group(2))}</h{lvl}>'); i += 1; continue
        if re.match(r'^[-*]\s+', s) or re.match(r'^\d+\.\s+', s):
            ordered = bool(re.match(r'^\d+\.\s+', s))
            items = []
            while i < n and (re.match(r'^[-*]\s+', lines[i].strip()) or re.match(r'^\d+\.\s+', lines[i].strip())):
                items.append('<li>' + inline(re.sub(r'^([-*]|\d+\.)\s+', '', lines[i].strip())) + '</li>')
                i += 1
            tag = 'ol' if ordered else 'ul'
            out.append(f'<{tag}>{"".join(items)}</{tag}>'); continue
        if s.startswith('>'):
            q = inline(re.sub(r'^>\s?', '', s))
            out.append(f'<blockquote>{q}</blockquote>'); i += 1; continue
        para = []
        while i < n:
            t = lines[i].strip()
            if not t or t.startswith(('|', '#', '>')) or re.match(r'^[-*]\s+', t) \
               or re.match(r'^\d+\.\s+', t) or re.match(r'^([-*_])\1{2,}$', t):
                break
            para.append(t); i += 1
        if para:
            out.append(f'<p>{inline(" ".join(para))}</p>')
    return '\n'.join(out)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: md2pdf.py input.md [output.pdf]")
    src = os.path.abspath(sys.argv[1])
    out = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.splitext(src)[0] + ".pdf"
    with open(src, encoding="utf-8") as f:
        body = convert(f.read())
    doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tf:
        tf.write(doc); html_path = tf.name
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={profile}", f"--print-to-pdf={out}",
                        "file://" + html_path], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.unlink(html_path)
    print(f"PDF: {out} ({os.path.getsize(out)//1024} KB)")


if __name__ == "__main__":
    main()
