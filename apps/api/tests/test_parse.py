"""剧本文件解析：格式分派、编码回退、以及「读不了要说清下一步」。

全是纯逻辑用例，不碰数据库也不碰实例 —— 这一层的价值恰恰在于它能离线跑完，
真机只验 HTTP 那一段（见 test_parse_script_live 之外的手工验收）。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.parse import MAX_BYTES, ParseError, parse_script


def make_docx(paragraphs: list[str]) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
        z.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


# ───────── 能读的 ─────────


def test_utf8_txt_keeps_paragraphs():
    r = parse_script("第一集.txt", "【第一幕】车站\n\n她来了。".encode("utf-8"))
    assert r.text == "【第一幕】车站\n\n她来了。"
    assert (r.format, r.encoding) == ("txt", "utf-8")


def test_gb18030_fallback_and_crlf():
    """中文 Windows 上微信/记事本传出来的稿子常是 GBK，且用 CRLF。"""
    r = parse_script("gbk稿.txt", "【第一幕】车站\r\n她来了。".encode("gb18030"))
    assert r.encoding == "gb18030"
    assert "\r" not in r.text
    assert r.text == "【第一幕】车站\n她来了。"


def test_clean_strips_bom_zero_width_trailing_space_and_collapses_blanks():
    """连续空行只压到 3 个换行：剧本里「两个空行 = 一场结束」是有意义的，不能一并抹平。"""
    raw = "﻿标题  \n\n\n\n\n正文​内容".encode("utf-8")
    assert parse_script("脏.md", raw).text == "标题\n\n\n正文内容"


def test_docx_extracts_body_and_notes_what_was_dropped():
    r = parse_script("稿子.docx", make_docx(["【第一幕】内景 出租车", "雨刮器一下一下。", "", "林晚：我还是来了。"]))
    assert r.format == "docx"
    assert r.text == "【第一幕】内景 出租车\n雨刮器一下一下。\n\n林晚：我还是来了。"
    assert any("页眉" in n for n in r.notes)


def test_html_drops_script_and_style_and_keeps_block_breaks():
    html = "<html><head><style>p{color:red}</style></head><body><h1>第一幕</h1><p>她<b>来了</b>。</p><script>x=1</script></body></html>"
    r = parse_script("网页稿.html", html.encode())
    assert r.text == "第一幕\n\n她来了。"
    assert "x=1" not in r.text and "color" not in r.text


def test_json_takes_raw_script_field():
    raw = json.dumps({"rawScript": "【第一幕】\n正文", "shots": [1, 2]}).encode()
    assert parse_script("导出.json", raw).text == "【第一幕】\n正文"


def test_over_model_cap_gets_a_note_not_a_silent_yes():
    """超过模型单次上限要当场说破：它能进编辑器，但对话改稿会被后端拒。"""
    r = parse_script("大稿.txt", ("场次内容。\n" * 30000).encode())
    d = r.as_dict()
    assert d["overModelCap"] is True
    assert any("24,000" in n for n in d["notes"])


# ───────── 读不了就要说清下一步 ─────────


def test_project_export_is_not_a_script():
    raw = json.dumps({"format": "h3studio.project", "version": 1, "project": {}}).encode()
    with pytest.raises(ParseError) as exc:
        parse_script("项目.json", raw)
    assert "导入项目" in (exc.value.message + "".join(exc.value.hints))


@pytest.mark.parametrize("ext", [".doc", ".pdf", ".rtf", ".wps", ".odt", ".pages"])
def test_refused_formats_point_at_a_way_out(ext):
    with pytest.raises(ParseError) as exc:
        parse_script(f"稿子{ext}", b"xx")
    assert any(word in exc.value.message for word in ("另存", "导出")), exc.value.message


def test_unknown_extension_lists_what_is_readable():
    with pytest.raises(ParseError) as exc:
        parse_script("稿子.srt", b"1\n00:00:00,000 --> x")
    assert any("能读的" in h for h in exc.value.hints)


def test_no_extension_is_refused_with_a_workaround():
    with pytest.raises(ParseError) as exc:
        parse_script("没后缀", "正文".encode())
    assert "扩展名" in exc.value.message


def test_empty_and_oversized_are_refused():
    with pytest.raises(ParseError, match="空的"):
        parse_script("空.txt", b"")
    with pytest.raises(ParseError, match="MB"):
        parse_script("肥.txt", b"a" * (MAX_BYTES + 10))
    with pytest.raises(ParseError) as exc:
        parse_script("巨稿.txt", ("字" * 200_001).encode())
    assert any("分段" in h for h in exc.value.hints)


def test_broken_docx_says_so_instead_of_returning_noise():
    with pytest.raises(ParseError, match="zip"):
        parse_script("坏.docx", b"PK\x03\x04not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/styles.xml", "<x/>")
    with pytest.raises(ParseError, match="document.xml"):
        parse_script("空壳.docx", buf.getvalue())


def test_document_with_no_visible_text_is_not_a_hit():
    """解出空正文必须报错，不能把一份空白稿当成"读成功了"交回前端去覆盖编辑器。"""
    with pytest.raises(ParseError, match="没抽出任何文字"):
        parse_script("空壳.txt", "   \n\n  \n".encode())
