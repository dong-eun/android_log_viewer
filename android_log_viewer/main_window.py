from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
import re
import time
from typing import Callable

from PySide6.QtCore import QProcess, QStringListModel, QThreadPool, QTimer, Qt
from PySide6.QtGui import QColor, QCloseEvent, QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCompleter,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .adb import (
    AdbError,
    AndroidDevice,
    build_logcat_dump_arguments,
    build_logcat_arguments,
    find_adb,
    list_devices,
    parse_packages,
    parse_processes,
    safe_filename,
)
from .log_parser import LogEntry, LogFilter, parse_logcat_line, parse_search_terms
from .workers import AdbFileCommand, AdbTextCommand


LEVEL_COLORS = {
    "V": "#9CA3AF",
    "D": "#60A5FA",
    "I": "#34D399",
    "W": "#FBBF24",
    "E": "#F87171",
    "F": "#FB7185",
    "A": "#FB7185",
    "?": "#D1D5DB",
}
DEFAULT_MAX_LOG_LINES = 5_000
MAX_LOG_LINE_OPTIONS = (1_000, 5_000, 10_000, 20_000)
DISPLAY_BATCH_SIZE = 250
DISPLAY_BATCH_INTERVAL_MS = 50
PACKAGE_FILTER = re.compile(r"(?:^|\s)package:(?P<name>[0-9A-Za-z._-]*)", re.IGNORECASE)


class MainWindow(QMainWindow):
    """기기 선택, 실시간 로그, 필터 및 진단 파일 저장 기능을 제공하는 메인 창."""

    def __init__(self) -> None:
        """로그 상태와 백그라운드 작업을 초기화하고 UI 이벤트를 연결한다."""
        super().__init__()
        self.setWindowTitle("Android Log Viewer")
        self.resize(1280, 800)
        self._adb_path: str | None = None
        self._devices: list[AndroidDevice] = []
        self._max_log_lines = DEFAULT_MAX_LOG_LINES
        self._logs: deque[LogEntry] = deque(maxlen=self._max_log_lines)
        self._pending_display: deque[LogEntry] = deque(maxlen=self._max_log_lines)
        self._visible_count = 0
        self._session_serial = ""
        self._packages: list[str] = []
        self._package_pids: dict[str, set[str]] = {}
        self._cached_filter = LogFilter()
        self._filter_render_pending = False
        self._read_buffer = ""
        self._follow_tail = True
        self._scroll_update_guard = False
        self._thread_pool = QThreadPool.globalInstance()
        self._active_workers: set[AdbFileCommand | AdbTextCommand] = set()
        self._process_query_in_flight = False
        self._process_query_serial = ""

        self._log_process = QProcess(self)
        self._log_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._log_process.readyReadStandardOutput.connect(self._read_log_output)
        self._log_process.finished.connect(self._logcat_finished)
        self._log_process.errorOccurred.connect(self._logcat_error)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self._render_all_logs)
        self._display_timer = QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.setInterval(DISPLAY_BATCH_INTERVAL_MS)
        self._display_timer.timeout.connect(self._flush_display_batch)
        self._process_refresh_timer = QTimer(self)
        self._process_refresh_timer.setInterval(3000)
        self._process_refresh_timer.timeout.connect(self._load_process_metadata)

        self._build_ui()
        self._apply_style()
        QTimer.singleShot(0, self.refresh_devices)

    def _build_ui(self) -> None:
        """기기·필터·로그·저장 영역의 위젯을 생성하고 시그널을 연결한다."""
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Device"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(320)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        device_row.addWidget(self.device_combo, 1)
        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.clicked.connect(self.refresh_devices)
        device_row.addWidget(self.refresh_button)
        self.stream_button = QPushButton("로그 시작")
        self.stream_button.clicked.connect(self._toggle_logcat)
        device_row.addWidget(self.stream_button)
        self.clear_button = QPushButton("화면 로그 지우기")
        self.clear_button.setToolTip("화면과 앱의 로그 메모리만 비웁니다. 기기의 실제 로그는 유지됩니다.")
        self.clear_button.clicked.connect(self.clear_screen)
        device_row.addWidget(self.clear_button)
        layout.addLayout(device_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter"))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("공백은 AND 조건 · 예: package:com.example error timeout")
        self.filter_input.setClearButtonEnabled(True)
        self._package_model = QStringListModel(self)
        self._package_completer = QCompleter(self._package_model, self)
        self._package_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._package_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._package_completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self._package_completer.setMaxVisibleItems(12)
        self.filter_input.setCompleter(self._package_completer)
        self._package_completer.activated.connect(lambda _text: self._load_process_metadata())
        self.filter_input.textChanged.connect(self._filter_changed)
        filter_row.addWidget(self.filter_input, 1)
        filter_row.addWidget(QLabel("Level"))
        self.level_combo = QComboBox()
        for label, value in (("Verbose+", "V"), ("Debug+", "D"), ("Info+", "I"), ("Warn+", "W"), ("Error+", "E")):
            self.level_combo.addItem(label, value)
        self.level_combo.currentIndexChanged.connect(self._filter_option_changed)
        filter_row.addWidget(self.level_combo)
        filter_row.addWidget(QLabel("최대 로그"))
        self.max_lines_combo = QComboBox()
        for line_count in MAX_LOG_LINE_OPTIONS:
            self.max_lines_combo.addItem(f"{line_count:,}줄", line_count)
        self.max_lines_combo.setCurrentIndex(MAX_LOG_LINE_OPTIONS.index(DEFAULT_MAX_LOG_LINES))
        self.max_lines_combo.currentIndexChanged.connect(self._max_log_lines_changed)
        self.max_lines_combo.setToolTip("앱 메모리와 화면에 유지할 최대 로그 줄 수입니다.")
        filter_row.addWidget(self.max_lines_combo)
        layout.addLayout(filter_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setMaximumBlockCount(self._max_log_lines)
        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed_font.setPointSize(11)
        self.log_view.setFont(fixed_font)
        self.log_view.verticalScrollBar().valueChanged.connect(self._scroll_value_changed)
        layout.addWidget(self.log_view, 1)

        action_row = QHBoxLayout()
        self.save_button = QPushButton("기기 전체 로그 저장")
        self.save_button.clicked.connect(self.save_logs)
        action_row.addWidget(self.save_button)
        self.dumpsys_button = QPushButton("dumpsys 저장")
        self.dumpsys_button.clicked.connect(self.save_dumpsys)
        action_row.addWidget(self.dumpsys_button)
        self.bugreport_button = QPushButton("bugreport 저장")
        self.bugreport_button.clicked.connect(self.save_bugreport)
        action_row.addWidget(self.bugreport_button)
        action_row.addStretch()
        self.count_label = QLabel("0 lines")
        action_row.addWidget(self.count_label)
        layout.addLayout(action_row)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self._update_controls()

    def _apply_style(self) -> None:
        """로그 레벨 색상이 잘 보이도록 애플리케이션의 어두운 테마를 적용한다."""
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #111827; color: #E5E7EB; }
            QLineEdit, QComboBox, QPlainTextEdit {
                background: #0B1220; border: 1px solid #374151; border-radius: 5px;
                color: #E5E7EB; padding: 6px;
            }
            QPlainTextEdit { selection-background-color: #374151; }
            QPushButton {
                background: #1F2937; border: 1px solid #4B5563; border-radius: 5px;
                padding: 7px 12px; color: #F9FAFB;
            }
            QPushButton:hover { background: #374151; }
            QPushButton:pressed { background: #4B5563; }
            QPushButton:disabled { color: #6B7280; background: #172033; }
            QStatusBar { color: #9CA3AF; }
            QAbstractItemView {
                background: #161B24; color: #D1D5DB; border: 1px solid #4B5563;
                selection-background-color: #20283A; selection-color: #67E8F9;
                padding: 5px; outline: 0;
            }
            """
        )

    def refresh_devices(self) -> None:
        """ADB 기기 목록을 다시 읽고 기존 선택을 가능한 경우 유지한다."""
        current_serial = self._selected_serial()
        self.refresh_button.setEnabled(False)
        try:
            self._adb_path = self._adb_path or find_adb()
            devices = list_devices(self._adb_path)
        except AdbError as exc:
            self._devices = []
            self.device_combo.clear()
            self.statusBar().showMessage(str(exc))
            QMessageBox.warning(self, "ADB 오류", str(exc))
        else:
            self._devices = devices
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            selected_index = -1
            for index, device in enumerate(devices):
                suffix = "" if device.state == "device" else f" [{device.state}]"
                self.device_combo.addItem(device.display_name + suffix, device.serial)
                if device.serial == current_serial:
                    selected_index = index
            self.device_combo.blockSignals(False)
            if devices:
                self.device_combo.setCurrentIndex(max(0, selected_index))
                ready_count = sum(device.state == "device" for device in devices)
                self.statusBar().showMessage(f"연결된 기기 {len(devices)}대 (사용 가능 {ready_count}대)")
            else:
                self.statusBar().showMessage("연결된 Android 기기가 없습니다.")
        finally:
            self.refresh_button.setEnabled(True)
            self._update_controls()

    def _selected_device(self) -> AndroidDevice | None:
        """콤보 상자에서 현재 선택한 시리얼에 대응하는 기기를 찾는다.

        Returns:
            AndroidDevice | None: 선택된 기기 객체. 일치하는 기기가 없으면 ``None``.
        """
        serial = self._selected_serial()
        return next((device for device in self._devices if device.serial == serial), None)

    def _selected_serial(self) -> str:
        """현재 선택된 기기의 ADB 시리얼을 반환한다.

        Returns:
            str: 선택된 기기 시리얼. 선택이 없으면 빈 문자열.
        """
        return str(self.device_combo.currentData() or "")

    def _device_changed(self) -> None:
        """기기 변경 시 로그 세션과 메타데이터를 교체하고 실행 중인 logcat을 재연결한다."""
        serial = self._selected_serial()
        if serial != self._session_serial:
            self._reset_logs()
            self._session_serial = serial
        if self._log_process.state() != QProcess.ProcessState.NotRunning:
            self._stop_logcat()
            self._start_logcat()
        self._load_device_metadata()
        self._update_controls()

    def _toggle_logcat(self) -> None:
        """현재 logcat 상태에 따라 실시간 로그 수신을 시작하거나 중지한다."""
        if self._log_process.state() == QProcess.ProcessState.NotRunning:
            self._start_logcat()
        else:
            self._stop_logcat()

    def _start_logcat(self) -> None:
        """선택 기기에 현재 시점 이후의 logcat 수신을 시작한다.

        앱의 이전 수신 로그만 초기화하며 Android 기기의 logcat 버퍼는
        삭제하지 않는다.
        """
        device = self._selected_device()
        if not self._adb_path or not device or device.state != "device":
            QMessageBox.information(self, "기기 선택", "사용 가능한 Android 기기를 선택해 주세요.")
            return
        self._reset_logs()
        self._read_buffer = ""
        self._load_device_metadata()
        self._log_process.setProgram(self._adb_path)
        self._log_process.setArguments(build_logcat_arguments(device.serial, time.time()))
        self._log_process.start()
        if not self._log_process.waitForStarted(2000):
            QMessageBox.warning(self, "Logcat 오류", "logcat 프로세스를 시작하지 못했습니다.")
            return
        self.stream_button.setText("로그 중지")
        self._process_refresh_timer.start()
        self.statusBar().showMessage(f"{device.display_name} logcat 수신 중")
        self._update_controls()

    def _stop_logcat(self) -> None:
        """실행 중인 logcat을 정상 종료하고 응답하지 않으면 강제로 종료한다."""
        if self._log_process.state() == QProcess.ProcessState.NotRunning:
            return
        self._log_process.terminate()
        if not self._log_process.waitForFinished(1500):
            self._log_process.kill()
        self.stream_button.setText("로그 시작")
        self._process_refresh_timer.stop()
        self._update_controls()

    def _read_log_output(self) -> None:
        """logcat 출력 조각을 줄 단위로 저장하고 화면 출력 대기열에 추가한다."""
        chunk = bytes(self._log_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._read_buffer += chunk
        lines = self._read_buffer.split("\n")
        self._read_buffer = lines.pop()
        for line in lines:
            entry = parse_logcat_line(line.rstrip("\r"))
            if (
                not self._filter_render_pending
                and len(self._logs) == self._logs.maxlen
                and self._matches_filter(self._logs[0])
            ):
                self._visible_count -= 1
            self._logs.append(entry)
            if not self._filter_render_pending and self._matches_filter(entry):
                self._visible_count += 1
                self._pending_display.append(entry)
        if self._pending_display and not self._display_timer.isActive():
            self._display_timer.start()
        self._update_count()

    def _append_entries(self, entries: list[LogEntry]) -> None:
        """여러 로그를 하나의 화면 갱신 단위로 문서 끝에 추가한다.

        Args:
            entries (list[LogEntry]): 수신 순서대로 화면에 추가할 로그 항목 목록.
        """
        if not entries:
            return
        scrollbar = self.log_view.verticalScrollBar()
        old_value = scrollbar.value()
        follow_tail = self._follow_tail
        cursor = QTextCursor(self.log_view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        formats: dict[str, QTextCharFormat] = {}
        self._scroll_update_guard = True
        self.log_view.setUpdatesEnabled(False)
        try:
            for entry in entries:
                if entry.level not in formats:
                    text_format = QTextCharFormat()
                    text_format.setForeground(QColor(LEVEL_COLORS.get(entry.level, LEVEL_COLORS["?"])))
                    formats[entry.level] = text_format
                cursor.insertText(entry.raw + "\n", formats[entry.level])
            if follow_tail:
                scrollbar.setValue(scrollbar.maximum())
            else:
                scrollbar.setValue(min(old_value, scrollbar.maximum()))
        finally:
            self.log_view.setUpdatesEnabled(True)
            self._scroll_update_guard = False
        self._follow_tail = follow_tail

    def _flush_display_batch(self) -> None:
        """대기 중인 로그를 제한된 개수만 꺼내 한 번에 화면에 출력한다."""
        entries: list[LogEntry] = []
        for _ in range(min(DISPLAY_BATCH_SIZE, len(self._pending_display))):
            entries.append(self._pending_display.popleft())
        self._append_entries(entries)
        if self._pending_display:
            self._display_timer.start()

    def _scroll_value_changed(self, value: int) -> None:
        """사용자 스크롤 위치에 따라 자동 로그 추적 상태를 갱신한다.

        Args:
            value (int): 세로 스크롤바의 현재 위치 값.
        """
        if self._scroll_update_guard:
            return
        scrollbar = self.log_view.verticalScrollBar()
        self._follow_tail = value >= scrollbar.maximum() - 1

    def _matches_filter(self, entry: LogEntry) -> bool:
        """미리 계산한 필터 조건을 로그 한 줄에 적용한다.

        Args:
            entry (LogEntry): 필터 적용 여부를 판정할 로그 항목.

        Returns:
            bool: 현재 UI 필터 조건을 모두 만족하면 ``True``.
        """
        return self._cached_filter.matches(entry)

    def _cache_filter(self) -> None:
        """UI의 필터 문자열과 패키지 PID를 반복 사용 가능한 조건으로 변환한다."""
        query = self.filter_input.text().strip()
        package_match = PACKAGE_FILTER.search(query)
        package_pids: frozenset[str] | None = None
        if package_match:
            package_prefix = package_match.group("name").casefold()
            matching_pids: set[str] = set()
            for package, pids in self._package_pids.items():
                if package.casefold().startswith(package_prefix):
                    matching_pids.update(pids)
            package_pids = frozenset(matching_pids)
            query = (query[: package_match.start()] + query[package_match.end() :]).strip()
        self._cached_filter = LogFilter(
            terms=tuple(term.casefold() for term in parse_search_terms(query)),
            minimum_level=str(self.level_combo.currentData() or "V"),
            package_pids=package_pids,
        )

    def _render_all_logs(self) -> None:
        """현재 필터로 보존 중인 로그를 다시 그리며 사용자의 스크롤 상태를 유지한다."""
        self._filter_render_pending = False
        self._display_timer.stop()
        self._pending_display.clear()
        scrollbar = self.log_view.verticalScrollBar()
        old_value = scrollbar.value()
        follow_tail = self._follow_tail
        self._scroll_update_guard = True
        self.log_view.setUpdatesEnabled(False)
        try:
            self.log_view.clear()
            entries = [entry for entry in self._logs if self._matches_filter(entry)]
            self._visible_count = len(entries)
            self._append_entries(entries)
            if follow_tail:
                scrollbar.setValue(scrollbar.maximum())
            else:
                scrollbar.setValue(min(old_value, scrollbar.maximum()))
        finally:
            self.log_view.setUpdatesEnabled(True)
            self._scroll_update_guard = False
        self._follow_tail = follow_tail
        self._update_count()

    def clear_screen(self) -> None:
        """앱이 수집한 화면용 로그와 출력 대기열을 비워 메모리를 해제한다."""
        self._display_timer.stop()
        self._pending_display.clear()
        self._logs.clear()
        self._visible_count = 0
        self._scroll_update_guard = True
        try:
            self.log_view.clear()
        finally:
            self._scroll_update_guard = False
        self._follow_tail = True
        self._update_count()
        self.statusBar().showMessage("화면과 앱 메모리를 비웠습니다. 기기 로그는 삭제되지 않았습니다.", 5000)

    def _reset_logs(self) -> None:
        """기기를 변경할 때 새로운 메모리 로그 세션을 시작한다."""
        self._display_timer.stop()
        self._pending_display.clear()
        self._logs.clear()
        self._visible_count = 0
        self._scroll_update_guard = True
        try:
            self.log_view.clear()
        finally:
            self._scroll_update_guard = False
        self._follow_tail = True
        self._update_count()

    def _filter_changed(self, text: str) -> None:
        """필터 입력을 지연 적용하고 패키지 자동완성을 제어한다.

        Args:
            text (str): 필터 입력창의 최신 문자열.
        """
        self._prepare_filter_render()
        if text.casefold().startswith("package:") and " " not in text:
            self._package_completer.setCompletionPrefix(text)
            self._package_completer.complete()
        else:
            self._package_completer.popup().hide()

    def _filter_option_changed(self) -> None:
        """로그 레벨 선택이 바뀌면 캐시를 갱신하고 전체 로그를 다시 그린다."""
        self._prepare_filter_render()

    def _prepare_filter_render(self) -> None:
        """필터 변경 중 이전 조건의 출력이 섞이지 않도록 대기열을 초기화한다."""
        self._cache_filter()
        self._filter_render_pending = True
        self._display_timer.stop()
        self._pending_display.clear()
        self._filter_timer.start()

    def _max_log_lines_changed(self) -> None:
        """선택한 최대 줄 수에 맞춰 로그 저장소와 Qt 문서 제한을 즉시 조정한다."""
        maximum = int(self.max_lines_combo.currentData() or DEFAULT_MAX_LOG_LINES)
        if maximum == self._max_log_lines:
            return
        self._max_log_lines = maximum
        self._logs = deque(self._logs, maxlen=maximum)
        self._pending_display = deque(maxlen=maximum)
        self.log_view.setMaximumBlockCount(maximum)
        self._render_all_logs()
        self.statusBar().showMessage(f"최대 로그를 {maximum:,}줄로 변경했습니다.", 4000)

    def _load_device_metadata(self) -> None:
        """선택 기기의 설치 패키지와 실행 프로세스 정보를 비동기로 요청한다."""
        device = self._selected_device()
        self._packages = []
        self._package_pids = {}
        self._cache_filter()
        self._package_model.setStringList([])
        if not self._adb_path or not device or device.state != "device":
            return
        serial = device.serial
        self._run_text_query(
            [self._adb_path, "-s", serial, "shell", "pm", "list", "packages"],
            lambda output: self._packages_loaded(serial, output),
        )
        self._load_process_metadata()

    def _load_process_metadata(self) -> None:
        """패키지 필터의 PID 매핑을 최신화하기 위해 실행 프로세스 목록을 요청한다."""
        device = self._selected_device()
        if not self._adb_path or not device or device.state != "device":
            return
        if self._process_query_in_flight:
            return
        serial = device.serial
        self._process_query_in_flight = True
        self._process_query_serial = serial
        self._run_text_query(
            [self._adb_path, "-s", serial, "shell", "ps", "-A"],
            lambda output: self._processes_loaded(serial, output),
            lambda: self._process_query_finished(serial),
        )

    def _run_text_query(
        self,
        command: list[str],
        on_complete: Callable[[str], None],
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """짧은 ADB 조회를 스레드 풀에 등록한다.

        Args:
            command (list[str]): 실행 파일을 포함한 전체 ADB 명령.
            on_complete (Callable[[str], None]): 표준 출력을 전달받을 완료 콜백.
            on_finished (Callable[[], None] | None, optional): 성공 여부와 무관하게 실행할 정리 콜백.
        """
        worker = AdbTextCommand(command)
        self._active_workers.add(worker)
        worker.signals.completed.connect(
            lambda output, item=worker: self._text_query_finished(item, on_complete, on_finished, output)
        )
        worker.signals.failed.connect(
            lambda _error, item=worker: self._text_query_failed(item, on_finished)
        )
        self._thread_pool.start(worker)

    def _text_query_finished(
        self,
        worker: AdbTextCommand,
        callback: Callable[[str], None],
        on_finished: Callable[[], None] | None,
        output: str,
    ) -> None:
        """완료된 문자열 작업을 정리하고 결과 콜백을 실행한다.

        Args:
            worker (AdbTextCommand): 완료되어 참조를 해제할 작업 객체.
            callback (Callable[[str], None]): 명령 결과를 처리할 요청별 콜백.
            on_finished (Callable[[], None] | None): 작업 상태를 정리할 선택 콜백.
            output (str): ADB 명령의 표준 출력.
        """
        self._active_workers.discard(worker)
        try:
            callback(output)
        finally:
            if on_finished:
                on_finished()

    def _text_query_failed(
        self,
        worker: AdbTextCommand,
        on_finished: Callable[[], None] | None,
    ) -> None:
        """실패한 문자열 조회 참조와 요청별 실행 상태를 정리한다.

        Args:
            worker (AdbTextCommand): 실패하여 참조를 해제할 작업 객체.
            on_finished (Callable[[], None] | None): 작업 상태를 정리할 선택 콜백.
        """
        self._active_workers.discard(worker)
        if on_finished:
            on_finished()

    def _process_query_finished(self, serial: str) -> None:
        """프로세스 조회 잠금을 해제하고 기기가 바뀌었다면 새 조회를 시작한다.

        Args:
            serial (str): 완료된 프로세스 조회의 기기 시리얼.
        """
        if serial != self._process_query_serial:
            return
        self._process_query_in_flight = False
        self._process_query_serial = ""
        if serial != self._selected_serial():
            QTimer.singleShot(0, self._load_process_metadata)

    def _packages_loaded(self, serial: str, output: str) -> None:
        """현재 기기의 패키지 조회 결과만 자동완성 모델에 반영한다.

        Args:
            serial (str): 조회를 시작했을 때 선택되어 있던 기기 시리얼.
            output (str): ``pm list packages`` 명령의 표준 출력.
        """
        if serial != self._selected_serial():
            return
        self._packages = parse_packages(output)
        self._package_model.setStringList([f"package:{package}" for package in self._packages])

    def _processes_loaded(self, serial: str, output: str) -> None:
        """현재 기기의 프로세스 정보를 패키지별 PID 집합으로 저장한다.

        Args:
            serial (str): 조회를 시작했을 때 선택되어 있던 기기 시리얼.
            output (str): ``ps -A`` 명령의 표준 출력.
        """
        if serial != self._selected_serial():
            return
        self._package_pids = parse_processes(output)
        self._cache_filter()
        if "package:" in self.filter_input.text().casefold():
            self._render_all_logs()

    def _update_count(self) -> None:
        """화면에 표시된 로그 수와 앱이 수집한 전체 로그 수를 갱신한다."""
        self.count_label.setText(f"{self._visible_count:,} / {len(self._logs):,} lines")

    def _default_name(self, extension: str) -> str:
        """현재 시각과 기기 모델을 조합해 기본 파일명을 만든다.

        Args:
            extension (str): 점을 제외한 파일 확장자.

        Returns:
            str: ``YYYYMMDDhhmm_기기명.확장자`` 형식의 파일명.
        """
        device = self._selected_device()
        name = safe_filename(device.model if device else "android_device")
        return f"{datetime.now():%Y%m%d%H%M}_{name}.{extension}"

    def save_logs(self) -> None:
        """화면 내용과 무관하게 기기의 전체 logcat 버퍼를 UTF-8 TXT로 저장한다."""
        path, _ = QFileDialog.getSaveFileName(self, "로그 저장", self._default_name("txt"), "Text files (*.txt)")
        if path:
            self._run_file_command(
                build_logcat_dump_arguments(),
                Path(path),
                stdout_to_file=True,
                label="기기 전체 logcat",
            )

    def save_dumpsys(self) -> None:
        """저장 경로를 선택받아 현재 기기의 전체 dumpsys 출력을 생성한다."""
        path, _ = QFileDialog.getSaveFileName(self, "dumpsys 저장", self._default_name("txt"), "Text files (*.txt)")
        if path:
            self._run_file_command(["shell", "dumpsys"], Path(path), stdout_to_file=True, label="dumpsys")

    def save_bugreport(self) -> None:
        """저장 경로를 선택받아 현재 기기의 bugreport ZIP 생성을 시작한다."""
        path, _ = QFileDialog.getSaveFileName(self, "bugreport 저장", self._default_name("zip"), "ZIP files (*.zip)")
        if path:
            destination = Path(path)
            if destination.suffix.lower() != ".zip":
                destination = destination.with_suffix(".zip")
            self._run_file_command(["bugreport", str(destination)], destination, stdout_to_file=False, label="bugreport")

    def _run_file_command(self, arguments: list[str], destination: Path, stdout_to_file: bool, label: str) -> None:
        """선택 기기용 ADB 파일 명령을 백그라운드에서 실행한다.

        Args:
            arguments (list[str]): 기기 선택 인수 뒤에 추가할 ADB 명령 인수.
            destination (Path): 결과 파일 저장 경로.
            stdout_to_file (bool): 표준 출력을 대상 파일에 직접 기록할지 여부.
            label (str): 상태 및 알림 메시지에 표시할 작업 이름.
        """
        device = self._selected_device()
        if not self._adb_path or not device or device.state != "device":
            QMessageBox.information(self, "기기 선택", "사용 가능한 Android 기기를 선택해 주세요.")
            return
        command = [self._adb_path, "-s", device.serial, *arguments]
        worker = AdbFileCommand(command, destination, stdout_to_file)
        self._active_workers.add(worker)
        worker.signals.completed.connect(lambda path, item=worker: self._command_finished(item, label, path))
        worker.signals.failed.connect(lambda error, item=worker: self._command_failed(item, label, error))
        self._thread_pool.start(worker)
        self.statusBar().showMessage(f"{label} 생성 중… 큰 파일은 몇 분 정도 걸릴 수 있습니다.")

    def _command_finished(self, worker: AdbFileCommand, label: str, path: str) -> None:
        """완료된 진단 파일 작업을 정리하고 저장 경로를 알린다.

        Args:
            worker (AdbFileCommand): 완료되어 참조를 해제할 작업 객체.
            label (str): 사용자에게 표시할 작업 이름.
            path (str): 생성된 결과 파일 경로.
        """
        self._active_workers.discard(worker)
        self.statusBar().showMessage(f"{label} 저장 완료: {path}", 8000)
        QMessageBox.information(self, "저장 완료", f"{label} 파일을 저장했습니다.\n{path}")

    def _command_failed(self, worker: AdbFileCommand, label: str, error: str) -> None:
        """실패한 진단 파일 작업을 정리하고 오류를 표시한다.

        Args:
            worker (AdbFileCommand): 실패하여 참조를 해제할 작업 객체.
            label (str): 사용자에게 표시할 작업 이름.
            error (str): 작업 스레드가 전달한 오류 메시지.
        """
        self._active_workers.discard(worker)
        self.statusBar().showMessage(f"{label} 저장 실패", 6000)
        QMessageBox.critical(self, f"{label} 실패", error)

    def _logcat_finished(self) -> None:
        """logcat 종료 후 버튼과 프로세스 정보 갱신 타이머를 중지 상태로 맞춘다."""
        self.stream_button.setText("로그 시작")
        self._process_refresh_timer.stop()
        self._update_controls()

    def _logcat_error(self, _error: QProcess.ProcessError) -> None:
        """Qt가 보고한 logcat 프로세스 오류를 상태 표시줄에 노출한다.

        Args:
            _error (QProcess.ProcessError): Qt 프로세스 오류 코드. 상세 문구는 프로세스에서 조회한다.
        """
        if self._log_process.errorString():
            self.statusBar().showMessage(f"Logcat 오류: {self._log_process.errorString()}", 6000)

    def _update_controls(self) -> None:
        """기기 연결 및 logcat 실행 상태에 따라 버튼 활성화와 문구를 갱신한다."""
        device = self._selected_device()
        ready = bool(device and device.state == "device" and self._adb_path)
        running = self._log_process.state() != QProcess.ProcessState.NotRunning
        self.stream_button.setEnabled(ready)
        self.stream_button.setText("로그 중지" if running else "로그 시작")
        self.dumpsys_button.setEnabled(ready)
        self.bugreport_button.setEnabled(ready)
        self.save_button.setEnabled(ready)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API 명명 규칙
        """자식 logcat 프로세스를 종료하고 창 닫기 이벤트를 승인한다.

        Args:
            event (QCloseEvent): Qt가 전달한 창 닫기 이벤트.
        """
        self._display_timer.stop()
        self._stop_logcat()
        event.accept()
