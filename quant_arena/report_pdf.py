"""Render a daily-report markdown document to PDF bytes.

Shared by every arena via :class:`BaseArenaService`; implemented once here so
the three arenas (A-share / Futumoo / IB) all produce identical output. The
pipeline is ``python-markdown`` -> HTML+CSS -> ``weasyprint`` -> PDF, chosen
for solid CJK font handling and wide-table layout without needing a headless
browser. Reports are written in Chinese and contain wide multi-column tables,
so the CSS below is tuned for those (small fixed-layout tables, CJK font
stack, page numbering).

WeasyPrint depends on native libraries (pango, cairo, gdk-pixbuf, harfbuzz,
fontconfig). On the deployment host (Debian/Raspberry Pi) install them with::

    apt-get install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
        libcairo2 libffi-dev fontconfig fonts-noto-cjk

A CJK font (e.g. fonts-noto-cjk) must be present or Chinese glyphs render as
blank boxes. Imports are deferred so a missing native lib only breaks PDF
generation, never report submission or service startup.
"""

from logging import getLogger

logger = getLogger(__name__)

# Tuned on real tester reports: A4, CJK font stack, 9px fixed-layout tables so
# the widest (8-column) tables wrap instead of overflowing the page.
_CSS = """
@page {
    size: A4;
    margin: 1.6cm 1.4cm;
    @bottom-center { content: counter(page) " / " counter(pages); font-size: 9px; color: #888; }
}
body {
    font-family: "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Heiti SC", "Hiragino Sans GB", "WenQuanYi Zen Hei", "Droid Sans Fallback", sans-serif;
    font-size: 10.5px;
    line-height: 1.6;
    color: #1a1a1a;
}
h1 { font-size: 19px; border-bottom: 2px solid #333; padding-bottom: 6px; }
h2 { font-size: 15px; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 22px; }
h3 { font-size: 12.5px; margin-top: 16px; }
h1, h2, h3 { line-height: 1.35; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 10px 0;
    font-size: 9px;
    table-layout: fixed;
    word-break: break-all;
}
th, td { border: 1px solid #bbb; padding: 3px 5px; text-align: left; vertical-align: top; }
th { background: #f0f0f0; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
blockquote {
    border-left: 3px solid #ccc; margin: 10px 0; padding: 4px 12px;
    color: #555; background: #f7f7f7; font-size: 9.5px;
}
code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-family: "SF Mono", Menlo, monospace; font-size: 9px; }
hr { border: none; border-top: 1px solid #ddd; margin: 14px 0; }
ul, ol { padding-left: 22px; }
li { margin: 2px 0; }
strong { font-weight: 600; }
"""


def render_daily_report_pdf(content: str) -> bytes:
    """Convert daily-report markdown to PDF bytes.

    Raises whatever the underlying libraries raise (missing native deps,
    parse errors); callers treat PDF delivery as best-effort and must not let
    a failure here block report persistence.
    """
    import markdown
    from weasyprint import HTML

    html_body = markdown.markdown(
        content,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    html_doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>{html_body}</body></html>"
    )
    return HTML(string=html_doc).write_pdf()
