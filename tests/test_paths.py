from __future__ import annotations

from pathlib import Path

import android_log_viewer.paths as paths


def test_development_screenshot_directory_is_inside_project() -> None:
    """개발 실행 시 프로젝트 루트 아래 screenshot 디렉토리를 사용하는지 검증한다."""
    expected_root = Path(paths.__file__).resolve().parent.parent

    assert paths.application_directory() == expected_root
    assert paths.screenshot_directory() == expected_root / "screenshot"


def test_windows_screenshot_directory_is_next_to_executable(monkeypatch) -> None:
    """Windows 패키지 실행 시 EXE 옆 screenshot 디렉토리를 사용하는지 검증한다."""
    executable = "/build/AndroidLogViewer/AndroidLogViewer.exe"
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setattr(paths.sys, "executable", executable)

    assert paths.application_directory() == Path(executable).parent
    assert paths.screenshot_directory() == Path(executable).parent / "screenshot"


def test_macos_screenshot_directory_is_next_to_app_bundle(monkeypatch) -> None:
    """macOS 패키지 실행 시 APP 번들 옆 screenshot 디렉토리를 사용하는지 검증한다."""
    executable = "/build/AndroidLogViewer.app/Contents/MacOS/AndroidLogViewer"
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.sys, "executable", executable)

    assert paths.application_directory() == Path("/build")
    assert paths.screenshot_directory() == Path("/build/screenshot")
