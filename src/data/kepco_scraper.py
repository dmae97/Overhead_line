"""한전 홈페이지 기반 Selenium 폴백.

주의: 이 모듈은 한전 사이트의 DOM/네트워크 구현에 의존한다.
약관/robots/봇탐지(CAPTCHA 등)로 인해 안정적으로 동작하지 않을 수 있다.

전략
- DOM 파싱 대신 Chrome DevTools(Network)로 JSON 응답을 포착해 파싱한다.
- 주소/키워드 입력 + 검색 트리거 이후, vol1/vol2/vol3 키가 포함된 응답을 탐색한다.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from src.core.config import settings
from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord


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
            driver.get(self._url)

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
        """실제 headless 동작 여부를 결정.

        - SELENIUM_HEADLESS가 명시적으로 "false"인 경우에만 headless 해제.
        - 미설정이거나 "true"이면 headless 모드 사용.
        - WSL 환경에서 DISPLAY가 설정되어 있어도 X 서버가 비활성일 수 있으므로
          명시적 false 외에는 headless를 기본으로 한다.
        """

        explicit = os.getenv("SELENIUM_HEADLESS")
        if explicit is not None:
            return explicit.strip().lower() != "false"
        # 환경변수 미설정 시: headless가 안전한 기본값
        return True

    def _create_driver(self):
        from selenium import webdriver
        from selenium.common.exceptions import SessionNotCreatedException

        opts = webdriver.ChromeOptions()

        headless = self._effective_headless()
        if headless:
            # Chrome 109+ 권장 headless 모드
            opts.add_argument("--headless=new")

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
        chrome_bin = (
            shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        if not chrome_bin:
            raise ScraperError(
                "Chrome/Chromium 실행 파일을 찾을 수 없습니다. "
                "google-chrome 또는 chromium 설치가 필요합니다."
            )

        try:
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
    def _enable_network(driver) -> None:
        try:
            driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            # 일부 환경에서 CDP가 제한될 수 있음
            return

    @staticmethod
    def _trigger_search(driver, keyword: str) -> None:
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions as ec
        from selenium.webdriver.support.ui import WebDriverWait

        wait = WebDriverWait(driver, 20)

        # 1) 입력창 찾기 (현재 HTML에서 확인된 id)
        try:
            inp = wait.until(ec.presence_of_element_located((By.ID, "inpSearchKeyword")))
        except TimeoutException as exc:
            raise ScraperError("검색 입력창(#inpSearchKeyword)을 찾을 수 없습니다.") from exc

        inp.clear()
        inp.send_keys(keyword)

        # 2) 검색 트리거: 버튼 클릭 or Enter
        try:
            btn = driver.find_element(By.ID, "btn_search")
            btn.click()
        except Exception:
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
