"""한전ON (online.kepco.co.kr) Playwright 기반 여유용량 스크래퍼.

타겟 페이지: https://online.kepco.co.kr/EWM092D00 (주소로 검색)

전략:
1. Playwright로 EWM092D00 페이지 로드
2. Native <select> 요소에 select_option()으로 값 설정
   — WebSquare가 native change 이벤트를 인식하여 cascading 자동 처리
3. 검색 버튼 클릭
4. 결과 영역(wframe01) DOM에서 용량 데이터를 파싱

내부 API 엔드포인트 (참고):
- POST /ew/cpct/retrieveAddrGbn  — 주소 cascading (gbn: 0=시→시, 1=시→구, 2=구→동, ...)
- POST /ew/cpct/retrieveMeshNo   — 검색 (mesh 번호 조회 → 용량 데이터 로드)

주의:
- WebSquare SPA이므로 native DOM 이벤트(change)만으로 cascading 작동
- 결과 데이터는 API 응답이 아닌 DOM 요소에 직접 주입됨
- wframe01이 display:none → visible로 전환되면 데이터 로드 완료
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from src.core.config import settings
from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)

# WebSquare selectbox 요소 ID 매핑
_SELECT_IDS = {
    "sido": "mf_wfm_layout_sbx_sido_input_0",
    "si": "mf_wfm_layout_sbx_si_input_0",
    "gu": "mf_wfm_layout_sbx_gu_input_0",
    "lidong": "mf_wfm_layout_sbx_lidong_input_0",
    "li": "mf_wfm_layout_sbx_li_input_0",
    "bunji": "mf_wfm_layout_sbx_bunji_input_0",
}

# 결과 DOM 요소 ID 매핑 (wframe01 내부)
_RESULT_IDS = {
    "subst_nm": "mf_wfm_layout_wframe01_txt_subst_nm_label",
    "mtr_no": "mf_wfm_layout_wframe01_txt_mtr_no_label",
    "dl_nm": "mf_wfm_layout_wframe01_txt_dl_nm_label",
    # 변전소
    "subst_capa": "mf_wfm_layout_wframe01_txt_subst_capa_dsc",
    "subst_pwr": "mf_wfm_layout_wframe01_txt_subst_pwr_dsc",
    "g_subst_capa": "mf_wfm_layout_wframe01_txt_g_subst_capa_dsc",
    "vol1_1": "mf_wfm_layout_wframe01_txt_subst_vol1_dsc_1",
    "vol1_2": "mf_wfm_layout_wframe01_txt_subst_vol1_dsc_2",
    # 변압기
    "mtr_capa": "mf_wfm_layout_wframe01_txt_mtr_capa_dsc",
    "mtr_pwr": "mf_wfm_layout_wframe01_txt_mtr_pwr_dsc",
    "g_mtr_capa": "mf_wfm_layout_wframe01_txt_g_mtr_capa_dsc",
    "vol2_1": "mf_wfm_layout_wframe01_txt_mtr_vol2_dsc_1",
    "vol2_2": "mf_wfm_layout_wframe01_txt_mtr_vol2_dsc_2",
    # 배전선로 (DL)
    "dl_capa": "mf_wfm_layout_wframe01_txt_dl_capa_dsc",
    "dl_pwr": "mf_wfm_layout_wframe01_txt_dl_pwr_dsc",
    "g_dl_capa": "mf_wfm_layout_wframe01_txt_g_dl_capa_dsc",
    "vol3_1": "mf_wfm_layout_wframe01_txt_dl_vol3_dsc_1",
    "vol3_2": "mf_wfm_layout_wframe01_txt_dl_vol3_dsc_2",
    # 여유상태 텍스트
    "subst_yn": "mf_wfm_layout_wframe01_txt_substYn",
    "mtr_yn": "mf_wfm_layout_wframe01_txt_mtrYn",
    "dl_yn": "mf_wfm_layout_wframe01_txt_dlYn",
}

# 검색 버튼 ID
_SEARCH_BTN_ID = "mf_wfm_layout_btn_search"
# 결과 프레임 ID
_RESULT_FRAME_ID = "mf_wfm_layout_wframe01"

# EWM092D00 기본 URL
DEFAULT_EWM_URL = "https://online.kepco.co.kr/EWM092D00"

# 각 select 단계별 대기 시간 (초)
_SELECT_WAIT_SECONDS = 3.0
# 검색 결과 대기 시간 (초)
_SEARCH_WAIT_SECONDS = 10.0


def _clean_number(text: str) -> str:
    """WebSquare 숫자 텍스트에서 콤마·공백을 제거하고 순수 숫자 문자열 반환.

    예: "159,,000" → "159000", "13,000" → "13000", "0" → "0"
    """
    if not text:
        return "0"
    cleaned = re.sub(r"[,\s]", "", text.strip())
    if not cleaned:
        return "0"
    # 혹시 숫자가 아닌 문자가 섞여 있으면 숫자만 추출
    digits = re.sub(r"[^\d\-.]", "", cleaned)
    return digits if digits else "0"


def _find_system_chromium() -> str | None:
    """시스템에 설치된 Chromium/Chrome 바이너리 경로를 찾는다."""
    candidates = [
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ]
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def _ensure_playwright_browsers() -> None:
    """Playwright 브라우저 바이너리가 없으면 자동 설치를 시도한다."""
    logger.info("📦 Playwright chromium 브라우저 자동 설치 시도...")
    try:
        proc = subprocess.run(
            ["python", "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            logger.info("✅ Playwright chromium 설치 완료")
        else:
            logger.warning(
                "⚠️ Playwright chromium 설치 실패 (rc=%d): %s",
                proc.returncode,
                (proc.stderr or proc.stdout)[:300],
            )
    except Exception as exc:
        logger.warning("⚠️ Playwright 자동설치 중 예외: %s", exc)


@dataclass(frozen=True)
class OnlineScraperOptions:
    """한전ON 스크래퍼 옵션."""

    headless: bool = field(default_factory=lambda: settings.playwright_headless)
    page_load_timeout_ms: int = field(
        default_factory=lambda: int(settings.playwright_page_load_timeout_seconds * 1000)
    )
    result_timeout_seconds: float = field(
        default_factory=lambda: settings.playwright_result_timeout_seconds
    )
    browser_type: str = field(default_factory=lambda: settings.playwright_browser_type)


class KepcoOnlineScraper:
    """한전ON EWM092D00 Playwright 기반 용량 조회 스크래퍼.

    사용법::

        scraper = KepcoOnlineScraper()
        records = scraper.fetch_capacity(
            sido="충청남도",
            si="천안시",
            gu="서북구",
            dong="불당동",
        )
    """

    def __init__(
        self,
        url: str | None = None,
        options: OnlineScraperOptions | None = None,
    ) -> None:
        self._url = url or DEFAULT_EWM_URL
        self._options = options or OnlineScraperOptions()

    def fetch_capacity(
        self,
        sido: str,
        si: str = "",
        gu: str = "",
        dong: str = "",
        li: str = "",
        jibun: str = "",
    ) -> list[CapacityRecord]:
        """주소 정보로 여유용량을 조회하여 CapacityRecord 리스트를 반환.

        Args:
            sido: 시/도 (예: "충청남도")
            si: 시 (예: "천안시", 빈 문자열이면 스킵)
            gu: 구/군 (예: "서북구")
            dong: 동/면 (예: "불당동")
            li: 리 (예: "동산리", 선택사항)
            jibun: 상세번지 (예: "1", 선택사항)

        Returns:
            CapacityRecord 리스트 (최소 1건)

        Raises:
            ScraperError: 브라우저 자동화 실패, 결과 없음 등
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScraperError(
                "playwright 패키지가 설치되어 있지 않습니다.\n"
                "설치: `pip install playwright && playwright install chromium`"
            ) from exc

        with sync_playwright() as pw:
            browser = None
            try:
                browser = self._launch_browser(pw)
                context = browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    locale="ko-KR",
                    extra_http_headers={
                        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                )
                # 자동화 감지 우회
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                """)

                page = context.new_page()
                page.set_default_timeout(self._options.page_load_timeout_ms)

                logger.info("📡 한전ON EWM092D00 페이지 로딩: %s", self._url)
                page.goto(self._url, wait_until="networkidle")
                time.sleep(2)  # WebSquare 초기화 대기
                logger.info("✅ 페이지 로드 완료: %s", page.url)

                # 주소 선택 (cascading)
                self._select_address(page, sido, si, gu, dong, li, jibun)

                # 검색 실행
                self._click_search(page)

                # 결과 DOM 파싱
                records = self._parse_results(page)

                if not records:
                    raise ScraperError(
                        f"'{sido} {si} {gu} {dong}'에 대한 여유용량 결과를 찾지 못했습니다."
                    )

                logger.info("✅ %d건의 여유용량 레코드 파싱 완료", len(records))
                return records

            except ScraperError:
                raise
            except Exception as exc:
                logger.exception("한전ON 스크래핑 실패")
                raise ScraperError(
                    f"한전ON 브라우저 자동화 오류: {type(exc).__name__}: {exc}"
                ) from exc
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

    def fetch_capacity_by_region(
        self,
        sido: str,
        sigungu: str,
        dong: str = "",
        jibun: str = "",
    ) -> list[CapacityRecord]:
        """RegionInfo 스타일 입력으로 조회.

        sigungu를 "시 + 구/군"으로 분리하여 내부 fetch_capacity 호출.

        Args:
            sido: 시/도명 (예: "충청남도")
            sigungu: 시군구명 (예: "천안시 서북구", "세종특별자치시")
            dong: 읍면동명 (예: "불당동")
            jibun: 상세번지

        Returns:
            CapacityRecord 리스트
        """
        si, gu = self._split_sigungu(sigungu, sido)
        return self.fetch_capacity(sido=sido, si=si, gu=gu, dong=dong, jibun=jibun)

    @staticmethod
    def _split_sigungu(sigungu: str, sido: str) -> tuple[str, str]:
        """시군구명을 시/구로 분리.

        예:
          "천안시 서북구" → ("천안시", "서북구")
          "천안시" → ("천안시", "")
          "세종특별자치시" → ("", "")  # sido와 동일하면 시군구 없음
        """
        if not sigungu or sigungu == sido:
            return ("", "")

        parts = sigungu.strip().split()
        if len(parts) >= 2:
            return (parts[0], " ".join(parts[1:]))
        return (sigungu.strip(), "")

    def _select_address(
        self,
        page: Any,
        sido: str,
        si: str,
        gu: str,
        dong: str,
        li: str,
        jibun: str,
    ) -> None:
        """Cascading selectbox에 주소를 설정."""
        steps = [
            ("sido", sido),
            ("si", si),
            ("gu", gu),
            ("lidong", dong),
            ("li", li),
        ]

        for name, value in steps:
            if not value or value == "전체":
                continue

            select_id = _SELECT_IDS[name]
            try:
                # 옵션 목록에서 매칭되는 항목 확인
                options = self._get_select_options(page, select_id)
                if value not in options:
                    logger.warning(
                        "⚠️ '%s' selectbox에서 '%s'을(를) 찾을 수 없습니다. 옵션: %s",
                        name,
                        value,
                        options[:10],
                    )
                    # 부분 매칭 시도
                    matched = [o for o in options if value in o or o in value]
                    if matched:
                        value = matched[0]
                        logger.info("→ 부분 매칭: '%s'", value)
                    else:
                        continue

                page.select_option(f"#{select_id}", label=value)
                logger.info("✅ %s 선택: %s", name, value)
                time.sleep(_SELECT_WAIT_SECONDS)

            except Exception as exc:
                logger.warning("⚠️ %s 선택 실패 (%s): %s", name, value, exc)

        # 번지 선택 (인덱스 기반 — 첫 번째 유효 항목)
        if jibun:
            try:
                bunji_options = self._get_select_options(page, _SELECT_IDS["bunji"])
                if jibun in bunji_options:
                    page.select_option(f"#{_SELECT_IDS['bunji']}", label=jibun)
                    logger.info("✅ 번지 선택: %s", jibun)
                elif len(bunji_options) > 1:
                    page.select_option(f"#{_SELECT_IDS['bunji']}", index=1)
                    logger.info(
                        "✅ 번지 선택: 첫 번째 항목 (%s)",
                        bunji_options[1] if len(bunji_options) > 1 else "N/A",
                    )
                time.sleep(1)
            except Exception as exc:
                logger.warning("⚠️ 번지 선택 실패: %s", exc)
        else:
            # 번지 미입력 시 첫 번째 유효 항목 자동 선택
            try:
                bunji_options = self._get_select_options(page, _SELECT_IDS["bunji"])
                if len(bunji_options) > 1:
                    page.select_option(f"#{_SELECT_IDS['bunji']}", index=1)
                    logger.info("✅ 번지 자동선택: %s", bunji_options[1])
                    time.sleep(1)
            except Exception:
                pass

    @staticmethod
    def _get_select_options(page: Any, select_id: str) -> list[str]:
        """native select 요소의 옵션 텍스트 목록을 반환."""
        return page.evaluate(f"""() => {{
            const sel = document.getElementById('{select_id}');
            if (!sel) return [];
            const opts = [];
            for (let i = 0; i < sel.options.length; i++) {{
                opts.push(sel.options[i].text);
            }}
            return opts;
        }}""")

    @staticmethod
    def _click_search(page: Any) -> None:
        """검색 버튼을 클릭하고 결과를 대기."""
        search_btn = page.query_selector(f"#{_SEARCH_BTN_ID}")
        if not search_btn:
            raise ScraperError("검색 버튼을 찾을 수 없습니다.")

        logger.info("🔍 검색 버튼 클릭")
        search_btn.click()

        # 결과 프레임이 visible이 될 때까지 대기
        try:
            page.wait_for_function(
                f"""() => {{
                    const el = document.getElementById('{_RESULT_FRAME_ID}');
                    return el && el.style.display !== 'none' && el.offsetParent !== null;
                }}""",
                timeout=int(_SEARCH_WAIT_SECONDS * 1000),
            )
            logger.info("✅ 결과 프레임 표시됨")
        except Exception:
            logger.warning("⏰ 결과 프레임 대기 시간 초과 (%.0fs)", _SEARCH_WAIT_SECONDS)
            # 타임아웃이어도 DOM 파싱 시도 (데이터는 있을 수 있음)

        # 추가 대기 (데이터 렌더링)
        time.sleep(2)

    @staticmethod
    def _parse_results(page: Any) -> list[CapacityRecord]:
        """결과 프레임(wframe01) DOM에서 용량 데이터를 추출하여 CapacityRecord로 변환."""
        raw = page.evaluate(
            """() => {
            const result = {};
            const ids = %s;
            
            for (const [key, elId] of Object.entries(ids)) {
                const el = document.getElementById(elId);
                result[key] = el ? el.textContent.trim() : '';
            }
            
            // 결과 프레임 표시 여부
            const frame = document.getElementById('%s');
            result['_visible'] = frame ? (frame.style.display !== 'none') : false;
            
            return result;
        }"""
            % (
                str({k: v for k, v in _RESULT_IDS.items()}).replace("'", '"'),
                _RESULT_FRAME_ID,
            )
        )

        if not raw.get("_visible"):
            logger.warning("결과 프레임이 표시되지 않았습니다.")
            return []

        subst_nm = raw.get("subst_nm", "")
        mtr_no = raw.get("mtr_no", "")
        dl_nm = raw.get("dl_nm", "")

        if not subst_nm and not dl_nm:
            logger.warning("결과 데이터가 비어있습니다.")
            return []

        # 여유용량 추출 (vol1_1: 접수기준, vol1_2: 접속계획반영)
        vol1 = _clean_number(raw.get("vol1_1", "0"))
        vol2 = _clean_number(raw.get("vol2_1", "0"))
        vol3 = _clean_number(raw.get("vol3_1", "0"))

        # 용량 정보
        js_subst_pwr = _clean_number(raw.get("subst_capa", "0"))
        subst_pwr = _clean_number(raw.get("subst_pwr", "0"))
        js_mtr_pwr = _clean_number(raw.get("mtr_capa", "0"))
        mtr_pwr = _clean_number(raw.get("mtr_pwr", "0"))
        js_dl_pwr = _clean_number(raw.get("dl_capa", "0"))
        dl_pwr = _clean_number(raw.get("dl_pwr", "0"))

        record = CapacityRecord(
            substNm=subst_nm,
            mtrNo=mtr_no,
            dlNm=dl_nm,
            jsSubstPwr=js_subst_pwr,
            substPwr=subst_pwr,
            jsMtrPwr=js_mtr_pwr,
            mtrPwr=mtr_pwr,
            jsDlPwr=js_dl_pwr,
            dlPwr=dl_pwr,
            vol1=vol1,
            vol2=vol2,
            vol3=vol3,
        )

        logger.info(
            "📊 파싱 결과: 변전소=%s, 변압기=%s, DL=%s | vol1=%s, vol2=%s, vol3=%s",
            subst_nm,
            mtr_no,
            dl_nm,
            vol1,
            vol2,
            vol3,
        )

        return [record]

    def _launch_browser(self, pw: Any) -> Any:
        """Playwright 브라우저 인스턴스 실행 (3단계 폴백)."""
        browser_type_name = self._options.browser_type.lower()

        if browser_type_name == "firefox":
            launcher = pw.firefox
        elif browser_type_name == "webkit":
            launcher = pw.webkit
        else:
            launcher = pw.chromium

        launch_args = (
            [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
            if browser_type_name == "chromium"
            else []
        )

        # 1차: Playwright 관리 바이너리
        try:
            return launcher.launch(
                headless=self._options.headless,
                args=launch_args,
            )
        except Exception as first_err:
            logger.warning("⚠️ Playwright 바이너리 실패: %s", str(first_err)[:200])

        # 2차: 자동 설치 후 재시도
        _ensure_playwright_browsers()
        try:
            return launcher.launch(
                headless=self._options.headless,
                args=launch_args,
            )
        except Exception as second_err:
            logger.warning("⚠️ 자동설치 후 실패: %s", str(second_err)[:200])

        # 3차: 시스템 chromium 폴백
        system_chromium = _find_system_chromium()
        if system_chromium and browser_type_name == "chromium":
            try:
                return launcher.launch(
                    headless=self._options.headless,
                    executable_path=system_chromium,
                    args=launch_args,
                )
            except Exception as third_err:
                raise ScraperError(
                    f"Playwright 브라우저 실행 실패 (3단계 모두 실패): {third_err}"
                ) from third_err

        raise ScraperError(
            "Playwright 브라우저를 실행할 수 없습니다.\n해결: `playwright install chromium` 실행"
        )
