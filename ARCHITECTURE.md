# 서비스화 기본 틀

라이브커머스 스토리보드 스튜디오를 무료 베타 → 유료 SaaS → iOS Live Activity 컴패니언 앱으로
확장하기 위한 최소 골격.

## 파일 구조

```
app.py                       # Streamlit 메인 앱 (스토리보드 생성 워크플로우)
pages/
  └─ 2_📱_진행상황.py        # 모바일 친화 진행상황 페이지 (PWA 메타 포함)
jobs.py                      # SQLite 기반 작업 상태 모듈 (단일 진실 공급원)
api.py                       # FastAPI — iOS 클라이언트가 호출할 진행상황 API
data/jobs.db                 # SQLite DB (자동 생성, .gitignore)
```

## 데이터 흐름

```
[Streamlit app.py]
      │ create_job / update_progress / complete_job
      ▼
   jobs.py ──── SQLite (data/jobs.db, WAL 모드)
      ▲                       ▲
      │ list_jobs / get_job   │ get_progress
      │                       │
[Streamlit 진행상황 페이지]   [FastAPI api.py] ──── 향후 iOS 앱 / Live Activity
```

핵심: **jobs.py가 모든 클라이언트의 단일 데이터 소스.** Streamlit, FastAPI, 향후 iOS 모두 동일한 테이블을 본다.

## 실행

```bash
# 웹 (Streamlit)
streamlit run app.py

# 모바일/iOS용 API (별도 터미널)
uvicorn api:app --host 0.0.0.0 --port 8001 --reload
```

## 사용자 식별 (베타)

지금은 익명 세션 기반 — `?user=foo` 쿼리스트링으로 ID 고정 가능.
같은 ID를 모바일 진행상황 페이지에서도 쓰면 작업 목록이 보인다.

**다음 단계:** Supabase Auth로 교체. `_get_user_id()` 함수만 갈아끼우면 끝.

## 작업(Job) 스키마

| 컬럼 | 설명 |
|---|---|
| `id` | 12자 hex (URL/푸시 토큰에 안전) |
| `user_id` | 사용자 식별자 |
| `type` | `"storyboard"` 등 |
| `status` | `queued` / `running` / `completed` / `failed` |
| `progress` | 0~100 |
| `current_step` | 사용자에게 보여줄 한글 메시지 |
| `step_index` / `total_steps` | "3/5" 형태 표시용 |
| `result_json` | 완료 시 결과 메타데이터 (파일명, 씬 개수 등) |
| `error` | 실패 시 사유 |
| `created_at` / `updated_at` / `completed_at` | UNIX timestamp |

## API 엔드포인트

```
GET  /health
GET  /api/jobs                   # 헤더: X-User-Id
GET  /api/jobs/{id}              # 헤더: X-User-Id
GET  /api/jobs/{id}/progress     # 인증 없음 (Live Activity 폴링용 경량)
```

## 로드맵

### Phase 1 — 지금 (기본 틀) ✅
- 작업 추적 + 모바일 진행상황 페이지 + API 스캐폴드

### Phase 2 — 인증 + 배포
- Supabase Auth (카카오/구글 로그인)
- Streamlit Community Cloud 또는 Railway에 배포
- 도메인 연결

### Phase 3 — 결제
- 토스페이먼츠 또는 Stripe
- 사용량 추적 (Claude 토큰 → 과금)

### Phase 4 — iOS 앱
- Xcode WidgetKit 프로젝트
- `/api/jobs/{id}/progress` 폴링 → Live Activity 갱신
- 무료 Apple Developer 계정으로 본인 기기 테스트
- 안정화되면 유료 계정 + App Store 등록 검토

## 마이그레이션 노트 (SQLite → Supabase)

`jobs` 테이블 스키마는 그대로 Supabase Postgres로 옮길 수 있게 설계됨.
이전 시 `jobs.py`의 `_conn()`만 psycopg/asyncpg로 교체하면 다른 코드는 무변경.
