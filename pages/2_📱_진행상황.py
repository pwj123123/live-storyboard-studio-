"""모바일 친화 진행상황 페이지.

홈 화면에 추가(Add to Home Screen)로 PWA처럼 사용 가능.
나중에 iOS 네이티브 앱은 동일한 데이터(jobs.py)를 api.py 통해 읽음.
"""
from __future__ import annotations

import time
from datetime import datetime

import streamlit as st

import jobs as jobs_mod

st.set_page_config(
    page_title="진행상황",
    page_icon="📱",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="LIVE STUDIO">
<meta name="theme-color" content="#0F0F14">
<style>
    .stApp { background: #0F0F14; }
    .main .block-container { padding-top: 1rem; padding-bottom: 4rem; max-width: 480px; }
    [data-testid="stSidebar"] { display: none; }
    header[data-testid="stHeader"] { background: transparent !important; }

    h1, h2, h3, p, span, div, label {
        font-family: -apple-system, BlinkMacSystemFont, 'Noto Sans KR', sans-serif !important;
    }

    .page-title {
        color: #FFFFFF; font-size: 1.6rem; font-weight: 800;
        letter-spacing: -0.5px; margin-bottom: 0.2rem;
    }
    .page-sub { color: #6B7280; font-size: 0.85rem; margin-bottom: 1.5rem; }

    .job-card {
        background: #1A1A24;
        border: 1px solid #2A2A38;
        border-radius: 14px;
        padding: 16px 18px;
        margin-bottom: 12px;
    }
    .job-row { display: flex; justify-content: space-between; align-items: flex-start; }
    .job-title { color: #FFFFFF; font-weight: 700; font-size: 1rem; line-height: 1.3; }
    .job-meta { color: #6B7280; font-size: 0.78rem; margin-top: 4px; }
    .job-step { color: #B5B5C5; font-size: 0.85rem; margin-top: 10px; min-height: 1.2em; }

    .progress-bar-bg {
        width: 100%; height: 8px; background: #2A2A38;
        border-radius: 4px; margin-top: 12px; overflow: hidden;
    }
    .progress-bar-fill {
        height: 100%; background: linear-gradient(90deg, #8B5CF6, #06B6D4);
        border-radius: 4px; transition: width 0.5s ease;
    }
    .progress-bar-fill.done { background: #22C55E; }
    .progress-bar-fill.fail { background: #EF4444; }

    .status-badge {
        display: inline-block; padding: 3px 10px; border-radius: 8px;
        font-size: 0.68rem; font-weight: 700; letter-spacing: 0.5px;
        white-space: nowrap;
    }
    .status-running   { background: rgba(139,92,246,0.2);  color: #B299FF; }
    .status-completed { background: rgba(34,197,94,0.2);   color: #6EE7B7; }
    .status-failed    { background: rgba(239,68,68,0.2);   color: #FCA5A5; }
    .status-queued    { background: rgba(156,163,175,0.2); color: #D1D5DB; }

    .empty-state {
        text-align: center; padding: 4rem 1rem; color: #6B7280;
    }

    /* 토글 */
    .stCheckbox label { color: #B5B5C5 !important; }
</style>
""",
    unsafe_allow_html=True,
)


def _get_user_id() -> str:
    qs = st.query_params
    if "user" in qs:
        st.session_state["user_id"] = qs["user"]
    return st.session_state.get("user_id", "default")


user_id = _get_user_id()

st.markdown('<div class="page-title">📱 진행상황</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="page-sub">사용자: <code>{user_id}</code></div>',
    unsafe_allow_html=True,
)

col_a, col_b = st.columns([3, 1])
with col_a:
    auto_refresh = st.toggle("자동 새로고침 (3초)", value=True, key="auto_refresh")
with col_b:
    if st.button("🔄", help="수동 새로고침", use_container_width=True):
        st.rerun()

job_list = jobs_mod.list_jobs(user_id=user_id, limit=20)

if not job_list:
    st.markdown(
        '<div class="empty-state">아직 작업이 없습니다.<br>메인 페이지에서 스토리보드를 생성해보세요.</div>',
        unsafe_allow_html=True,
    )
else:
    has_running = False
    for job in job_list:
        status = job["status"]
        if status == jobs_mod.STATUS_RUNNING:
            has_running = True

        progress_pct = int(job.get("progress") or 0)
        bar_class = ""
        if status == jobs_mod.STATUS_COMPLETED:
            bar_class = "done"
        elif status == jobs_mod.STATUS_FAILED:
            bar_class = "fail"

        created = datetime.fromtimestamp(job["created_at"]).strftime("%m/%d %H:%M")
        title = job.get("title") or job.get("type", "작업")
        step = job.get("current_step") or ""
        step_idx = job.get("step_index") or 0
        total = job.get("total_steps") or 0
        step_counter = f" · {step_idx}/{total}" if total else ""

        if status == jobs_mod.STATUS_FAILED and job.get("error"):
            step = f"⚠️ {job['error'][:80]}"

        st.markdown(
            f"""
        <div class="job-card">
            <div class="job-row">
                <div class="job-title">{title}</div>
                <div class="status-badge status-{status}">{status.upper()}</div>
            </div>
            <div class="job-meta">{created}{step_counter} · {progress_pct}%</div>
            <div class="progress-bar-bg">
                <div class="progress-bar-fill {bar_class}" style="width: {progress_pct}%;"></div>
            </div>
            <div class="job-step">{step}</div>
        </div>
        """,
            unsafe_allow_html=True,
        )

    if auto_refresh and has_running:
        time.sleep(3)
        st.rerun()
