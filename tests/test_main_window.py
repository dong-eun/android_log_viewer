from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from android_log_viewer.adb import AndroidDevice
from android_log_viewer.log_parser import LogEntry
from android_log_viewer.main_window import DEFAULT_MAX_LOG_LINES, DISPLAY_BATCH_SIZE, MainWindow


def _create_window() -> MainWindow:
    """테스트용 QApplication과 메인 창을 생성한다.

    Returns:
        MainWindow: 화면 표시 없이 사용할 메인 창 객체.
    """
    QApplication.instance() or QApplication([])
    return MainWindow()


def test_default_and_selected_max_log_lines_are_applied() -> None:
    """기본 5,000줄과 사용자가 선택한 줄 제한이 저장소와 문서에 함께 적용되는지 검증한다."""
    window = _create_window()
    try:
        assert window._logs.maxlen == DEFAULT_MAX_LOG_LINES
        assert window.log_view.maximumBlockCount() == DEFAULT_MAX_LOG_LINES

        index = window.max_lines_combo.findData(1_000)
        window.max_lines_combo.setCurrentIndex(index)

        assert window._logs.maxlen == 1_000
        assert window._pending_display.maxlen == 1_000
        assert window.log_view.maximumBlockCount() == 1_000
    finally:
        window.close()


def test_clear_screen_releases_internal_log_memory() -> None:
    """화면 지우기가 앱 로그와 배치 출력 대기열을 모두 해제하는지 검증한다."""
    window = _create_window()
    try:
        entry = LogEntry(raw="sample")
        window._logs.append(entry)
        window._pending_display.append(entry)
        window._visible_count = 1

        window.clear_screen()

        assert not window._logs
        assert not window._pending_display
        assert window._visible_count == 0
        assert window.log_view.toPlainText() == ""
    finally:
        window.close()


def test_cached_filter_reuses_terms_level_and_package_pids() -> None:
    """캐시된 필터가 AND 검색어, 레벨과 패키지 PID를 모두 적용하는지 검증한다."""
    window = _create_window()
    try:
        window._package_pids = {"com.example.app": {"1234"}}
        window.filter_input.setText("package:com.example timeout network")
        window.level_combo.setCurrentIndex(window.level_combo.findData("W"))
        window._cache_filter()

        matching = LogEntry(raw="W Network: timeout", level="W", pid="1234")
        wrong_pid = LogEntry(raw="W Network: timeout", level="W", pid="9999")
        low_level = LogEntry(raw="D Network: timeout", level="D", pid="1234")

        assert window._matches_filter(matching)
        assert not window._matches_filter(wrong_pid)
        assert not window._matches_filter(low_level)
    finally:
        window.close()


def test_display_queue_is_flushed_in_bounded_batches() -> None:
    """대기 로그가 한 번에 지정된 최대 배치 크기만큼만 출력되는지 검증한다."""
    window = _create_window()
    try:
        total = DISPLAY_BATCH_SIZE + 10
        window._pending_display.extend(LogEntry(raw=f"line {index}") for index in range(total))

        window._flush_display_batch()

        assert len(window._pending_display) == 10
        assert len(window.log_view.toPlainText().splitlines()) == DISPLAY_BATCH_SIZE
    finally:
        window.close()


def test_internal_log_queues_stay_bounded_during_large_burst() -> None:
    """대량 로그가 한꺼번에 들어와도 앱 내부 저장소와 출력 대기열이 설정값을 넘지 않는지 검증한다."""
    window = _create_window()
    try:
        index = window.max_lines_combo.findData(1_000)
        window.max_lines_combo.setCurrentIndex(index)
        entries = (LogEntry(raw=f"line {line_number}") for line_number in range(100_000))

        for entry in entries:
            window._logs.append(entry)
            window._pending_display.append(entry)

        assert len(window._logs) == 1_000
        assert len(window._pending_display) == 1_000
        assert window._logs[0].raw == "line 99000"
    finally:
        window.close()


def test_process_query_does_not_start_while_previous_query_is_running() -> None:
    """주기 타이머가 호출되어도 진행 중인 프로세스 조회와 중복 실행되지 않는지 검증한다."""
    window = _create_window()
    commands: list[list[str]] = []
    try:
        device = AndroidDevice(serial="device-1", state="device")
        window._adb_path = "adb"
        window._run_text_query = lambda command, _complete, _finished=None: commands.append(command)  # type: ignore[method-assign]
        window._selected_device = lambda: device  # type: ignore[method-assign]

        window._load_process_metadata()
        window._load_process_metadata()

        assert len(commands) == 1
        assert window._process_query_in_flight
    finally:
        window.close()
