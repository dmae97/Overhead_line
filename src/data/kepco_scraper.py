"""한전 홈페이지 기반 Selenium 폴백.

주의: 이 모듈은 한전 사이트의 DOM/네트워크 구현에 의존한다.
약관/robots/봇탐지(CAPTCHA 등)로 인해 안정적으로 동작하지 않을 수 있다.

전략
- DOM 파싱 대신 Chrome DevTools(Network)로 JSON 응답을 포착해 파싱한다.
- 주소/키워드 입력 + 검색 트리거 이후, vol1/vol2/vol3 키가 포함된 응답을 탐색한다.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from src.core.config import settings
from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    return []


def _looks_like_capacity_payload(text: str) -> bool:
    # 빠른 힌트: 응답에 vol1/vol2/vol3가 포함되면 용량 응답일 확률이 높다.
    return '"vol1"' in text and '"vol2"' in text and '"vol3"' in text


@dataclass(frozen=True)
class ScrapeOptions:
    headless: bool = settings.selenium_headless
    page_load_timeout_seconds: float = settings.selenium_page_load_timeout_seconds
    result_timeout_seconds: float = settings.selenium_result_timeout_seconds


class KepcoCapacityScraper:
    def __init__(self, url: str | None = None, options: ScrapeOptions | None = None) -> None:
        self._url = url or settings.kepco_on_capacity_url
        self._options = options or ScrapeOptions()

    def fetch_capacity_by_keyword(self, keyword: str) -> list[CapacityRecord]:
        """키워드(주소/지번 등)로 검색 후 여유용량 레코드를 반환."""
        try:
            # selenium은 런타임에만 import (환경에 따라 미설치일 수 있음)
            from selenium.common.exceptions import TimeoutException, WebDriverException
        except ModuleNotFoundError as exc:
            raise ScraperError(
                "selenium 패키지가 설치되어 있지 않습니다. "
                "`uv sync --extra dev`로 설치 후 `uv run streamlit run src/app.py`로 실행하세요."
            ) from exc

        driver = self._create_driver()
        try:
            driver.set_page_load_timeout(self._options.page_load_timeout_seconds)

            # 일부 한전 페이지는 Selenium/Headless 환경을 감지하면 임시 안내 페이지로
            # 강제 이동시키는 경우가 있다. (index.html)
            # document-start에 webdriver 플래그를 비활성화해 정상 페이지 로딩을 시도한다.
            self._apply_stealth(driver)

            logger.info("📡 한전 접속가능 용량조회 페이지 로딩: %s", self._url)
            driver.get(self._url)

            # 봇 탐지 리디렉트 감지
            self._check_redirect(driver)

            self._enable_network(driver)

            self._trigger_search(driver, keyword)
            payload = self._wait_for_capacity_payload(driver)
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
            return records
        except TimeoutException as exc:
            raise ScraperError("페이지 로딩/응답 대기 시간이 초과되었습니다.") from exc
        except WebDriverException as exc:
            raise ScraperError(f"브라우저 자동화 오류: {exc}") from exc
        finally:
            with suppress(Exception):
                driver.quit()

    def _effective_headless(self) -> bool:
        """실제 headless 동작 여부를 결정."""
        explicit = os.getenv("SELENIUM_HEADLESS")
        if explicit is not None:
            return explicit.strip().lower() != "false"
        # 환경변수 미설정 시: headless가 안전한 기본값
        return True

    def _create_driver(self):
        from selenium import webdriver
        from selenium.common.exceptions import SessionNotCreatedException
        from selenium.webdriver.chrome.service import Service

        opts = webdriver.ChromeOptions()

        headless = self._effective_headless()
        if headless:
            # Chrome 109+ 권장 headless 모드
            opts.add_argument("--headless=new")

        # 자동화 감지 완화 (사이트에 따라 headless/selenium 감지 시 임시 페이지로 이동)
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-software-rasterizer")
        opts.add_argument("--remote-debugging-port=0")
        opts.add_argument("--window-size=1400,900")
        opts.add_argument("--lang=ko-KR")

        # 성능 로그 활성화 (Network 이벤트 포착)
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # 브라우저 바이너리 체크 (환경 문제를 더 빨리 진단)
        chrome_bin = os.getenv("CHROME_BIN") or (
            shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        if not chrome_bin:
            raise ScraperError(
                "Chrome/Chromium 실행 파일을 찾을 수 없습니다. "
                "google-chrome 또는 chromium 설치가 필요합니다.\n"
                "- Streamlit Cloud: repo 루트에 packages.txt를 추가하고 `chromium`, "
                "`chromium-driver`를 설치하세요.\n"
                "- 대안: Secrets에 KEPCO_API_KEY를 설정해 OpenAPI 모드로 실행하세요."
            )

        # chromedriver는 있으면 사용하고, 없으면 Selenium Manager에 맡긴다.
        opts.binary_location = chrome_bin
        driver_path = os.getenv("CHROMEDRIVER_PATH") or shutil.which("chromedriver")
        service = Service(driver_path) if driver_path else None

        try:
            if service is not None:
                return webdriver.Chrome(service=service, options=opts)
            return webdriver.Chrome(options=opts)
        except SessionNotCreatedException as exc:
            display = os.getenv("DISPLAY")
            raise ScraperError(
                "Chrome 세션 생성에 실패했습니다. "
                "(WSL/서버 환경에서 DISPLAY가 없으면 headless가 필요합니다.)\n"
                f"- DISPLAY={display!r}\n"
                f"- SELENIUM_HEADLESS={os.getenv('SELENIUM_HEADLESS')!r}\n"
                "해결: .env에 `SELENIUM_HEADLESS=true`를 설정하거나 "
                "GUI 환경(WSLg 등)에서 실행하세요."
            ) from exc

    @staticmethod
    def _apply_stealth(driver) -> None:
        """document-start에 최소 스텔스 스크립트를 주입."""
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": (
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    )
                },
            )
        except Exception:
            return

    @staticmethod
    def _check_redirect(driver) -> None:
        """봇 탐지/유지보수 등에 의한 리디렉트를 감지."""
        current_url = driver.current_url.lower()
        redirect_indicators = ["/index.html", "/kepco/main/main.do"]
        for indicator in redirect_indicators:
            if indicator in current_url and "cohepp" not in current_url:
                logger.warning(
                    "⚠️ 봇 탐지/리디렉트 감지: 현재 URL=%s",
                    driver.current_url,
                )
                raise ScraperError(
                    f"봇 탐지로 인해 다른 페이지로 리디렉트되었습니다.\n"
                    f"현재 URL: {driver.current_url}\n"
                    f"잠시 후 다시 시도하거나, KEPCO_API_KEY를 설정해 OpenAPI를 사용하세요."
                )

    @staticmethod
    def _enable_network(driver) -> None:
        try:
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            # 일부 환경에서 CDP가 제한될 수 있음
            return

    @staticmethod
    def _trigger_search(driver, keyword: str) -> None:
        """다중 셀렉터 + iframe 탐색으로 검색 입력창을 찾아 키워드를 입력."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions as ec
        from selenium.webdriver.support.ui import WebDriverWait

        logger.info("🔍 검색 키워드: %s", keyword)

        # 사용할 셀렉터 목록 (우선순위 순)
        input_selectors = [
            (By.ID, "inpSearchKeyword"),
            (By.NAME, "searchKeyword"),
            (By.NAME, "keyword"),
            (By.NAME, "addr"),
            (By.CSS_SELECTOR, "input[placeholder*='주소']"),
            (By.CSS_SELECTOR, "input[placeholder*='검색']"),
            (By.CSS_SELECTOR, "input[type='text']"),
        ]

        wait = WebDriverWait(driver, 30)

        # 1) 메인 프레임에서 탐색
        inp = None
        for by, value in input_selectors:
            try:
                inp = wait.until(
                    ec.presence_of_element_located((by, value)),
                )
                if inp and inp.is_displayed():
                    logger.info("✅ 입력창 발견 (메인 프레임): %s=%s", by, value)
                    break
                inp = None
            except Exception:
                inp = None
                continue

        # 2) iframe 안에서 탐색
        if inp is None:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            logger.info("🔍 iframe %d개 탐색 시작", len(iframes))
            for iframe in iframes:
                try:
                    driver.switch_to.frame(iframe)
                    iframe_wait = WebDriverWait(driver, 5)
                    for by, value in input_selectors:
                        try:
                            inp = iframe_wait.until(
                                ec.presence_of_element_located((by, value)),
                            )
                            if inp and inp.is_displayed():
                                logger.info(
                                    "✅ 입력창 발견 (iframe): %s=%s",
                                    by,
                                    value,
                                )
                                break
                            inp = None
                        except Exception:
                            inp = None
                            continue
                    if inp:
                        break
                    driver.switch_to.default_content()
                except Exception:
                    with suppress(Exception):
                        driver.switch_to.default_content()
                    continue

        if inp is None:
            # 진단 정보 수집
            page_title = ""
            current_url = ""
            with suppress(Exception):
                driver.switch_to.default_content()
                page_title = driver.title
                current_url = driver.current_url
            raise ScraperError(
                f"검색 입력창을 찾을 수 없습니다.\n"
                f"현재 URL: {current_url}\n"
                f"페이지 제목: {page_title}\n"
                f"페이지 구조가 변경되었거나 봇 감지로 차단되었을 수 있습니다."
            )

        inp.clear()
        inp.send_keys(keyword)

        # 검색 트리거: 버튼 클릭 or Enter
        search_triggered = False
        button_selectors = [
            (By.ID, "btn_search"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
        ]
        for by, value in button_selectors:
            try:
                btn = driver.find_element(by, value)
                if btn.is_displayed():
                    btn.click()
                    search_triggered = True
                    logger.info("✅ 검색 버튼 클릭: %s=%s", by, value)
                    break
            except Exception:
                continue

        if not search_triggered:
            logger.info("검색 버튼 미발견 → Enter 키로 검색 트리거")
            inp.send_keys(Keys.ENTER)

    def _wait_for_capacity_payload(self, driver) -> Any:
        """Network 로그에서 vol1/2/3 포함 JSON 응답을 찾아 반환."""
        end_time = time.time() + self._options.result_timeout_seconds

        # performance logs는 누적되므로, 반복적으로 비우며 탐색
        while time.time() < end_time:
            try:
                logs = driver.get_log("performance")
            except Exception:
                logs = []

            for entry in logs:
                msg = entry.get("message")
                if not msg:
                    continue
                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                message = data.get("message", {})
                if message.get("method") != "Network.responseReceived":
                    continue

                params = message.get("params", {})
                response = params.get("response", {})
                mime = (response.get("mimeType") or "").lower()
                if "json" not in mime:
                    # 일부 응답은 text/html로 올 수 있어 body 기반으로 한 번 더 판별
                    pass

                request_id = params.get("requestId")
                if not request_id:
                    continue

                body_text = self._get_response_body(driver, request_id)
                if not body_text:
                    continue

                if not _looks_like_capacity_payload(body_text):
                    continue

                try:
                    return json.loads(body_text)
                except Exception:
                    # JSON이 아니면 무시
                    continue

            time.sleep(0.3)

        raise ScraperError(
            "용량 응답을 찾지 못했습니다. "
            "사이트가 CAPTCHA/봇탐지를 요구하거나 DOM/네트워크가 변경되었을 수 있습니다."
        )

    @staticmethod
    def _get_response_body(driver, request_id: str) -> str | None:
        try:
            res = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
        except Exception:
            return None

        body = res.get("body")
        if not isinstance(body, str) or not body:
            return None
        return body
