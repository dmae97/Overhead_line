# AGENTS.md - 한전 배전선로 여유용량 스캐너 개발 가이드라인

> **프로젝트**: Overhead Line Capacity Scanner  
> **최종 수정**: 2026-02-05

---

## 프로젝트 개요

한전(KEPCO) 배전선로 여유용량을 실시간 조회하는 Streamlit 기반 웹 대시보드.  
태양광 발전사업자/컨설턴트가 특정 지역의 계통연계 가능 여부를 빠르게 판단할 수 있도록 지원.

---

## 기술 스택

| 카테고리 | 기술 | 용도 |
|----------|------|------|
| 언어 | Python 3.11+ | 메인 언어 |
| UI 프레임워크 | Streamlit ≥1.30 | 웹 대시보드 |
| HTTP 클라이언트 | httpx ≥0.27 | 한전 API 호출 (async) |
| 주소 데이터 | PublicDataReader ≥1.1 | 법정동/행정동 코드 |
| 데이터 처리 | pandas ≥2.0 | DataFrame 연산 |
| 시각화 | plotly ≥5.0 | 인터랙티브 차트 |
| 데이터 검증 | pydantic ≥2.0 | API 응답 모델 |
| 환경변수 | python-dotenv | .env 관리 |
| 패키지 관리 | uv | 빠른 의존성 설치 |
| 린팅 | ruff | 코드 품질 |
| 테스트 | pytest + pytest-asyncio | 단위/통합 테스트 |
| 스크래핑 (v2) | selenium ≥4.0 | 한전ON 폴백 |

---

## 아키텍처 원칙

### 1. API First
- **한전 REST API**가 1차 데이터 소스. Selenium은 폴백(v2) 전용.
- API 불가 시에만 스크래핑 로직 실행.

### 2. Clean Architecture
```
UI (Streamlit) → Service Layer → Data Layer (API/Scraper)
                                       ↓
                                  Models (Pydantic)
```
- UI는 데이터 소스를 알지 못함 (추상화)
- Service layer가 API vs Scraper 선택 담당

### 3. 캐싱 전략
- **법정동코드**: 앱 시작 시 1회 로드 → `st.cache_data` (TTL: 24h)
- **API 응답**: `st.cache_data` (TTL: 5분) — 동일 요청 반복 방지
- **시도/시군구 목록**: 법정동코드에서 파생, 별도 캐시

### 4. 에러 핸들링
```python
# 모든 외부 호출은 try/except + 사용자 친화적 메시지
try:
    result = await kepco_api.query(params)
except KepcoAPIError as e:
    st.error(f"한전 API 오류: {e.message}")
    st.info("잠시 후 다시 시도해주세요.")
except httpx.TimeoutException:
    st.warning("요청 시간 초과. 네트워크 상태를 확인해주세요.")
```

---

## 코딩 컨벤션

### 파일 구조
```
src/
├── app.py              # Streamlit 엔트리포인트
├── core/               # 설정, 예외, 상수
├── data/               # 데이터 접근 레이어
├── ui/                 # Streamlit UI 컴포넌트
└── utils/              # 유틸리티 (캐시, 내보내기)
```

### Python 스타일
- **ruff** 린터 사용 (설정: `pyproject.toml`)
- **Type hints** 필수 — 모든 함수 시그니처에 타입 명시
- **Docstring** 필수 — Google style
- **한글 변수명 금지** — 코드에서는 영문만 사용 (주석/로그는 한글 OK)
- **f-string** 우선 사용

### 명명 규칙
| 대상 | 규칙 | 예시 |
|------|------|------|
| 파일 | snake_case | `kepco_api.py` |
| 클래스 | PascalCase | `KepcoApiClient` |
| 함수 | snake_case | `fetch_capacity()` |
| 상수 | UPPER_SNAKE | `KEPCO_API_BASE_URL` |
| Pydantic 모델 | PascalCase | `CapacityResponse` |

### Import 순서
```python
# 1. 표준 라이브러리
import os
from pathlib import Path

# 2. 서드파티
import httpx
import pandas as pd
import streamlit as st
from pydantic import BaseModel

# 3. 로컬 모듈
from src.core.config import settings
from src.data.kepco_api import KepcoApiClient
```

---

## 핵심 모듈 가이드

### `src/data/address.py` — 주소 데이터 관리

```python
"""PublicDataReader 기반 주소 데이터 관리 모듈

핵심 API:
- pdr.code_bdong()  → 법정동코드 전체 DataFrame (인증키 불필요)
- pdr.code_hdong()  → 행정동코드 전체 DataFrame (인증키 불필요)

법정동코드 DataFrame 컬럼:
  시도코드(str), 시도명(str), 시군구코드(str), 시군구명(str),
  법정동코드(str), 읍면동명(str), 동리명(str), 생성일자(str), 말소일자(str)

⚠️ 주의사항:
- 말소일자가 비어있는 행만 현행(유효) 법정동
- 시군구코드는 5자리 (시도코드 2자리 + 시군구 고유 3자리)
- 한전 API의 cityCd는 시군구코드의 뒤 3자리
"""
```

### `src/data/kepco_api.py` — 한전 API 클라이언트

```python
"""한전 전력데이터 개방포털 REST API 클라이언트

엔드포인트: https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do
인증: API Key (40자리) — 전력데이터 개방포털 회원가입 후 발급

요청 파라미터 매핑 (PublicDataReader → KEPCO API):
  시도코드 (2자리)     → metroCd
  시군구코드 (뒤 3자리) → cityCd
  읍면동명             → addrLidong
  동리명               → addrLi
  번지                 → addrJibun

응답 핵심 필드:
  vol1: 변전소 여유용량 (kW)
  vol2: 변압기 여유용량 (kW)
  vol3: DL(배전선로) 여유용량 (kW)

⚠️ 주의사항:
- 여유용량이 어느 하나라도 "0"이면 해당 DL 내 특고압/저압 연계 불가
- 응답은 수치적 잠재량이며, 실제 기술검토 시 허용 불가할 수 있음
- API Key는 반드시 .env 파일에서 로드
"""
```

### `src/data/models.py` — Pydantic 데이터 모델

```python
"""모든 외부 데이터는 Pydantic 모델로 검증

필수 모델:
- CapacityRecord: 단일 배전선로 여유용량 레코드
- CapacityResponse: API 응답 전체
- AddressParams: 한전 API 요청 파라미터
- RegionInfo: 지역 선택 정보 (시도/시군구/읍면동)
"""
```

---

## 환경 설정

### `.env` 파일 구조
```env
# 한전 전력데이터 개방포털 API Key
# https://bigdata.kepco.co.kr 회원가입 후 발급
KEPCO_API_KEY=your_40_char_api_key_here

# 선택: 디버그 모드
DEBUG=false

# 선택: API 호출 간 딜레이 (초)
API_DELAY_SECONDS=1.0
```

### `.env.example` — 커밋 대상
```env
KEPCO_API_KEY=
DEBUG=false
API_DELAY_SECONDS=1.0
```

---

## 테스트 전략

### 단위 테스트
```python
# tests/test_address.py — 주소 데이터 변환 로직
# tests/test_kepco_api.py — API 클라이언트 (mock 응답)
# tests/test_models.py — Pydantic 모델 검증
```

### 통합 테스트
```python
# tests/test_integration.py — 실제 API 호출 (CI에서는 skip)
# @pytest.mark.skipif(not os.getenv("KEPCO_API_KEY"), reason="API key required")
```

### Mock 데이터
```python
# tests/conftest.py에 fixture 정의
# 실제 API 응답을 JSON으로 저장 → tests/fixtures/kepco_response.json
```

### 테스트 실행
```bash
# 전체 테스트
pytest -v

# 통합 테스트 제외
pytest -v -m "not integration"

# 커버리지
pytest --cov=src --cov-report=html
```

---

## Git 워크플로우

### 브랜치 전략
- `main`: 안정 버전
- `develop`: 개발 통합
- `feature/*`: 기능 브랜치 (예: `feature/kepco-api-client`)
- `fix/*`: 버그 수정

### 커밋 메시지 컨벤션
```
feat: 한전 API 클라이언트 구현
fix: 법정동코드 매핑 오류 수정
docs: SPEC.md 업데이트
refactor: 주소 변환 로직 분리
test: API 클라이언트 단위 테스트 추가
chore: ruff 설정 추가
```

### .gitignore 필수 항목
```
.env
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
data/*.csv
data/*.db
.venv/
```

---

## 실행 방법

### 개발 환경 셋업
```bash
# 1. 의존성 설치
uv sync

# 2. 환경변수 설정
cp .env.example .env
# .env 파일에 KEPCO_API_KEY 입력

# 3. 앱 실행
streamlit run src/app.py

# 4. 테스트
pytest -v
```

### Docker (배포)
```bash
docker build -t overhead-line .
docker run -p 8501:8501 --env-file .env overhead-line
```

---

## 자주 하는 실수 방지

### ❌ 하지 말 것
- API Key를 코드에 직접 작성
- `as any` / `type: ignore` 등의 타입 억제
- 빈 except 블록 (`except: pass`)
- print() 디버깅 (로깅 사용)
- 전역 상태 직접 조작 (st.session_state 사용)

### ✅ 반드시 할 것
- 모든 외부 호출에 타임아웃 설정
- API 응답은 Pydantic 모델로 검증
- 환경변수는 `src/core/config.py`를 통해 접근
- DataFrame 연산은 `.copy()` 후 수행 (원본 보호)
- Streamlit 캐시는 TTL 명시

---

## 한전 API 특이사항

1. **여유용량 = 0**: 해당 DL 내 특고압 및 저압(전용변압기 활용) 연계 불가
2. **수치적 잠재량**: API 응답은 "단순 수치적 잠재량"이며, 기술검토 시 허용 불가할 수 있음
3. **전용선로 연계**: 여유용량이 0이라도 전용선로 연계나 저압 연계는 해당 사업소 문의 가능
4. **코드 체계**: 한전 metroCd/cityCd는 법정동코드 체계와 동일 (행정동 X)
5. **API Key 발급**: https://bigdata.kepco.co.kr 회원가입 후 마이페이지에서 발급

---

## 참고 링크

| 자료 | URL |
|------|-----|
| 한전 접속가능 용량조회 | https://home.kepco.co.kr/kepco/CO/H/E/COHEPP001/COHEPP00110.do?menuCd=FN420106 |
| 한전 전력데이터 개방포털 | https://bigdata.kepco.co.kr |
| 분산전원 연계정보 API | https://bigdata.kepco.co.kr/cmsmain.do?scode=S01&pcode=000493&pstate=dgen&redirect=Y |
| 공공데이터포털 (한전 API) | https://www.data.go.kr/data/15147381/openapi.do |
| PublicDataReader GitHub | https://github.com/WooilJeong/PublicDataReader |
| PublicDataReader 법정동코드 가이드 | https://wooiljeong.github.io/python/pdr-code/ |
| 재생에너지 클라우드플랫폼 | https://recloud.energy.or.kr/process/sub1_3_1.do |
