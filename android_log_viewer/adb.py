from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class AdbError(RuntimeError):
    """ADB를 찾을 수 없거나 명령 실행에 실패했을 때 발생하는 예외."""


@dataclass(frozen=True, slots=True)
class AndroidDevice:
    """ADB가 보고한 Android 기기의 식별 정보와 연결 상태를 표현한다."""

    serial: str
    state: str
    model: str = "Android device"
    product: str = ""
    transport_id: str = ""

    @property
    def display_name(self) -> str:
        """기기 선택 목록에 표시할 이름을 만든다.

        Returns:
            str: 모델명과 ADB 시리얼을 조합한 문자열.
        """
        return f"{self.model} ({self.serial})"


def find_adb() -> str:
    """PATH 또는 Android SDK 기본 설치 경로에서 adb를 찾는다.

    Raises:
        AdbError: 확인 가능한 모든 경로에서 adb 실행 파일을 찾지 못한 경우.

    Returns:
        str: 발견한 adb 실행 파일의 절대 경로.
    """
    executable = "adb.exe" if os.name == "nt" else "adb"
    found = shutil.which(executable)
    if found:
        return found

    sdk_roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    home = Path.home()
    sdk_roots.extend(
        [
            str(home / "Library" / "Android" / "sdk"),
            str(home / "AppData" / "Local" / "Android" / "Sdk"),
        ]
    )
    for root in filter(None, sdk_roots):
        candidate = Path(root) / "platform-tools" / executable
        if candidate.is_file():
            return str(candidate)
    raise AdbError(
        "ADB를 찾을 수 없습니다. Android SDK Platform-Tools를 설치하고 "
        "adb를 PATH에 추가하거나 ANDROID_HOME을 설정해 주세요."
    )


def parse_devices(output: str) -> list[AndroidDevice]:
    """``adb devices -l`` 원문을 기기 객체 목록으로 변환한다.

    Args:
        output (str): ADB가 출력한 기기 목록 원문.

    Returns:
        list[AndroidDevice]: 상태와 속성이 파싱된 Android 기기 목록.
    """
    devices: list[AndroidDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, state = fields[:2]
        attributes: dict[str, str] = {}
        for item in fields[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                attributes[key] = value
        devices.append(
            AndroidDevice(
                serial=serial,
                state=state,
                model=attributes.get("model", "Android device").replace("_", " "),
                product=attributes.get("product", ""),
                transport_id=attributes.get("transport_id", ""),
            )
        )
    return devices


def parse_packages(output: str) -> list[str]:
    """``pm list packages`` 출력을 정렬된 패키지 이름 목록으로 변환한다.

    Args:
        output (str): 패키지마다 ``package:`` 접두사가 붙은 명령 출력.

    Returns:
        list[str]: 중복을 제거하고 대소문자 구분 없이 정렬한 패키지명 목록.
    """
    packages = {
        line.strip().removeprefix("package:").strip()
        for line in output.splitlines()
        if line.strip().startswith("package:") and line.strip().removeprefix("package:").strip()
    }
    return sorted(packages, key=str.casefold)


def parse_processes(output: str) -> dict[str, set[str]]:
    """간결한 형식과 기존 Android ``ps`` 형식을 모두 분석한다.

    Args:
        output (str): ``adb shell ps -A`` 명령의 표준 출력.

    Returns:
        dict[str, set[str]]: 패키지명을 키로 하고 실행 중인 PID 집합을 값으로 갖는 매핑.
    """
    processes: dict[str, set[str]] = {}
    pid_index: int | None = None
    name_index: int | None = None
    for raw_line in output.splitlines():
        fields = raw_line.strip().split()
        upper_fields = [field.upper() for field in fields]
        if "PID" in upper_fields:
            pid_index = upper_fields.index("PID")
            name_index = upper_fields.index("NAME") if "NAME" in upper_fields else len(fields) - 1
            continue
        if len(fields) < 2:
            continue
        current_pid_index = pid_index if pid_index is not None else 0
        current_name_index = name_index if name_index is not None else len(fields) - 1
        if current_pid_index >= len(fields) or current_name_index >= len(fields):
            continue
        pid, name = fields[current_pid_index], fields[current_name_index]
        if not pid.isdigit():
            continue
        # Android 보조 프로세스는 ``package.name:service`` 형식을 사용한다.
        package = name.split(":", 1)[0]
        processes.setdefault(package, set()).add(pid)
    return processes


def list_devices(adb_path: str, timeout: float = 8.0) -> list[AndroidDevice]:
    """ADB로 현재 연결된 기기를 조회한다.

    Args:
        adb_path (str): 실행할 adb 파일 경로.
        timeout (float, optional): 명령 최대 대기 시간(초). 기본값은 8.0이다.

    Raises:
        AdbError: 명령 실행, 시간 초과 또는 ADB 응답에 실패한 경우.

    Returns:
        list[AndroidDevice]: 현재 ADB가 인식하는 기기 목록.
    """
    try:
        result = subprocess.run(
            [adb_path, "devices", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdbError(f"기기 목록을 가져오지 못했습니다: {exc}") from exc
    if result.returncode != 0:
        raise AdbError(result.stderr.strip() or "ADB 기기 조회에 실패했습니다.")
    return parse_devices(result.stdout)


_UNSAFE_FILENAME = re.compile(r"[^0-9A-Za-z가-힣._-]+")


def safe_filename(value: str) -> str:
    """운영체제에서 파일명으로 쓰기 어려운 문자를 밑줄로 치환한다.

    Args:
        value (str): 파일명으로 사용할 원본 문자열.

    Returns:
        str: macOS와 Windows에서 안전하게 사용할 수 있는 파일명 조각.
    """
    cleaned = _UNSAFE_FILENAME.sub("_", value).strip("._")
    return cleaned or "android_device"


def build_logcat_arguments(serial: str, start_timestamp: float) -> list[str]:
    """로그 시작 시점 이후의 항목만 수신하도록 logcat 인수를 구성한다.

    ``-T``에는 Unix epoch 시간을 전달하므로 기기 logcat 버퍼를 삭제하는
    ``-c`` 명령 없이 지정 시점 이후 로그만 읽을 수 있다.

    Args:
        serial (str): 로그를 수신할 기기의 ADB 시리얼.
        start_timestamp (float): 로그 수신 시작 직전의 Unix epoch 시간.

    Returns:
        list[str]: QProcess에 전달할 ADB 명령 인수 목록.
    """
    return [
        "-s",
        serial,
        "logcat",
        "-v",
        "threadtime",
        "-T",
        f"{start_timestamp:.3f}",
    ]


def build_logcat_dump_arguments() -> list[str]:
    """기기의 접근 가능한 모든 logcat 버퍼를 파일로 덤프할 인수를 구성한다.

    ``-d``는 현재 버퍼를 출력한 후 종료하며, 버퍼를 삭제하는 ``-c``는
    사용하지 않는다.

    Returns:
        list[str]: 기기 선택 인수 뒤에 전달할 logcat 명령 인수 목록.
    """
    return ["logcat", "-b", "all", "-d", "-v", "threadtime"]
