"""실행 환경에 따라 앱이 파일을 저장할 기준 경로를 계산한다."""

from __future__ import annotations

from pathlib import Path
import sys


def application_directory() -> Path:
    """실행 파일 또는 개발 프로젝트가 위치한 디렉토리를 반환한다.

    macOS 앱 번들에서는 ``.app`` 파일이 놓인 디렉토리를 사용하고,
    Windows 실행 파일에서는 ``.exe``가 놓인 디렉토리를 사용한다.

    Returns:
        Path: 사용자 생성 파일을 저장할 애플리케이션 기준 디렉토리.
    """
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if (
            sys.platform == "darwin"
            and executable.parent.name == "MacOS"
            and executable.parent.parent.name == "Contents"
            and executable.parent.parent.parent.suffix == ".app"
        ):
            return executable.parents[3]
        return executable.parent
    return Path(__file__).resolve().parent.parent


def screenshot_directory() -> Path:
    """실행 파일 기준의 화면 캡처 저장 디렉토리를 반환한다.

    Returns:
        Path: ``screenshot`` 하위 디렉토리 경로.
    """
    return application_directory() / "screenshot"
