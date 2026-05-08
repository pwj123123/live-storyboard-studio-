"""FastAPI 서비스 — 향후 iOS Live Activity / 모바일 클라이언트가 호출할 진행상황 API.

개발 실행: uvicorn api:app --host 0.0.0.0 --port 8001 --reload

인증은 현재 X-User-Id 헤더 기반 (베타 단계). 추후 Supabase JWT 검증으로 교체.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import jobs

app = FastAPI(title="Live Storyboard Studio API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_user(x_user_id: Optional[str]) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    return x_user_id


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/jobs")
def api_list_jobs(
    x_user_id: Optional[str] = Header(default=None),
    limit: int = 20,
) -> dict:
    user = _require_user(x_user_id)
    return {"jobs": jobs.list_jobs(user_id=user, limit=limit)}


@app.get("/api/jobs/{job_id}")
def api_get_job(
    job_id: str,
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    user = _require_user(x_user_id)
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["user_id"] != user:
        raise HTTPException(status_code=403, detail="Forbidden")
    return job


@app.get("/api/jobs/{job_id}/progress")
def api_get_progress(job_id: str) -> dict:
    """Live Activity 폴링용 경량 엔드포인트.

    job_id는 푸시 토큰과 함께 클라이언트에 전달되는 비공개 식별자라고 가정한다.
    민감 정보(result, error)는 제외하고 진행률 위주로만 반환.
    """
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "current_step": job.get("current_step"),
        "step_index": job.get("step_index"),
        "total_steps": job.get("total_steps"),
        "title": job.get("title"),
        "updated_at": job.get("updated_at"),
    }
