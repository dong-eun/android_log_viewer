from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class CommandSignals(QObject):
    """파일 생성 작업의 완료 경로 또는 실패 메시지를 UI 스레드로 전달한다."""

    completed = Signal(str)
    failed = Signal(str)


class TextCommandSignals(QObject):
    """문자열 조회 작업의 결과 또는 실패 메시지를 UI 스레드로 전달한다."""

    completed = Signal(str)
    failed = Signal(str)


class AdbTextCommand(QRunnable):
    """Qt를 차단하지 않고 짧은 ADB 조회를 실행하여 디코딩된 문자열을 반환한다."""

    def __init__(self, command: list[str], timeout: int = 20) -> None:
        """ADB 문자열 조회 작업을 초기화한다.

        Args:
            command (list[str]): 실행 파일을 포함한 ADB 명령 인수 목록.
            timeout (int, optional): 명령 최대 대기 시간(초). 기본값은 20이다.
        """
        super().__init__()
        self.command = command
        self.timeout = timeout
        self.signals = TextCommandSignals()

    @Slot()
    def run(self) -> None:
        """ADB 명령을 실행한 뒤 표준 출력을 문자열로 변환해 시그널로 전달한다.

        명령 오류와 예외는 호출 스레드로 전파하지 않고 ``failed`` 시그널로
        전달한다.
        """
        try:
            result = subprocess.run(
                self.command,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
            if result.returncode != 0:
                error = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(error or "ADB 조회 명령에 실패했습니다.")
            output = result.stdout.decode("utf-8", errors="replace")
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.completed.emit(output)


class AdbFileCommand(QRunnable):
    """오래 걸릴 수 있는 ADB 명령을 UI 차단 없이 실행한다."""

    def __init__(self, command: list[str], destination: Path, stdout_to_file: bool) -> None:
        """ADB 파일 생성 작업을 초기화한다.

        Args:
            command (list[str]): 실행 파일을 포함한 ADB 명령 인수 목록.
            destination (Path): 결과 파일을 저장할 경로.
            stdout_to_file (bool): 표준 출력을 대상 파일에 직접 기록할지 여부.
        """
        super().__init__()
        self.command = command
        self.destination = destination
        self.stdout_to_file = stdout_to_file
        self.signals = CommandSignals()

    @Slot()
    def run(self) -> None:
        """ADB 파일 명령을 실행하고 성공 시 경로, 실패 시 오류를 시그널로 전달한다.

        실패한 직접 출력 작업이 불완전한 파일을 남기지 않도록 대상 파일을
        가능한 경우 삭제한다.
        """
        try:
            if self.stdout_to_file:
                with self.destination.open("wb") as output:
                    result = subprocess.run(
                        self.command,
                        stdout=output,
                        stderr=subprocess.PIPE,
                        timeout=600,
                        check=False,
                    )
            else:
                result = subprocess.run(
                    self.command,
                    capture_output=True,
                    timeout=1200,
                    check=False,
                )
            if result.returncode != 0:
                stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(stderr or f"ADB 명령이 종료 코드 {result.returncode}로 실패했습니다.")
            if not self.destination.exists():
                raise RuntimeError("ADB 명령은 완료되었지만 결과 파일이 생성되지 않았습니다.")
        except Exception as exc:  # 작업 스레드에서 발생한 모든 오류를 UI에 전달한다.
            if self.stdout_to_file and self.destination.exists():
                try:
                    self.destination.unlink()
                except OSError:
                    pass
            self.signals.failed.emit(str(exc))
        else:
            self.signals.completed.emit(str(self.destination))
