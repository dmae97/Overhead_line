# TODO

## 지금 해야 할 일
- [x] 테스트 환경 준비 (uv sync 또는 pip install pytest)
- [x] 모델 테스트 실행 및 통과 확인 (pytest -v tests/test_models.py)
- [x] Streamlit에서 "읍/면/동=전체" 선택 시 OpenAPI(시군구 단위) 조회 동작 확인
- [x] Playwright로 배포 앱 inner URL 조회 결과 재검증 (현재 `/ew/cpct/retrieveMeshNo`는 HTTP 500 관측 → L2 폴백 경로 점검)
- [x] OpenAPI에서 동 미지정/전체 조회 가능 여부 확인 (문서 또는 실측)
- [x] "전체" 선택 시 UX 문구/폴백 정책 확정

## 다음 로드맵
### 단기 (1~2주)
- [x] to_kepco_params/폴백 분기 단위 테스트 추가
- [x] OpenAPI 0건 응답에 대한 사용자 안내 개선
- [x] 캐시 정책(키/TTL) 점검 및 문서화
- [x] README/SPEC에 "전체 조회" 제한/폴백 규칙 반영

### 중기 (1~2개월)
- [ ] OpenAPI vs 한전ON 결과 비교 로그/리포트 자동화
- [ ] 재시도/지연 정책 설정값화 및 튜닝
- [ ] 에러 기반 자동 폴백 옵션 제공

### 장기
- [ ] 지역 검색/추천 UX 강화
- [ ] 성능/비용 모니터링 대시보드 추가
- [ ] 동 리스트 배치 조회 기능 검토
