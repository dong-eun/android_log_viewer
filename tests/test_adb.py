from android_log_viewer.adb import (
    build_logcat_arguments,
    parse_devices,
    parse_packages,
    parse_processes,
    safe_filename,
)


def test_parse_devices_supports_ready_and_unauthorized_devices() -> None:
    """사용 가능 기기와 미승인 기기의 상태 및 속성이 모두 파싱되는지 검증한다."""
    output = """List of devices attached
R3CT123456 device product:dm3q model:SM_S918N device:dm3q transport_id:1
emulator-5554 unauthorized usb:1-2 transport_id:2
"""
    devices = parse_devices(output)

    assert len(devices) == 2
    assert devices[0].serial == "R3CT123456"
    assert devices[0].model == "SM S918N"
    assert devices[0].state == "device"
    assert devices[1].state == "unauthorized"


def test_safe_filename_removes_platform_unsafe_characters() -> None:
    """기기 이름의 파일명 비호환 문자가 안전한 밑줄로 치환되는지 검증한다."""
    assert safe_filename("Pixel 9 Pro / test:01") == "Pixel_9_Pro_test_01"


def test_parse_packages_sorts_and_removes_prefix() -> None:
    """패키지 출력의 접두사가 제거되고 이름순으로 정렬되는지 검증한다."""
    output = " package:com.example.zeta\npackage:com.android.chrome\n"

    assert parse_packages(output) == ["com.android.chrome", "com.example.zeta"]


def test_parse_processes_supports_android_ps_and_secondary_processes() -> None:
    """기본·보조 프로세스의 PID가 같은 패키지로 묶이는지 검증한다."""
    output = """USER PID PPID VSZ RSS WCHAN ADDR S NAME
u0_a123 1234 1 0 0 0 0 S com.example.app
u0_a123 1250 1 0 0 0 0 S com.example.app:worker
"""

    assert parse_processes(output) == {"com.example.app": {"1234", "1250"}}


def test_build_logcat_arguments_reads_only_from_start_without_clearing() -> None:
    """logcat 인수가 시작 시간은 포함하고 버퍼 삭제 옵션은 포함하지 않는지 검증한다."""
    arguments = build_logcat_arguments("device-123", 1_721_276_800.125)

    assert arguments == [
        "-s",
        "device-123",
        "logcat",
        "-v",
        "threadtime",
        "-T",
        "1721276800.125",
    ]
    assert "-c" not in arguments
