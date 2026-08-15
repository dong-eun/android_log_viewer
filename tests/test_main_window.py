from __future__ import annotations

from collections import deque
from datetime import datetime
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from android_log_viewer.adb import AndroidDevice
from android_log_viewer.log_parser import LogEntry, LogFilter
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
        assert window.log_view.maximumBlockCount() == 0

        index = window.max_lines_combo.findData(1_000)
        window.max_lines_combo.setCurrentIndex(index)

        assert window._logs.maxlen == 1_000
        assert window._pending_display.maxlen == 1_000
        assert window.log_view.maximumBlockCount() == 0
    finally:
        window.close()


def test_max_log_control_is_between_device_and_filter_rows() -> None:
    """최대 노출 로그 설정이 Device 행과 Filter 행 사이에 배치되는지 검증한다."""
    window = _create_window()
    try:
        root_layout = window.centralWidget().layout()
        limit_layout = root_layout.itemAt(1).layout()
        filter_layout = root_layout.itemAt(2).layout()

        assert limit_layout.itemAt(0).widget().text() == "최대 노출 로그"
        assert filter_layout.itemAt(0).widget().text() == "Filter"
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


def test_count_label_shows_filtered_total_and_max_lines() -> None:
    """카운트 라벨이 필터 결과, 전체 로그 수와 최대 보관 줄 수를 함께 보여주는지 검증한다."""
    window = _create_window()
    try:
        window._visible_count = 12
        window._logs.extend(LogEntry(raw=f"line {index}") for index in range(251))

        window._update_count()

        assert window.count_label.text() == "필터 결과 12 / 전체 251 · 최대 5,000"
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


def test_package_filter_can_be_combined_with_explicit_and_operator() -> None:
    """패키지 필터와 명시적 AND 연산자를 함께 사용할 수 있는지 검증한다."""
    window = _create_window()
    try:
        window._package_pids = {"com.example.app": {"1234"}}
        window.filter_input.setText("package:com.example & ERROR")
        window._cache_filter()

        matching = LogEntry(raw="E App: ERROR", level="E", pid="1234")
        wrong_text = LogEntry(raw="E App: WARNING", level="E", pid="1234")
        wrong_pid = LogEntry(raw="E App: ERROR", level="E", pid="9999")

        assert not window._cached_filter.error_message
        assert window._matches_filter(matching)
        assert not window._matches_filter(wrong_text)
        assert not window._matches_filter(wrong_pid)
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


def test_scrolling_away_freezes_view_until_returning_to_bottom() -> None:
    """스크롤 중 화면을 고정하고 최하단 복귀 시 누적 로그 출력을 재개하는지 검증한다."""
    window = _create_window()
    try:
        window._store_log_entry(LogEntry(raw="기존 화면 로그"))
        window._flush_display_batch()
        frozen_text = window.log_view.toPlainText()

        window._follow_tail = False
        window._store_log_entry(LogEntry(raw="스크롤 중 수신 로그"))
        window._flush_display_batch()

        assert window.log_view.toPlainText() == frozen_text
        assert [entry.raw for entry in window._logs] == ["기존 화면 로그", "스크롤 중 수신 로그"]
        assert [entry.raw for entry in window._pending_display] == ["스크롤 중 수신 로그"]

        scrollbar = window.log_view.verticalScrollBar()
        window._scroll_value_changed(scrollbar.maximum())

        assert window._follow_tail
        assert window._display_timer.isActive()

        window._display_timer.stop()
        window._flush_display_batch()

        assert window.log_view.toPlainText().splitlines() == ["기존 화면 로그", "스크롤 중 수신 로그"]
        assert not window._pending_display
    finally:
        window.close()


def test_log_limit_overflow_replaces_oldest_line_without_full_render() -> None:
    """최대 줄 초과 시 전체 렌더링 없이 가장 오래된 줄만 새 로그로 교체하는지 검증한다."""
    window = _create_window()
    try:
        window._max_log_lines = 3
        window._logs = deque(maxlen=3)
        window._pending_display = deque(maxlen=3)
        for index in range(3):
            window._store_log_entry(LogEntry(raw=f"line {index}"))
        window._flush_display_batch()

        with patch.object(window, "_render_all_logs", side_effect=AssertionError("전체 렌더링 호출")):
            window._store_log_entry(LogEntry(raw="line 3"))
            window._flush_display_batch()

        assert window.log_view.toPlainText().splitlines() == ["line 1", "line 2", "line 3"]
        assert [entry.raw for entry in window._logs] == ["line 1", "line 2", "line 3"]
        assert window._visible_count == 3
    finally:
        window.close()


def test_filtered_eviction_removes_stale_display_line_without_new_match() -> None:
    """오래된 일치 로그가 밀려날 때 새 로그가 불일치해도 화면의 잔여 줄을 제거하는지 검증한다."""
    window = _create_window()
    try:
        window._max_log_lines = 3
        window._logs = deque(maxlen=3)
        window._pending_display = deque(maxlen=3)
        window._cached_filter = LogFilter(terms=("keep",))
        for index in range(3):
            window._store_log_entry(LogEntry(raw=f"keep {index}"))
        window._flush_display_batch()

        window._store_log_entry(LogEntry(raw="drop 3"))
        window._flush_display_batch()

        assert window.log_view.toPlainText().splitlines() == ["keep 1", "keep 2"]
        assert window._visible_count == 2
    finally:
        window.close()


def test_unrendered_overflow_keeps_only_newest_pending_lines() -> None:
    """화면 출력 전 한도를 초과해도 대기열에는 최신 로그만 남는지 검증한다."""
    window = _create_window()
    try:
        window._max_log_lines = 3
        window._logs = deque(maxlen=3)
        window._pending_display = deque(maxlen=3)
        for index in range(4):
            window._store_log_entry(LogEntry(raw=f"line {index}"))

        window._flush_display_batch()

        assert window.log_view.toPlainText().splitlines() == ["line 1", "line 2", "line 3"]
        assert window._pending_display_removals == 0
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
            window._store_log_entry(entry)

        assert len(window._logs) == 1_000
        assert len(window._pending_display) == 1_000
        assert window._logs[0].raw == "line 99000"
        assert window._visible_count == 1_000
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


def test_screenshot_path_uses_timestamp_and_safe_device_name(tmp_path) -> None:
    """화면 캡처 경로가 지정 형식의 시각과 안전한 기기명으로 생성되는지 검증한다."""
    window = _create_window()
    try:
        device = AndroidDevice(serial="device-1", state="device", model="Pixel 9 Pro")
        window._selected_device = lambda: device  # type: ignore[method-assign]
        captured_at = datetime(2026, 7, 25, 14, 30, 45)

        with patch("android_log_viewer.main_window.screenshot_directory", return_value=tmp_path / "screenshot"):
            path = window._screenshot_path(captured_at)

        assert path == tmp_path / "screenshot" / "20260725143045_Pixel_9_Pro.png"
    finally:
        window.close()


def test_capture_screen_creates_directory_and_starts_adb_file_command(tmp_path) -> None:
    """화면 캡처가 저장 디렉토리를 만들고 PNG 스트리밍 명령을 시작하는지 검증한다."""
    window = _create_window()
    try:
        device = AndroidDevice(serial="device-1", state="device", model="Pixel")
        destination = tmp_path / "screenshot" / "20260725143045_Pixel.png"
        window._adb_path = "adb"
        window._selected_device = lambda: device  # type: ignore[method-assign]

        with (
            patch.object(window, "_screenshot_path", return_value=destination),
            patch.object(window, "_run_file_command") as run_file_command,
        ):
            window.capture_screen()

        assert destination.parent.is_dir()
        run_file_command.assert_called_once_with(
            ["exec-out", "screencap", "-p"],
            destination,
            stdout_to_file=True,
            label="화면 캡처",
        )
        assert window._screenshot_in_progress
        assert not window.screenshot_button.isEnabled()
    finally:
        window.close()
