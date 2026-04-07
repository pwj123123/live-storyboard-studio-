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
        max_tokens=8192,
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
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        resp = requests.get(url, headers=headers, timeout=20, verify=False)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or 'utf-8'

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
# CSS
# ─────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');

    .stApp {
        background: #0B0B12;
        font-family: 'Noto Sans KR', sans-serif;
    }
    header[data-testid="stHeader"] { background: transparent !important; }
    h1, h2, h3, h4, p, span, label, div {
        font-family: 'Noto Sans KR', sans-serif !important;
    }

    /* 헤더 */
    .header-bar {
        background: rgba(255,255,255,0.02);
        border-bottom: 1px solid rgba(255,255,255,0.06);
        padding: 1.2rem 2rem;
        margin: -1rem -1rem 2rem -1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .header-title {
        color: #fff;
        font-size: 1.2rem;
        font-weight: 800;
        letter-spacing: -0.5px;
    }
    .header-accent {
        color: #8B5CF6;
    }
    .header-nav {
        display: flex;
        gap: 24px;
    }
    .header-nav-item {
        color: #555;
        font-size: 0.8rem;
        font-weight: 500;
        padding: 6px 14px;
        border-radius: 8px;
        transition: all 0.3s;
    }
    .header-nav-active {
        color: #c0c0e0;
        background: rgba(138,92,246,0.1);
    }

    /* 섹션 */
    .section-label {
        color: #8B5CF6;
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 2px;
        margin-bottom: 0.8rem;
    }

    /* 입력 */
    .stTextInput input, .stTextArea textarea {
        background: rgba(255,255,255,0.04) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 10px !important;
        color: #e0e0f0 !important;
        transition: border-color 0.3s !important;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: #8B5CF6 !important;
        box-shadow: 0 0 0 1px rgba(138,92,246,0.2) !important;
    }
    [data-testid="stSelectbox"] > div > div {
        background: rgba(255,255,255,0.04) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 10px !important;
        color: #e0e0f0 !important;
    }
    [data-testid="stDateInput"] > div > div {
        background: rgba(255,255,255,0.04) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 10px !important;
    }
    [data-testid="stDateInput"] input { color: #e0e0f0 !important; }
    label, .stTextInput label, .stSelectbox label,
    [data-testid="stWidgetLabel"] p {
        color: #8888aa !important;
        font-size: 0.85rem !important;
    }
    .stMarkdown p, .stMarkdown li { color: #b0b0cc !important; }

    /* 버튼 */
    .stButton > button {
        background: #8B5CF6 !important;
        color: white !important;
        border: none !important;
        padding: 0.75rem 2rem !important;
        font-size: 0.95rem !important;
        font-weight: 700 !important;
        border-radius: 10px !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 4px 12px rgba(138,92,246,0.2);
    }
    .stButton > button:hover {
        background: #7C3AED !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 20px rgba(138,92,246,0.3) !important;
    }
    .stDownloadButton > button {
        background: #06B6D4 !important;
        color: white !important;
        border: none !important;
        padding: 0.75rem 2rem !important;
        font-size: 0.95rem !important;
        font-weight: 700 !important;
        border-radius: 10px !important;
        box-shadow: 0 4px 12px rgba(6,182,212,0.2);
        transition: all 0.3s ease !important;
    }
    .stDownloadButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 20px rgba(6,182,212,0.3) !important;
    }

    /* 업로더 */
    [data-testid="stFileUploader"] {
        background: rgba(255,255,255,0.02);
        border: 1px dashed rgba(255,255,255,0.1);
        border-radius: 10px;
        padding: 1.2rem;
    }
    [data-testid="stFileUploader"]:hover {
        border-color: rgba(138,92,246,0.3);
    }

    /* Expander */
    .streamlit-expanderHeader {
        background: rgba(255,255,255,0.03) !important;
        border-radius: 10px !important;
        color: #b0b0cc !important;
        font-weight: 500 !important;
    }
    .streamlit-expanderContent {
        background: rgba(255,255,255,0.015) !important;
        border-radius: 0 0 10px 10px !important;
    }

    /* 알림 */
    .stAlert { border-radius: 10px !important; }

    /* 구분선 */
    hr { border-color: rgba(255,255,255,0.05) !important; }

    /* 스크롤바 */
    ::-webkit-scrollbar { width: 4px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

    .footer-3d {
        text-align: center;
        padding: 2rem 0;
        color: #333;
        font-size: 0.7rem;
        letter-spacing: 2px;
    }
</style>
""", unsafe_allow_html=True)

# ─── 헤더 ───
st.markdown("""
<div class="header-bar">
    <div class="header-title"><span class="header-accent">LIVE</span> STUDIO</div>
    <div class="header-nav">
        <span class="header-nav-item header-nav-active">스토리보드</span>
        <span class="header-nav-item">배너 생성</span>
        <span class="header-nav-item">분석</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────
# 입력 폼
# ─────────────────────────────────────────

# 기본 정보
st.markdown('<div class="section-label">기본 정보</div>', unsafe_allow_html=True)

col1, col2 = st.columns([3, 1])
with col1:
    topic = st.text_input("라이브 주제 / 브랜드명", placeholder="예: 닥터지 신제품 런칭")
with col2:
    style = st.selectbox("톤앤매너", ["밝고 친근한", "전문적이고 신뢰감 있는", "활기차고 에너지 넘치는", "고급스럽고 세련된"])

st.markdown("---")

# 방송 정보
st.markdown('<div class="section-label">방송 정보</div>', unsafe_allow_html=True)

col_a, col_b, col_c = st.columns(3)
with col_a:
    broadcast_platform = st.selectbox(
        "플랫폼",
        ["카카오쇼핑라이브", "네이버 쇼핑라이브", "쿠팡 라이브", "SSG 라이브", "11번가 라이브", "기타"],
        key="bc_platform",
    )
    if broadcast_platform == "기타":
        broadcast_platform = st.text_input("플랫폼명", key="bc_plat_custom")
with col_b:
    hosts = st.text_input("쇼호스트", placeholder="예: 이소유 & 조을희", key="bc_hosts")
with col_c:
    broadcast_duration = st.selectbox("방송 시간", ["30분", "60분", "90분", "120분"], index=1, key="bc_duration")

col_d, col_e, col_f = st.columns(3)
with col_d:
    broadcast_date = st.date_input("방송 날짜", key="bc_date")
with col_e:
    broadcast_time = st.text_input("방송 시간대", placeholder="예: 20:30~21:30", key="bc_time")
with col_f:
    broadcast_location = st.text_input("방송 장소", placeholder="예: 강남구 역삼로 216", key="bc_location")

if broadcast_date:
    date_str = broadcast_date.strftime("%Y년 %m월 %d일")
    datetime_str = f"{date_str} {broadcast_time}" if broadcast_time else date_str
else:
    datetime_str = ""

st.markdown("---")

# 제품 소개서
st.markdown('<div class="section-label">제품 소개서</div>', unsafe_allow_html=True)

col_g, col_h = st.columns([1, 1])
with col_g:
    uploaded_excel = st.file_uploader(
        "엑셀 (.xlsx) 업로드 — 상품 정보 + URL 자동 추출",
        type=["xlsx"],
    )
with col_h:
    with st.expander("URL 직접 입력"):
        st.text_area(
            "한 줄에 하나씩",
            placeholder="https://smartstore.naver.com/...",
            height=80,
            key="sb_urls_manual",
        )

st.markdown("---")

# 생성 버튼
generate_btn = st.button(
    "스토리보드 생성하기",
    use_container_width=True,
    disabled=not topic,
)


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
    manual_urls = st.session_state.get("sb_urls_manual", "")
    if manual_urls.strip():
        extra = [u.strip() for u in manual_urls.strip().split("\n") if u.strip()]
        for u in extra:
            if u not in urls_to_fetch:
                urls_to_fetch.append(u)

    # 3) URL에서 상품 정보 수집 (실패해도 계속 진행)
    all_fetched = []
    failed_count = 0
    if urls_to_fetch:
        with st.spinner(f"상품 페이지 {len(urls_to_fetch)}개에서 정보 수집 중..."):
            for i, url in enumerate(urls_to_fetch):
                info = fetch_product_info(url)
                if "가져오지 못했습니다" in info:
                    failed_count += 1
                else:
                    all_fetched.append(f"[상품 {i+1}] {url}\n{info}")
        if all_fetched:
            st.success(f"상품 정보 {len(all_fetched)}개 수집 완료!")
        if failed_count > 0:
            st.info(f"URL {failed_count}개는 접근이 차단된 사이트입니다. 소개서 내용만으로 진행합니다.")

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

        sb_prompt = f"""라이브커머스 방송 스토리보드를 JSON으로 작성해.

[입력 정보]
주제/브랜드: {topic}
톤앤매너: {style}
플랫폼: {broadcast_platform}
쇼호스트: {hosts or '(미정)'}
방송 일시: {datetime_str or '(미정)'}
방송 장소: {broadcast_location or '(미정)'}
방송 시간: {broadcast_duration}
{product_context}

[규칙]
- 오프닝 → 브랜드소개 → 제품별소개 → 혜택/가격 → Q&A → 클로징 순서
- 방송 시간({broadcast_duration})에 맞게 장면 수 조절
- 쇼호스트 대본은 대화체로 자연스럽게
- 반드시 JSON만 출력. 설명 텍스트 금지.

```json
{{
  "title": "방송 제목",
  "platform": "{broadcast_platform}",
  "total_duration": "{broadcast_duration}",
  "hosts": "{hosts or '(미정)'}",
  "scenes": [
    {{
      "scene_number": 1,
      "duration": "시간범위",
      "section": "구간명",
      "host_script": "쇼호스트 대본",
      "screen_display": "화면 표시 내용",
      "product_info": "제품/혜택 정보",
      "direction_note": "연출 지시",
      "viewer_action": "시청자 유도"
    }}
  ],
  "live_concept": "컨셉 한줄",
  "key_benefits": ["혜택1", "혜택2", "혜택3"]
}}
```"""

        sb_result = call_claude(sb_prompt)

        # JSON 파싱 (여러 방법으로 시도)
        sb_data = None
        # 방법 1: ```json ... ``` 블록 찾기
        code_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', sb_result)
        if code_match:
            try:
                sb_data = json.loads(code_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 방법 2: 전체에서 { } 찾기
        if sb_data is None:
            try:
                # 가장 바깥쪽 { } 찾기
                start = sb_result.index('{')
                depth = 0
                end = start
                for i in range(start, len(sb_result)):
                    if sb_result[i] == '{':
                        depth += 1
                    elif sb_result[i] == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                sb_data = json.loads(sb_result[start:end])
            except (ValueError, json.JSONDecodeError):
                pass

        # 방법 3: 실패 시 AI에게 다시 요청
        if sb_data is None:
            with st.spinner("스토리보드를 다시 생성하고 있습니다..."):
                retry_prompt = f"아래 내용을 올바른 JSON 형식으로만 변환해줘. 다른 텍스트 없이 JSON만 출력해줘:\n\n{sb_result[:3000]}"
                retry_result = call_claude(retry_prompt)
                try:
                    retry_match = re.search(r'\{[\s\S]*\}', retry_result)
                    if retry_match:
                        sb_data = json.loads(retry_match.group())
                except (ValueError, json.JSONDecodeError):
                    pass

        if sb_data is None:
            st.error("스토리보드 생성에 실패했습니다. 다시 시도해주세요.")
            with st.expander("AI 응답 원문 (디버깅용)"):
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
