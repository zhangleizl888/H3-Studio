"""剧本文件解析：把外部稿子读成编辑器能直接吃的纯文本。

只用标准库。这台机器上没装任何文档解析依赖，而常见稿子格式 stdlib 全覆盖：
.docx 本身就是 zip + WordprocessingML（zipfile + xml.etree 抽 <w:t>），
.html 有 html.parser，编码回退有 gb18030（GBK/GB2312 的超集）。为这一个功能引包不值。

一条底线：**解不出来就报错并说清下一步**（例如老 .doc 要另存为 .docx 再传），
绝不返回一份被乱码糊过的「看起来像正文」的东西 —— 它接下来会被当成整篇稿子写回编辑器。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .llm import MAX_INPUT_CHARS

MAX_BYTES = 12 * 1024 * 1024
#: 解出来的正文字数上限。再大既撑爆 IndexedDB，也根本进不了模型（上限 24000 字）
MAX_TEXT_CHARS = 200_000

TEXT_EXTS = {".txt", ".text", ".md", ".markdown", ".fdx", ".fountain", ".csv", ".tsv"}
HTML_EXTS = {".html", ".htm"}
#: 明确拒收的：不是「不支持」三个字能交代过去的，要给可执行的下一步
REFUSALS: dict[str, str] = {
    ".doc": "老 .doc 是二进制格式，标准库读不了。请在 Word/WPS 里另存为 .docx 或 .txt 再传。",
    ".rtf": "RTF 的转义层要靠库来剥。请在 Word/WPS 里另存为 .docx 或 .txt 再传。",
    ".wps": "WPS 私有格式读不了。请在 WPS 里另存为 .docx 或 .txt 再传。",
    ".odt": "ODT 的正文在 content.xml 里、命名空间与 docx 不同。请先另存为 .docx 或 .txt 再传。",
    ".pdf": "PDF 抽文本层要靠库（字体子集与编码映射绕不过去），扫描件还得 OCR。请先另存为 .docx 或 .txt 再传。",
    ".pages": "苹果 Pages 的包结构读不了。请先在 Pages 里导出为 .docx 或纯文本再传。",
    ".docxf": "加密/模板变体读不了。请先另存为普通 .docx 再传。",
}

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ParseError(Exception):
    """读不了。message 就是给人看的下一步，别在路由层再包一层「操作失败」。"""

    def __init__(self, message: str, *, hints: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hints = hints or []


@dataclass
class ParsedScript:
    name: str
    format: str
    text: str
    encoding: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def lines(self) -> int:
        return self.text.count("\n") + 1 if self.text else 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "format": self.format,
            "encoding": self.encoding,
            "chars": self.chars,
            "lines": self.lines,
            "text": self.text,
            "notes": self.notes,
            "overModelCap": self.chars > MAX_INPUT_CHARS,
            "modelCap": MAX_INPUT_CHARS,
        }


# ───────── 入口 ─────────


def parse_script(filename: str, raw: bytes) -> ParsedScript:
    name = (filename or "未命名").strip() or "未命名"
    ext = Path(name).suffix.lower()
    if not raw:
        raise ParseError("文件是空的")
    if len(raw) > MAX_BYTES:
        raise ParseError(f"文件 {len(raw) / 1024 / 1024:.1f} MB，超过 {MAX_BYTES // 1024 // 1024} MB 上限")

    if ext in REFUSALS:
        raise ParseError(f"{ext} 读不了。{REFUSALS[ext]}")
    if not ext:
        raise ParseError(
            f"「{name}」没有扩展名，认不出是什么格式。",
            hints=["改成 .txt 或 .md 再传", "或者直接把正文粘进剧本编辑器"],
        )

    encoding: str | None
    if ext == ".docx":
        text, fmt, encoding = _docx_text(raw), "docx", None
    elif ext in HTML_EXTS:
        text, encoding = _decode(raw)
        text, fmt = _html_text(text), "html"
    elif ext == ".json":
        text, fmt, encoding = _json_text(raw), "json", None
    elif ext in TEXT_EXTS:
        text, encoding = _decode(raw)
        fmt = ext.lstrip(".")
    else:
        raise ParseError(
            f"不支持 {ext} 这种文件。",
            hints=[f"能读的：{'、'.join(sorted(e for e in TEXT_EXTS | HTML_EXTS | {'.docx', '.json'}))}", "其它格式请先另存为 .txt / .md / .docx"],
        )

    text = _clean(text)
    if not text.strip():
        raise ParseError(f"从「{name}」里没抽出任何文字。")
    if len(text) > MAX_TEXT_CHARS:
        raise ParseError(
            f"抽出 {len(text):,} 字，超过 {MAX_TEXT_CHARS:,} 字的读入上限。这份稿子按一次对话整篇回吐是跑不动的。",
            hints=["先按场次分段：拆成几个文件，一次传一段"],
        )

    out = ParsedScript(name=name, format=fmt, text=text, encoding=encoding)
    if out.chars > MAX_INPUT_CHARS:
        out.notes.append(
            f"正文 {out.chars:,} 字，超过模型单次上限 {MAX_INPUT_CHARS:,} 字：这份稿子能进编辑器，"
            "但对话改稿会被后端直接拒（它要整篇回吐）。要改就先分场分段。"
        )
    if fmt == "docx":
        out.notes.append("只抽了正文：页眉页脚、批注、文本框与表格线不进稿子。")
    if fmt == "html":
        out.notes.append("按纯文本抽的：加粗/颜色这类排版会丢。")
    return out


# ───────── 各格式 ─────────


def _decode(raw: bytes) -> tuple[str, str]:
    """先 UTF-8（含 BOM），再 GB18030，再 Big5。

    顺序不能反：GB18030 几乎任何字节序列都解得开，先试它会把一份真 UTF-8 的稿子
    悄悄解成乱码 —— 那正是「看起来像正文」的事故来源。
    """
    for enc in ("utf-8-sig", "gb18030", "big5"):
        try:
            return raw.decode(enc), ("utf-8" if enc == "utf-8-sig" else enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8（有非法字节，已按替换符处理）"


def _docx_text(raw: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ParseError("这个 .docx 不是有效的 zip 包，多半是下载/转发时坏了。", hints=["重新从 Word/WPS 另存一份"]) from exc
    with zf:
        if "word/document.xml" not in zf.namelist():
            raise ParseError(".docx 里没有 word/document.xml，不是 Word 正文文档。")
        try:
            root = ET.fromstring(zf.read("word/document.xml"))
        except ET.ParseError as exc:
            raise ParseError(f".docx 的正文 XML 解析失败：{type(exc).__name__}") from exc

    paras: list[str] = []
    for p in root.iter(f"{W_NS}p"):
        buf: list[str] = []
        for node in p.iter():
            if node.tag == f"{W_NS}t":
                buf.append(node.text or "")
            elif node.tag == f"{W_NS}tab":
                buf.append("\t")
            elif node.tag in (f"{W_NS}br", f"{W_NS}cr"):
                buf.append("\n")
        paras.append("".join(buf))
    return "\n".join(paras)


class _TextScraper(HTMLParser):
    """只留可见文本：script/style 整块跳过，块级标签后面补一个换行，保住段落结构。"""

    SKIP = {"script", "style", "head", "noscript", "svg"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip and data:
            self.out.append(data)


def _html_text(html: str) -> str:
    scraper = _TextScraper()
    scraper.feed(html)
    return "".join(scraper.out)


def _json_text(raw: bytes) -> str:
    text, _ = _decode(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"扩展名是 .json 但内容不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）") from exc
    if isinstance(data, dict):
        if data.get("format") == "h3studio.project":
            raise ParseError(
                "这是 H3 Studio 的项目导出文件，不是剧本正文。",
                hints=["到 项目库 → 导入项目 整包导入（镜头、角色、参考图会一起回来）"],
            )
        script = data.get("rawScript")
        if isinstance(script, str) and script.strip():
            return script
    return json.dumps(data, ensure_ascii=False, indent=2)


# ───────── 收尾清理 ─────────

_ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\ufeff]")


def _clean(text: str) -> str:
    text = _ZERO_WIDTH.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()
