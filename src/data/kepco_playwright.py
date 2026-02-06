"""한전 홈페이지 기반 Playwright 헤드리스 스크래퍼.

Selenium 폴백(`kepco_scraper.py`) 대비 장점:
- 네이티브 response 이벤트 리스너 (CDP 명령 불필요)
- 자동 대기 (explicit WebDriverWait 불필요)
- 더 나은 stealth (navigator.webdriver 기본 우회)
- Chromium 외 Firefox/WebKit도 지원 가능
- 경량한 headless 모드

전략:
- page.on("response") 이벤트로 JSON 응답을 실시간 캡처
- vol1/vol2/vol3 키가 포함된 응답을 탐지하면 파싱
- 주소/키워드 입력 + 검색 트리거 후 결과 대기
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from src.core.config import settings
from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    """API 응답 페이로드에서 레코드 리스트를 추출."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    return []


def _looks_like_capacity_payload(text: str) -> bool:
    """빠른 힌트: 응답에 vol1/vol2/vol3가 포함되면 용량 응답일 확률이 높다."""
    return '"vol1"' in text and '"vol2"' in text and '"vol3"' in text


@dataclass(frozen=True)
class PlaywrightOptions:
    """Playwright 스크래퍼 실행 옵션."""

    headless: bool = field(default_factory=lambda: settings.playwright_headless)
    page_load_timeout_ms: int = field(
        default_factory=lambda: int(settings.playwright_page_load_timeout_seconds * 1000)
    )
    result_timeout_ms: int = field(
        default_factory=lambda: int(settings.playwright_result_timeout_seconds * 1000)
    )
    browser_type: str = field(default_factory=lambda: settings.playwright_browser_type)


class KepcoPlaywrightScraper:
    """Playwright 기반 한전 접속가능 용량조회 스크래퍼.

    사용 예시::

        scraper = KepcoPlaywrightScraper()
        records = scraper.fetch_capacity_by_keyword("세종특별자치시 조치원읍")
        for r in records:
            print(f"{r.dl_nm}: {r.min_capacity} kW")
    """

    def __init__(
        self,
        url: str | None = None,
        options: PlaywrightOptions | None = None,
    ) -> None:
        self._url = url or settings.kepco_on_capacity_url
        self._options = options or PlaywrightOptions()

    def fetch_capacity_by_keyword(self, keyword: str) -> list[CapacityRecord]:
        """키워드(주소/지번 등)로 검색 후 여유용량 레코드를 반환.

        Args:
            keyword: 검색할 주소 키워드 (예: "세종특별자치시 조치원읍")

        Returns:
            CapacityRecord 리스트

        Raises:
            ScraperError: 브라우저 자동화 실패, 응답 파싱 실패 등
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScraperError(
                "playwright 패키지가 설치되어 있지 않습니다.\n"
                "설치: `pip install playwright && playwright install chromium`\n"
                "또는: `uv add playwright && playwright install chromium`"
            ) from exc

        # 캡처된 용량 데이터를 저장할 컨테이너
        captured_payload: list[Any] = []
        payload_lock = threading.Lock()

        def _on_response(response):
            """Network 응답 이벤트 핸들러 — vol1/vol2/vol3 포함 JSON 탐지."""
            try:
                content_type = response.headers.get("content-type", "")
                # JSON 응답 또는 text/html(일부 한전 응답)을 대상으로
                if response.status != 200:
                    return

                body = response.text()
                if not body or not _looks_like_capacity_payload(body):
                    return

                data = json.loads(body)
                with payload_lock:
                    if not captured_payload:
                        captured_payload.append(data)
                        logger.info(
                            "✅ 용량 응답 캡처 성공 (URL: %s, size: %d bytes)",
                            response.url[:80],
                            len(body),
                        )
            except Exception:
                # JSON 파싱 실패 등은 무시 (다른 응답 계속 탐색)
                pass

        with sync_playwright() as pw:
            browser = None
            try:
                browser = self._launch_browser(pw)
                context = browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    locale="ko-KR",
                    # navigator.webdriver 우회를 위한 스텔스
                    extra_http_headers={
                        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                )

                # 자동화 감지 우회: navigator.webdriver 플래그 제거
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    // Chrome 자동화 감지 완화
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['ko-KR', 'ko', 'en-US', 'en']
                    });
                """)

                page = context.new_page()
                page.set_default_timeout(self._options.page_load_timeout_ms)

                # 응답 이벤트 리스너 등록
                page.on("response", _on_response)

                logger.info("📡 한전 접속가능 용량조회 페이지 로딩: %s", self._url)
                page.goto(self._url, wait_until="domcontentloaded")
                logger.info("✅ 페이지 로드 완료")

                # 검색 실행
                self._trigger_search(page, keyword)

                # 용량 응답 대기
                self._wait_for_capacity_payload(page, captured_payload, payload_lock)

                with payload_lock:
                    if not captured_payload:
                        raise ScraperError(
                            "용량 응답을 찾지 못했습니다. "
                            "사이트가 CAPTCHA/봇탐지를 요구하거나 DOM이 변경되었을 수 있습니다."
                        )
                    payload = captured_payload[0]

                raw = _extract_records(payload)
                if not raw:
                    raise ScraperError("용량 데이터 파싱 실패 (data가 비어있음)")

                records: list[CapacityRecord] = []
                for item in raw:
                    try:
                        records.append(CapacityRecord(**item))
                    except Exception:
                        continue

                if not records:
                    raise ScraperError("용량 데이터 파싱 실패 (유효 레코드 0건)")

                logger.info("✅ %d건의 여유용량 레코드 파싱 완료", len(records))
                return records

            except ScraperError:
                raise
            except Exception as exc:
                logger.exception("Playwright 스크래핑 실패")
                raise ScraperError(f"브라우저 자동화 오류: {type(exc).__name__}: {exc}") from exc
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

    def _launch_browser(self, pw):
        """Playwright 브라우저 인스턴스 실행."""
        browser_type_name = self._options.browser_type.lower()

        if browser_type_name == "firefox":
            launcher = pw.firefox
        elif browser_type_name == "webkit":
            launcher = pw.webkit
        else:
            launcher = pw.chromium

        logger.info(
            "🚀 Playwright %s 브라우저 시작 (headless=%s)",
            browser_type_name,
            self._options.headless,
        )

        return launcher.launch(
            headless=self._options.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
            if browser_type_name == "chromium"
            else [],
        )

    def _trigger_search(self, page, keyword: str) -> None:
        """검색 입력창에 키워드를 입력하고 검색을 트리거."""
        logger.info("🔍 검색 키워드: %s", keyword)

        # 입력창 대기 및 입력
        try:
            # 한전 접속가능 용량조회 페이지의 검색 입력창
            # 여러 가능한 셀렉터를 순서대로 시도
            input_selectors = [
                "#inpSearchKeyword",
                "input[name='searchKeyword']",
                "input[type='text']",
            ]

            input_elem = None
            for selector in input_selectors:
                try:
                    input_elem = page.wait_for_selector(
                        selector,
                        timeout=10000,
                        state="visible",
                    )
                    if input_elem:
                        logger.info("✅ 입력창 발견: %s", selector)
                        break
                except Exception:
                    continue

            if not input_elem:
                # 페이지 스크린샷으로 디버깅 힌트 제공
                logger.warning("검색 입력창을 찾지 못함. 현재 URL: %s", page.url)
                raise ScraperError(
                    "검색 입력창을 찾을 수 없습니다. "
                    "페이지 구조가 변경되었거나 봇 감지로 차단되었을 수 있습니다."
                )

            # 기존 텍스트 제거 후 키워드 입력
            input_elem.click()
            input_elem.fill("")
            input_elem.fill(keyword)

            # 검색 버튼 클릭 시도
            search_triggered = False
            button_selectors = [
                "#btn_search",
                "button[type='submit']",
                "button:has-text('검색')",
                "a:has-text('검색')",
                ".btn_search",
            ]

            for selector in button_selectors:
                try:
                    btn = page.wait_for_selector(selector, timeout=3000, state="visible")
                    if btn:
                        btn.click()
                        search_triggered = True
                        logger.info("✅ 검색 버튼 클릭: %s", selector)
                        break
                except Exception:
                    continue

            # 버튼을 못 찾으면 Enter 키로 검색
            if not search_triggered:
                logger.info("검색 버튼 미발견 → Enter 키로 검색 트리거")
                input_elem.press("Enter")

        except ScraperError:
            raise
        except Exception as exc:
            raise ScraperError(f"검색 트리거 실패: {exc}") from exc

    def _wait_for_capacity_payload(
        self,
        page,
        captured_payload: list[Any],
        payload_lock: threading.Lock,
    ) -> None:
        """용량 응답이 캡처될 때까지 대기."""
        import time

        timeout_seconds = self._options.result_timeout_ms / 1000.0
        end_time = time.time() + timeout_seconds
        poll_interval = 0.3  # 300ms 간격으로 폴링

        logger.info("⏳ 용량 응답 대기 중... (최대 %.0f초)", timeout_seconds)

        while time.time() < end_time:
            with payload_lock:
                if captured_payload:
                    return

            # 페이지 이벤트 처리를 위해 잠시 대기
            try:
                page.wait_for_timeout(int(poll_interval * 1000))
            except Exception:
                time.sleep(poll_interval)

        # 타임아웃 도달
        logger.warning("⏰ 용량 응답 대기 시간 초과 (%.0f초)", timeout_seconds)
