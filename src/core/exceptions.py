"""커스텀 예외 클래스 모듈

앱에서 발생하는 모든 예외의 계층 구조를 정의한다.
각 레이어별로 구분된 예외를 사용하여 에러 핸들링을 명확하게 한다.
"""

from __future__ import annotations


class OverheadLineError(Exception):
    """앱 전체 기본 예외

    모든 커스텀 예외의 부모 클래스.
    """

    def __init__(self, message: str = "알 수 없는 오류가 발생했습니다.") -> None:
        self.message = message
        super().__init__(self.message)


class AddressDataError(OverheadLineError):
    """주소 데이터 관련 에러

    PublicDataReader 호출 실패, 법정동코드 매핑 오류 등.
    """

    def __init__(
        self,
        message: str = "주소 데이터 처리 중 오류가 발생했습니다.",
    ) -> None:
        super().__init__(message)


class KepcoAPIError(OverheadLineError):
    """한전 OpenAPI 호출/파싱 관련 에러."""

    def __init__(
        self,
        message: str = "한전 API 호출 중 오류가 발생했습니다.",
        status_code: int | None = None,
    ) -> None:
        self.status_code = status_code
        super().__init__(message)


class ScraperError(OverheadLineError):
    """브라우저 자동화(Playwright/Selenium) 기반 웹 조회 에러."""

    def __init__(self, message: str = "웹 조회(스크래핑) 중 오류가 발생했습니다.") -> None:
        super().__init__(message)


class DataLoadError(OverheadLineError):
    """데이터 로드 관련 에러

    CSV/Excel/JSON 파일 파싱 실패, 샘플 데이터 로드 실패 등.
    """

    def __init__(
        self,
        message: str = "데이터 로드 중 오류가 발생했습니다.",
    ) -> None:
        super().__init__(message)


class HistoryDBError(OverheadLineError):
    def __init__(
        self,
        message: str = "조회 이력 DB 처리 중 오류가 발생했습니다.",
    ) -> None:
        super().__init__(message)
