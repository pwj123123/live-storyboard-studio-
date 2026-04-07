import streamlit as st
import anthropic
from dotenv import load_dotenv
import os
import io
import json
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import openpyxl

from pptx import Presentation
from pptx.util import Pt, Cm
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 라이브커머스 방송 전문 스토리보드 작가입니다.
제품 소개서를 기반으로 라이브 방송의 전체 흐름(오프닝~클로징)을 구성하고,
쇼호스트가 바로 사용할 수 있는 구체적인 대본과 연출 지시를 작성합니다.
시청자의 구매를 유도하는 셀링포인트와 혜택을 강조해주세요."""


# ─────────────────────────────────────────
# 유틸 함수
# ─────────────────────────────────────────

def call_claude(prompt):
    """Claude API 호출"""
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def parse_excel_products(file_bytes):
    """엑셀 제품 소개서에서 상품 정보 + URL 추출"""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    all_cells = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            if cell.value is not None:
                all_cells[cell.coordinate] = str(cell.value).strip()

    # URL 추출
    urls = []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            if cell.value:
                found = re.findall(r'https?://\S+', str(cell.value))
                for u in found:
                    if u not in urls:
                        urls.append(u)
            if cell.hyperlink and cell.hyperlink.target:
                u = cell.hyperlink.target
                if u.startswith("http") and u not in urls:
                    urls.append(u)

    # 상품 정보 텍스트
    brand = ""
    for coord, val in all_cells.items():
        if "브랜드" in val or "제품명" in val or "상품명" in val:
            brand = val

    products = []
    for coord, val in all_cells.items():
        row_num = int(re.findall(r'\d+', coord)[0])
        row_vals = {c: v for c, v in all_cells.items() if int(re.findall(r'\d+', c)[0]) == row_num}
        row_text = " | ".join(row_vals.values())
        if len(row_text) > 5 and row_text not in products:
            products.append(row_text)

    full_text = f"브랜드: {brand}\n\n" + "\n".join(products[:80])
    if len(full_text) > 3000:
        full_text = full_text[:3000]

    wb.close()
    return {"text": full_text, "urls": urls, "brand": brand}


def fetch_product_info(url):
    """상품 URL에서 정보를 가져온다"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "iframe", "noscript"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        meta_desc = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            meta_desc = meta["content"].strip()

        og_data = {}
        for og in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
            key = og.get("property", "").replace("og:", "")
            val = og.get("content", "")
            if val:
                og_data[key] = val

        price_texts = []
        for el in soup.find_all(string=re.compile(r'[\d,]+원')):
            text = el.strip()
            if text and len(text) < 100:
                price_texts.append(text)

        main = soup.find("main") or soup.find("article") or soup.find("div", {"id": re.compile(r"content|product|detail", re.I)}) or soup.find("div", {"class": re.compile(r"content|product|detail", re.I)})
        body_text = main.get_text(separator="\n", strip=True) if main else (soup.body.get_text(separator="\n", strip=True) if soup.body else "")

        lines = [l.strip() for l in body_text.split("\n") if l.strip() and len(l.strip()) > 1]
        body_text = "\n".join(lines[:150])

        result = f"[상품 페이지 정보]\n제목: {title}\n"
        if meta_desc:
            result += f"설명: {meta_desc}\n"
        for k, v in og_data.items():
            result += f"{k}: {v}\n"
        if price_texts:
            result += f"가격 정보: {', '.join(price_texts[:5])}\n"
        result += f"\n[상세 내용]\n{body_text[:3000]}"
        return result

    except Exception as e:
        return f"(URL에서 정보를 가져오지 못했습니다: {e})"


def create_storyboard_ppt(sb_data, topic, tone):
    """스토리보드 데이터를 PPT 파일로 변환"""
    SLIDE_W = Cm(33.87)
    SLIDE_H = Cm(19.05)
    C_DARK = RGBColor(0x1A, 0x1A, 0x2E)
    C_ACCENT = RGBColor(0x8B, 0x5C, 0xF6)
    C_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    C_BLACK = RGBColor(0x33, 0x33, 0x33)
    C_GRAY = RGBColor(0x88, 0x88, 0x88)

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    def add_header(slide, text):
        rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Cm(0), Cm(0), SLIDE_W, Cm(2))
        rect.fill.solid()
        rect.fill.fore_color.rgb = C_DARK
        rect.line.fill.background()
        tf = rect.text_frame
        tf.margin_left = Cm(1)
        tf.margin_top = Cm(0.3)
        p = tf.paragraphs[0]
        p.text = text
        p.font.size = Pt(20)
        p.font.bold = True
        p.font.color.rgb = C_WHITE

    def set_cell(cell, text, size=9, bold=False, color=C_BLACK, bg=None, align=PP_ALIGN.LEFT):
        cell.text = ""
        tf = cell.text_frame
        tf.word_wrap = True
        for i, line in enumerate(str(text).split("\n")):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = line
            p.font.size = Pt(size)
            p.font.bold = bold
            p.font.color.rgb = color
            p.alignment = align
            p.space_before = Pt(2)
            p.space_after = Pt(2)
        if bg:
            from pptx.oxml.ns import qn
            tcPr = cell._tc.get_or_add_tcPr()
            solidFill = tcPr.makeelement(qn('a:solidFill'), {})
            srgbClr = solidFill.makeelement(qn('a:srgbClr'), {'val': bg})
            solidFill.append(srgbClr)
            tcPr.append(solidFill)
        cell.vertical_anchor = MSO_ANCHOR.TOP

    # ── 슬라이드 1: 표지 ──
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg_rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Cm(0), Cm(0), SLIDE_W, SLIDE_H)
    bg_rect.fill.solid()
    bg_rect.fill.fore_color.rgb = C_DARK
    bg_rect.line.fill.background()

    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Cm(14.5), Cm(5.5), Cm(5), Cm(0.3))
    bar.fill.solid()
    bar.fill.fore_color.rgb = C_ACCENT
    bar.line.fill.background()

    title = sb_data.get("title", topic)
    txBox = slide.shapes.add_textbox(Cm(3), Cm(6.5), Cm(28), Cm(3))
    tf = txBox.text_frame
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = C_WHITE
    p.alignment = PP_ALIGN.CENTER

    txBox2 = slide.shapes.add_textbox(Cm(3), Cm(10), Cm(28), Cm(1.5))
    tf2 = txBox2.text_frame
    p2 = tf2.paragraphs[0]
    p2.text = f"{sb_data.get('platform', '')}  |  {sb_data.get('total_duration', '')}  |  {sb_data.get('hosts', '')}  |  {tone}"
    p2.font.size = Pt(14)
    p2.font.color.rgb = RGBColor(0xAA, 0xAA, 0xBB)
    p2.alignment = PP_ALIGN.CENTER

    txBox3 = slide.shapes.add_textbox(Cm(3), Cm(14), Cm(28), Cm(1))
    tf3 = txBox3.text_frame
    p3 = tf3.paragraphs[0]
    p3.text = f"LIVE COMMERCE STORYBOARD  |  {datetime.now().strftime('%Y.%m.%d')}"
    p3.font.size = Pt(11)
    p3.font.color.rgb = C_GRAY
    p3.alignment = PP_ALIGN.CENTER

    # ── 슬라이드 2: 전체 플로우 ──
    scenes = sb_data.get("scenes", [])
    slide2 = prs.slides.add_slide(prs.slide_layouts[6])
    add_header(slide2, "전체 플로우 — (실제 상황에 따라 상이할 수 있음)")

    headers = ["구간", "섹션", "시간", "핵심 내용"]
    rows_data = [headers]
    for s in scenes:
        rows_data.append([
            str(s.get("scene_number", "")),
            s.get("section", ""),
            s.get("duration", ""),
            s.get("product_info", s.get("narration", ""))[:40],
        ])

    tbl_shape = slide2.shapes.add_table(len(rows_data), 4, Cm(1.5), Cm(3), Cm(31), Cm(min(14, 1.2 * len(rows_data))))
    table = tbl_shape.table
    for i, w in enumerate([Cm(3), Cm(5), Cm(5), Cm(18)]):
        table.columns[i].width = w

    for r, row in enumerate(rows_data):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            if r == 0:
                set_cell(cell, val, size=10, bold=True, color=C_WHITE, bg='1A1A2E', align=PP_ALIGN.CENTER)
            else:
                bg_hex = 'F5F5F5' if r % 2 == 0 else None
                set_cell(cell, val, size=9, align=PP_ALIGN.CENTER if c < 3 else PP_ALIGN.LEFT, bg=bg_hex)

    # ── 슬라이드 3~: 구간별 상세 ──
    for s in scenes:
        sl = prs.slides.add_slide(prs.slide_layouts[6])
        add_header(sl, f"방송 정보 — {s.get('section', '')}  ({s.get('duration', '')})")

        detail_rows = [
            ["항목", "내용"],
            ["쇼호스트 대본", s.get("host_script", s.get("narration", ""))],
            ["화면 표시", s.get("screen_display", s.get("screen_description", ""))],
            ["제품/혜택 정보", s.get("product_info", s.get("subtitle", ""))],
            ["연출 지시", s.get("direction_note", s.get("camera_note", ""))],
            ["시청자 유도", s.get("viewer_action", s.get("bgm_sfx", ""))],
        ]

        tbl = sl.shapes.add_table(len(detail_rows), 2, Cm(1.5), Cm(3.5), Cm(31), Cm(13))
        tbl_table = tbl.table
        tbl_table.columns[0].width = Cm(7)
        tbl_table.columns[1].width = Cm(24)

        for r, row in enumerate(detail_rows):
            for c, val in enumerate(row):
                cell = tbl_table.cell(r, c)
                if r == 0:
                    set_cell(cell, val, size=10, bold=True, color=C_WHITE, bg='1A1A2E', align=PP_ALIGN.CENTER)
                elif c == 0:
                    set_cell(cell, val, size=10, bold=True, color=RGBColor(0x1A, 0x1A, 0x2E), bg='E8EEF7', align=PP_ALIGN.LEFT)
                else:
                    set_cell(cell, val, size=10, bg='FFFFFF')

    # ── 마지막 슬라이드: 라이브 컨셉 & 핵심 혜택 ──
    sl_last = prs.slides.add_slide(prs.slide_layouts[6])
    add_header(sl_last, "라이브 컨셉 & 핵심 혜택")

    concept = sb_data.get("live_concept", "")
    benefits = sb_data.get("key_benefits", [])
    benefits_text = "\n".join([f"  {i+1}. {b}" for i, b in enumerate(benefits)])
    hosts_info = sb_data.get("hosts", "")

    info_rows = [
        ["항목", "내용"],
        ["라이브 컨셉", concept],
        ["핵심 혜택", benefits_text],
        ["진행자", hosts_info],
    ]

    tbl_last = sl_last.shapes.add_table(len(info_rows), 2, Cm(1.5), Cm(3.5), Cm(31), Cm(10))
    tbl_last_table = tbl_last.table
    tbl_last_table.columns[0].width = Cm(7)
    tbl_last_table.columns[1].width = Cm(24)

    for r, row in enumerate(info_rows):
        for c, val in enumerate(row):
            cell = tbl_last_table.cell(r, c)
            if r == 0:
                set_cell(cell, val, size=10, bold=True, color=C_WHITE, bg='1A1A2E', align=PP_ALIGN.CENTER)
            elif c == 0:
                set_cell(cell, val, size=10, bold=True, color=RGBColor(0x1A, 0x1A, 0x2E), bg='E8EEF7')
            else:
                set_cell(cell, val, size=10)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ─────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────
st.set_page_config(page_title="STUDIO", page_icon="🎬", layout="wide")

# ─────────────────────────────────────────
# 3D 모션 프리미엄 CSS
# ─────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&family=Inter:wght@300;400;500;600;700;800;900&display=swap');

    /* ── 전역 ── */
    .stApp {
        background: #08080f;
        font-family: 'Noto Sans KR', 'Inter', sans-serif;
    }
    header[data-testid="stHeader"] { background: transparent !important; }
    h1, h2, h3, h4, p, span, label, div {
        font-family: 'Noto Sans KR', 'Inter', sans-serif !important;
    }

    /* ── 배경 그리드 ── */
    .bg-grid {
        position: fixed;
        top: 0; left: 0;
        width: 100%; height: 100%;
        pointer-events: none;
        z-index: 0;
        background-image:
            linear-gradient(rgba(138, 92, 246, 0.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(138, 92, 246, 0.03) 1px, transparent 1px);
        background-size: 60px 60px;
    }

    /* ── 글로우 오브 ── */
    .glow-orb {
        position: fixed;
        border-radius: 50%;
        filter: blur(80px);
        pointer-events: none;
        z-index: 0;
    }
    .glow-orb-1 {
        top: -10%; right: -5%;
        width: 500px; height: 500px;
        background: rgba(138, 92, 246, 0.08);
        animation: orbMove1 20s ease-in-out infinite;
    }
    .glow-orb-2 {
        bottom: -15%; left: -10%;
        width: 600px; height: 600px;
        background: rgba(59, 130, 246, 0.06);
        animation: orbMove2 25s ease-in-out infinite;
    }
    .glow-orb-3 {
        top: 40%; right: 20%;
        width: 300px; height: 300px;
        background: rgba(6, 182, 212, 0.05);
        animation: orbMove3 18s ease-in-out infinite;
    }
    @keyframes orbMove1 {
        0%, 100% { transform: translate(0, 0) scale(1); }
        33% { transform: translate(-50px, 30px) scale(1.1); }
        66% { transform: translate(30px, -20px) scale(0.9); }
    }
    @keyframes orbMove2 {
        0%, 100% { transform: translate(0, 0) scale(1); }
        50% { transform: translate(40px, -40px) scale(1.15); }
    }
    @keyframes orbMove3 {
        0%, 100% { transform: translate(0, 0); }
        50% { transform: translate(-30px, 30px); }
    }

    /* ── 히어로 ── */
    .hero-3d {
        perspective: 1200px;
        margin-bottom: 2.5rem;
    }
    .hero-inner {
        background: linear-gradient(135deg,
            rgba(138, 92, 246, 0.08) 0%,
            rgba(59, 130, 246, 0.06) 50%,
            rgba(6, 182, 212, 0.04) 100%);
        border: 1px solid rgba(138, 92, 246, 0.15);
        border-radius: 24px;
        padding: 3rem 3.5rem;
        position: relative;
        overflow: hidden;
        transform: rotateX(2deg);
        transition: transform 0.8s cubic-bezier(0.23, 1, 0.32, 1);
        box-shadow:
            0 25px 80px rgba(138, 92, 246, 0.1),
            0 0 0 1px rgba(255, 255, 255, 0.03) inset;
        animation: heroFloat 8s ease-in-out infinite;
        backdrop-filter: blur(40px);
        -webkit-backdrop-filter: blur(40px);
    }
    .hero-inner:hover {
        transform: rotateX(0deg) translateY(-3px);
    }
    @keyframes heroFloat {
        0%, 100% { transform: rotateX(2deg) translateY(0); }
        50% { transform: rotateX(0.5deg) translateY(-6px); }
    }
    .hero-inner::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(138, 92, 246, 0.3), rgba(59, 130, 246, 0.3), transparent);
    }
    .hero-inner::after {
        content: '';
        position: absolute;
        top: -50%; right: -20%;
        width: 400px; height: 400px;
        background: radial-gradient(circle, rgba(138, 92, 246, 0.1) 0%, transparent 70%);
        animation: pulseGlow 6s ease-in-out infinite;
    }
    @keyframes pulseGlow {
        0%, 100% { opacity: 0.3; transform: scale(1); }
        50% { opacity: 0.8; transform: scale(1.3); }
    }
    .hero-label {
        color: #8B5CF6;
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 3px;
        margin-bottom: 0.8rem;
        position: relative;
        z-index: 1;
    }
    .hero-title {
        color: #FFFFFF;
        font-size: 2.5rem;
        font-weight: 900;
        letter-spacing: -1px;
        margin: 0;
        position: relative;
        z-index: 1;
        line-height: 1.2;
    }
    .hero-gradient {
        background: linear-gradient(135deg, #8B5CF6, #3B82F6, #06B6D4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        background-size: 200% 200%;
        animation: gradientFlow 5s ease infinite;
    }
    @keyframes gradientFlow {
        0%, 100% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
    }
    .hero-sub {
        color: #7777aa;
        font-size: 1rem;
        margin-top: 0.8rem;
        font-weight: 300;
        position: relative;
        z-index: 1;
    }

    /* ── 네비게이션 카드 (사이드바 대용) ── */
    .nav-card {
        background: rgba(255, 255, 255, 0.02);
        backdrop-filter: blur(20px);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 16px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        gap: 14px;
        cursor: pointer;
        transition: all 0.4s cubic-bezier(0.23, 1, 0.32, 1);
        position: relative;
        overflow: hidden;
    }
    .nav-card:hover {
        border-color: rgba(138, 92, 246, 0.3);
        transform: translateX(6px);
        box-shadow: 0 8px 30px rgba(138, 92, 246, 0.1);
    }
    .nav-card.active {
        border-color: rgba(138, 92, 246, 0.4);
        background: rgba(138, 92, 246, 0.06);
        box-shadow: 0 0 25px rgba(138, 92, 246, 0.08);
    }
    .nav-icon {
        width: 42px; height: 42px;
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.2rem;
        flex-shrink: 0;
    }
    .nav-icon-purple { background: linear-gradient(135deg, rgba(138, 92, 246, 0.2), rgba(138, 92, 246, 0.05)); }
    .nav-icon-blue { background: linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(59, 130, 246, 0.05)); }
    .nav-icon-cyan { background: linear-gradient(135deg, rgba(6, 182, 212, 0.2), rgba(6, 182, 212, 0.05)); }
    .nav-title {
        color: #d0d0e0;
        font-size: 0.95rem;
        font-weight: 600;
    }
    .nav-desc {
        color: #666688;
        font-size: 0.75rem;
        font-weight: 300;
    }
    .nav-badge {
        margin-left: auto;
        font-size: 0.65rem;
        padding: 3px 8px;
        border-radius: 6px;
        font-weight: 600;
    }
    .badge-active {
        background: rgba(138, 92, 246, 0.15);
        color: #8B5CF6;
    }
    .badge-soon {
        background: rgba(255, 255, 255, 0.05);
        color: #555577;
    }

    /* ── 글래스 카드 ── */
    .glass-card {
        background: rgba(255, 255, 255, 0.02);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 20px;
        padding: 2rem;
        margin-bottom: 1.2rem;
        transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1);
        position: relative;
        overflow: hidden;
    }
    .glass-card::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent);
    }
    .glass-card:hover {
        border-color: rgba(138, 92, 246, 0.2);
        transform: translateY(-2px);
        box-shadow: 0 20px 50px rgba(0, 0, 0, 0.2);
    }

    /* ── 스텝 인디케이터 ── */
    .step-indicator {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 1.5rem;
    }
    .step-dot {
        width: 32px; height: 32px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 800;
        font-size: 0.85rem;
        color: white;
        background: linear-gradient(135deg, #8B5CF6, #6D28D9);
        box-shadow: 0 4px 15px rgba(138, 92, 246, 0.3);
    }
    .step-text {
        color: #d0d0e0;
        font-size: 1.05rem;
        font-weight: 700;
    }

    /* ── 입력 필드 ── */
    .stTextInput input, .stTextArea textarea {
        background: rgba(255, 255, 255, 0.03) !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 14px !important;
        color: #e0e0f0 !important;
        transition: all 0.4s ease !important;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: #8B5CF6 !important;
        box-shadow: 0 0 0 2px rgba(138, 92, 246, 0.15), 0 0 30px rgba(138, 92, 246, 0.08) !important;
    }

    [data-testid="stSelectbox"] > div > div {
        background: rgba(255, 255, 255, 0.03) !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 14px !important;
        color: #e0e0f0 !important;
    }

    label, .stTextInput label, .stSelectbox label,
    [data-testid="stWidgetLabel"] p,
    .stMarkdown p, .stMarkdown li {
        color: #9999bb !important;
    }

    /* ── 버튼 3D ── */
    .stButton > button {
        background: linear-gradient(135deg, #8B5CF6, #6D28D9) !important;
        color: white !important;
        border: none !important;
        padding: 0.85rem 2.5rem !important;
        font-size: 1rem !important;
        font-weight: 700 !important;
        border-radius: 14px !important;
        transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1) !important;
        box-shadow:
            0 6px 20px rgba(138, 92, 246, 0.25),
            0 0 40px rgba(138, 92, 246, 0.08);
        transform: perspective(600px) rotateX(0deg);
        position: relative;
        overflow: hidden;
    }
    .stButton > button::before {
        content: '';
        position: absolute;
        top: 0; left: -100%;
        width: 100%; height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
        transition: left 0.6s;
    }
    .stButton > button:hover::before {
        left: 100%;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #A78BFA, #7C3AED) !important;
        transform: perspective(600px) rotateX(-4deg) translateY(-4px) !important;
        box-shadow:
            0 15px 40px rgba(138, 92, 246, 0.35),
            0 0 60px rgba(138, 92, 246, 0.12) !important;
    }
    .stButton > button:active {
        transform: perspective(600px) rotateX(2deg) translateY(0) !important;
        box-shadow: 0 4px 15px rgba(138, 92, 246, 0.2) !important;
    }

    /* 다운로드 버튼 */
    .stDownloadButton > button {
        background: linear-gradient(135deg, #06B6D4, #0891B2) !important;
        color: white !important;
        border: none !important;
        padding: 0.85rem 2.5rem !important;
        font-size: 1rem !important;
        font-weight: 700 !important;
        border-radius: 14px !important;
        box-shadow: 0 6px 20px rgba(6, 182, 212, 0.25);
        transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1) !important;
    }
    .stDownloadButton > button:hover {
        transform: perspective(600px) rotateX(-4deg) translateY(-4px) !important;
        box-shadow: 0 15px 40px rgba(6, 182, 212, 0.35) !important;
    }

    /* ── 파일 업로더 ── */
    [data-testid="stFileUploader"] {
        background: rgba(138, 92, 246, 0.03);
        border: 2px dashed rgba(138, 92, 246, 0.15);
        border-radius: 16px;
        padding: 1.8rem;
        transition: all 0.5s ease;
    }
    [data-testid="stFileUploader"]:hover {
        border-color: rgba(138, 92, 246, 0.4);
        background: rgba(138, 92, 246, 0.06);
        box-shadow: 0 0 40px rgba(138, 92, 246, 0.06);
    }

    /* ── Expander ── */
    .streamlit-expanderHeader {
        background: rgba(255, 255, 255, 0.03) !important;
        border-radius: 14px !important;
        color: #d0d0e0 !important;
        font-weight: 600 !important;
        border: 1px solid rgba(255, 255, 255, 0.05) !important;
        transition: all 0.3s ease !important;
    }
    .streamlit-expanderHeader:hover {
        background: rgba(138, 92, 246, 0.06) !important;
        border-color: rgba(138, 92, 246, 0.15) !important;
        transform: translateX(4px);
    }
    .streamlit-expanderContent {
        background: rgba(255, 255, 255, 0.015) !important;
        border: 1px solid rgba(255, 255, 255, 0.03) !important;
        border-radius: 0 0 14px 14px !important;
    }

    /* ── 알림 ── */
    .stAlert {
        border-radius: 14px !important;
        animation: fadeSlideIn 0.6s cubic-bezier(0.23, 1, 0.32, 1);
    }
    @keyframes fadeSlideIn {
        from { opacity: 0; transform: translateY(15px) scale(0.98); }
        to { opacity: 1; transform: translateY(0) scale(1); }
    }

    /* ── 구분선 ── */
    hr { border-color: rgba(255, 255, 255, 0.04) !important; }

    /* ── 스크롤바 ── */
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb {
        background: linear-gradient(180deg, #8B5CF6, #3B82F6);
        border-radius: 3px;
    }

    /* ── 푸터 ── */
    .footer-3d {
        text-align: center;
        padding: 3rem 0 2rem;
        color: #333355;
        font-size: 0.75rem;
        letter-spacing: 2px;
        text-transform: uppercase;
    }
</style>

<!-- 배경 효과 -->
<div class="bg-grid"></div>
<div class="glow-orb glow-orb-1"></div>
<div class="glow-orb glow-orb-2"></div>
<div class="glow-orb glow-orb-3"></div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────
# 히어로 헤더
# ─────────────────────────────────────────
st.markdown("""
<div class="hero-3d">
    <div class="hero-inner">
        <p class="hero-label">LIVE COMMERCE AUTOMATION</p>
        <p class="hero-title">
            <span class="hero-gradient">STUDIO</span>
        </p>
        <p class="hero-sub">
            제품 소개서를 업로드하면 AI가 라이브커머스 스토리보드 PPT를 자동으로 생성합니다.
        </p>
    </div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────
# 메뉴 네비게이션
# ─────────────────────────────────────────
st.markdown("""
<div style="display: flex; gap: 12px; margin-bottom: 2rem;">
    <div class="nav-card active" style="flex:1;">
        <div class="nav-icon nav-icon-purple">🎬</div>
        <div>
            <div class="nav-title">스토리보드 PPT</div>
            <div class="nav-desc">제품 소개서 → AI 스토리보드</div>
        </div>
        <span class="nav-badge badge-active">ACTIVE</span>
    </div>
    <div class="nav-card" style="flex:1; opacity: 0.5;">
        <div class="nav-icon nav-icon-blue">🎨</div>
        <div>
            <div class="nav-title">배너 자동 생성</div>
            <div class="nav-desc">Figma 연동 배너 제작</div>
        </div>
        <span class="nav-badge badge-soon">SOON</span>
    </div>
    <div class="nav-card" style="flex:1; opacity: 0.5;">
        <div class="nav-icon nav-icon-cyan">📊</div>
        <div>
            <div class="nav-title">콘텐츠 분석</div>
            <div class="nav-desc">성과 분석 & 리포트</div>
        </div>
        <span class="nav-badge badge-soon">SOON</span>
    </div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────
# 스토리보드 생성 메인
# ─────────────────────────────────────────
col_left, col_gap, col_right = st.columns([5, 0.3, 5])

with col_left:
    # STEP 1: 기본 정보
    st.markdown("""
    <div class="step-indicator">
        <div class="step-dot">1</div>
        <div class="step-text">기본 정보</div>
    </div>
    """, unsafe_allow_html=True)

    topic = st.text_input("라이브 주제 / 브랜드명", placeholder="예: 닥터지 신제품 런칭, 봄 시즌 뷰티 프로모션")

    style = st.selectbox("톤앤매너", ["밝고 친근한", "전문적이고 신뢰감 있는", "활기차고 에너지 넘치는", "고급스럽고 세련된"])

    st.markdown("<br>", unsafe_allow_html=True)

    # STEP 2: 방송 정보
    st.markdown("""
    <div class="step-indicator">
        <div class="step-dot">2</div>
        <div class="step-text">방송 정보</div>
    </div>
    """, unsafe_allow_html=True)

    broadcast_platform = st.selectbox(
        "방송 플랫폼",
        ["카카오쇼핑라이브", "네이버 쇼핑라이브", "쿠팡 라이브", "SSG 라이브", "11번가 라이브", "기타"],
        key="bc_platform",
    )
    if broadcast_platform == "기타":
        broadcast_platform = st.text_input("플랫폼명 직접 입력", key="bc_plat_custom")

    hosts = st.text_input("쇼호스트", placeholder="예: 이소유 & 조을희", key="bc_hosts")

    col_e, col_f = st.columns(2)
    with col_e:
        broadcast_date = st.date_input("방송 날짜", key="bc_date")
    with col_f:
        broadcast_time = st.text_input("방송 시간", placeholder="예: 20:30~21:30", key="bc_time")

    broadcast_location = st.text_input("방송 장소", placeholder="예: 서울 강남구 역삼로 216 B2", key="bc_location")
    broadcast_duration = st.selectbox("방송 시간(분)", ["30분", "60분", "90분", "120분"], index=1, key="bc_duration")

    if broadcast_date:
        date_str = broadcast_date.strftime("%Y년 %m월 %d일")
        datetime_str = f"{date_str} {broadcast_time}" if broadcast_time else date_str
    else:
        datetime_str = ""

    st.markdown("<br>", unsafe_allow_html=True)

    # STEP 3: 제품 소개서
    st.markdown("""
    <div class="step-indicator">
        <div class="step-dot">3</div>
        <div class="step-text">제품 소개서 업로드</div>
    </div>
    """, unsafe_allow_html=True)

    uploaded_excel = st.file_uploader(
        "엑셀 파일 (.xlsx)을 업로드하면 상품 정보 + URL을 자동으로 읽어옵니다",
        type=["xlsx"],
    )

    with st.expander("URL 직접 입력 (엑셀 없이 사용할 때)"):
        manual_urls = st.text_area(
            "상품 URL을 한 줄에 하나씩 입력",
            placeholder="https://smartstore.naver.com/...\nhttps://www.coupang.com/...",
            height=100,
            key="sb_urls_manual",
        )


with col_right:
    # STEP 4: 생성
    st.markdown("""
    <div class="step-indicator">
        <div class="step-dot">4</div>
        <div class="step-text">스토리보드 생성</div>
    </div>
    """, unsafe_allow_html=True)

    # 입력 미리보기
    if topic:
        st.markdown(f"""
        <div class="glass-card">
            <div style="color: #8B5CF6; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 1rem;">입력 정보 확인</div>
            <div style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);">
                <span style="color: #666688; font-size: 0.85rem;">주제</span>
                <span style="color: #d0d0e0; font-size: 0.85rem; font-weight: 500;">{topic}</span>
            </div>
            <div style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);">
                <span style="color: #666688; font-size: 0.85rem;">톤앤매너</span>
                <span style="color: #d0d0e0; font-size: 0.85rem;">{style}</span>
            </div>
            <div style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);">
                <span style="color: #666688; font-size: 0.85rem;">방송 플랫폼</span>
                <span style="color: #d0d0e0; font-size: 0.85rem;">{broadcast_platform}</span>
            </div>
            <div style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);">
                <span style="color: #666688; font-size: 0.85rem;">쇼호스트</span>
                <span style="color: #d0d0e0; font-size: 0.85rem;">{hosts or '(미입력)'}</span>
            </div>
            <div style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);">
                <span style="color: #666688; font-size: 0.85rem;">방송 일시</span>
                <span style="color: #d0d0e0; font-size: 0.85rem;">{datetime_str or '(미입력)'}</span>
            </div>
            <div style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);">
                <span style="color: #666688; font-size: 0.85rem;">방송 장소</span>
                <span style="color: #d0d0e0; font-size: 0.85rem;">{broadcast_location or '(미입력)'}</span>
            </div>
            <div style="display: flex; justify-content: space-between; padding: 6px 0;">
                <span style="color: #666688; font-size: 0.85rem;">소개서</span>
                <span style="color: #d0d0e0; font-size: 0.85rem;">{'업로드 완료' if uploaded_excel else '없음'}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    generate_btn = st.button(
        "스토리보드 생성하기",
        use_container_width=True,
        disabled=not topic,
    )

    if not topic:
        st.caption("콘텐츠 주제를 먼저 입력해주세요.")


# ─────────────────────────────────────────
# 생성 프로세스
# ─────────────────────────────────────────
if generate_btn and topic:
    st.markdown("---")

    excel_data = None
    urls_to_fetch = []
    product_info = ""

    # 1) 엑셀 파싱
    if uploaded_excel:
        with st.spinner("제품 소개서 분석 중..."):
            excel_data = parse_excel_products(uploaded_excel.getvalue())
            urls_to_fetch = excel_data["urls"]
            st.success(f"제품 소개서 분석 완료! (URL {len(urls_to_fetch)}개 발견)")

    # 2) 직접 입력 URL
    if manual_urls.strip():
        extra = [u.strip() for u in manual_urls.strip().split("\n") if u.strip()]
        for u in extra:
            if u not in urls_to_fetch:
                urls_to_fetch.append(u)

    # 3) URL에서 상품 정보 수집
    all_fetched = []
    if urls_to_fetch:
        with st.spinner(f"상품 페이지 {len(urls_to_fetch)}개에서 정보 수집 중..."):
            for i, url in enumerate(urls_to_fetch):
                info = fetch_product_info(url)
                if "가져오지 못했습니다" in info:
                    st.warning(f"URL {i+1} 실패: {url}")
                else:
                    all_fetched.append(f"[상품 {i+1}] {url}\n{info}")
        if all_fetched:
            st.success(f"상품 정보 {len(all_fetched)}개 수집 완료!")

    # 4) 상품 정보 조합
    parts = []
    if excel_data and excel_data["text"]:
        parts.append(f"[제품 소개서 내용]\n{excel_data['text']}")
    if all_fetched:
        parts.append("\n\n---\n\n".join(all_fetched))
    product_info = "\n\n===\n\n".join(parts)

    # 5) AI 스토리보드 생성
    with st.spinner("AI가 스토리보드를 구성하고 있습니다..."):
        product_context = ""
        if product_info:
            product_context = f"""

아래는 제품 소개서와 실제 상품 페이지에서 가져온 정보야.
각 상품의 실제 이름, 가격, 특징, 장점, 셀링포인트 등을 대본과 자막에 구체적으로 반영해줘.
상품이 여러 개면 각 상품을 자연스럽게 소개하는 장면을 포함해줘.

{product_info}
"""

        sb_prompt = f"""다음 정보를 기반으로 라이브커머스 방송 스토리보드를 만들어줘.

주제/브랜드: {topic}
톤앤매너: {style}

방송 정보:
- 플랫폼: {broadcast_platform}
- 쇼호스트: {hosts or '(미정)'}
- 방송 일시: {datetime_str or '(미정)'}
- 방송 장소: {broadcast_location or '(미정)'}
- 방송 시간: {broadcast_duration}
{product_context}
반드시 아래 JSON 형식으로만 응답해줘 (다른 텍스트 없이 JSON만):

{{
  "title": "라이브 방송 제목",
  "platform": "{broadcast_platform}",
  "total_duration": "{broadcast_duration}",
  "hosts": "{hosts or '(미정)'}",
  "scenes": [
    {{
      "scene_number": 1,
      "duration": "0~2분",
      "section": "오프닝",
      "host_script": "쇼호스트 대본 (대화체, 자연스럽게)",
      "screen_display": "화면에 표시할 내용 (배너, 가격표, 자막 등)",
      "product_info": "이 구간에서 소개할 제품/혜택 정보",
      "direction_note": "연출 지시 (카메라, 제품 클로즈업, 시연 등)",
      "viewer_action": "시청자 유도 액션 (댓글, 좋아요, 구매 등)"
    }}
  ],
  "live_concept": "라이브 컨셉 한 줄 요약",
  "key_benefits": ["핵심 혜택1", "핵심 혜택2", "핵심 혜택3"]
}}

라이브커머스 방송 플로우에 맞게 구성해줘:
1. 오프닝 (인사, 방송 소개)
2. 브랜드/제품 소개
3. 제품별 상세 소개 (각 제품마다 별도 장면)
4. 혜택/가격 소개
5. Q&A / 시청자 소통
6. 클로징 (이벤트, 마무리)

방송 시간({broadcast_duration})에 맞게 장면 수와 시간 배분을 조절해줘.
쇼호스트 대본은 실제 방송에서 바로 쓸 수 있도록 대화체로 자연스럽게 작성해줘."""

        sb_result = call_claude(sb_prompt)

        try:
            json_match = re.search(r'\{[\s\S]*\}', sb_result)
            if json_match:
                sb_data = json.loads(json_match.group())
            else:
                st.error("스토리보드 생성에 실패했습니다. 다시 시도해주세요.")
                st.stop()
        except json.JSONDecodeError:
            st.error("스토리보드 데이터 파싱에 실패했습니다. 다시 시도해주세요.")
            st.code(sb_result)
            st.stop()

    # 6) 미리보기
    st.markdown(f"""
    <div class="glass-card">
        <div style="color: #8B5CF6; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 0.5rem;">LIVE COMMERCE STORYBOARD</div>
        <div style="color: #e0e0f0; font-size: 1.3rem; font-weight: 800;">{sb_data.get('title', topic)}</div>
        <div style="color: #666688; font-size: 0.85rem; margin-top: 4px;">{sb_data.get('platform', broadcast_platform)} | {sb_data.get('total_duration', broadcast_duration)} | {sb_data.get('hosts', hosts)} | {len(sb_data.get('scenes', []))}개 구간</div>
    </div>
    """, unsafe_allow_html=True)

    # 컨셉 & 핵심 혜택
    live_concept = sb_data.get("live_concept", "")
    key_benefits = sb_data.get("key_benefits", [])
    if live_concept or key_benefits:
        with st.expander("라이브 컨셉 & 핵심 혜택", expanded=True):
            if live_concept:
                st.markdown(f"**컨셉:** {live_concept}")
            if key_benefits:
                st.markdown("**핵심 혜택:**")
                for b in key_benefits:
                    st.markdown(f"- {b}")

    scenes = sb_data.get("scenes", [])
    for scene in scenes:
        with st.expander(f"구간 {scene['scene_number']}  —  {scene.get('section', '')}  ({scene.get('duration', '')})"):
            st.markdown(f"**쇼호스트 대본:**")
            st.markdown(scene.get('host_script', ''))
            st.markdown("---")
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**화면 표시:** {scene.get('screen_display', '')}")
                st.markdown(f"**제품/혜택 정보:** {scene.get('product_info', '')}")
            with col_b:
                st.markdown(f"**연출 지시:** {scene.get('direction_note', '')}")
                st.markdown(f"**시청자 유도:** {scene.get('viewer_action', '')}")

    # 7) PPT 다운로드
    with st.spinner("PPT 파일 생성 중..."):
        ppt_bytes = create_storyboard_ppt(sb_data, topic, style)

    st.success("라이브커머스 스토리보드 PPT가 생성되었습니다!")

    filename = f"라이브커머스_스토리보드_{topic[:15]}_{datetime.now().strftime('%Y%m%d_%H%M')}.pptx"
    st.download_button(
        label=f"PPT 다운로드  —  {filename}",
        data=ppt_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        use_container_width=True,
    )


# ─────────────────────────────────────────
# 푸터
# ─────────────────────────────────────────
st.markdown(
    '<div class="footer-3d">STUDIO v1.0 &nbsp;&middot;&nbsp; Powered by Claude AI &nbsp;&middot;&nbsp; '
    f'{datetime.now().strftime("%Y.%m.%d")}</div>',
    unsafe_allow_html=True,
)
