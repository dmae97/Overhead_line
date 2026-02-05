# ⚡ 한전 배전선로 여유용량 스캐너

한전(KEPCO) 배전선로 여유용량을 실시간 조회하는 Streamlit 기반 웹 대시보드.

## 빠른 시작

```bash
uv sync
cp .env.example .env
# .env에 KEPCO_API_KEY 입력
streamlit run src/app.py
```

## Streamlit Cloud 배포

- Streamlit Cloud에서는 `.env` 대신 **Secrets**를 사용하세요.
- App settings → Secrets에 아래처럼 추가:

```toml
KEPCO_API_KEY = "your_40_char_api_key_here"
```

## 기능

- 시/도 → 시/군/구 → 읍/면/동 3단계 지역 선택
- 한전 REST API 기반 배전선로 여유용량 조회
- 변전소/변압기/DL 여유용량 색상 코딩 테이블
- Plotly 인터랙티브 바 차트
- CSV/Excel 다운로드
