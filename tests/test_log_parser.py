import pytest

from android_log_viewer.log_parser import LogFilter, parse_logcat_line, parse_search_terms


def test_parse_threadtime_logcat_line() -> None:
    """threadtime 로그에서 레벨, PID, 태그와 메시지가 추출되는지 검증한다."""
    entry = parse_logcat_line("07-18 10:44:12.123  1234  5678 E ActivityManager: Process crashed")

    assert entry.level == "E"
    assert entry.pid == "1234"
    assert entry.tag == "ActivityManager"
    assert entry.message == "Process crashed"


def test_filter_is_case_insensitive_and_respects_level() -> None:
    """텍스트 필터가 대소문자를 무시하고 최소 레벨을 적용하는지 검증한다."""
    entry = parse_logcat_line("07-18 10:44:12.123  1234  5678 W MyTag: Low memory")

    assert entry.matches("LOW MEMORY", "I")
    assert not entry.matches("memory", "E")


def test_unstructured_adb_output_is_preserved() -> None:
    """파싱할 수 없는 logcat 구분선도 손실 없이 원문으로 유지되는지 검증한다."""
    entry = parse_logcat_line("--------- beginning of main")

    assert entry.raw == "--------- beginning of main"
    assert entry.level == "?"


def test_space_separated_terms_use_and_matching() -> None:
    """공백으로 나눈 검색어가 모두 포함되어야 일치하는 AND 조건인지 검증한다."""
    entry = parse_logcat_line("07-18 10:44:12.123  1234  5678 E Network: request timeout while connecting")

    assert entry.matches("request timeout")
    assert entry.matches("timeout Network")
    assert not entry.matches("request success")


def test_quoted_phrase_is_preserved_as_one_and_term() -> None:
    """따옴표로 감싼 공백 포함 문장이 하나의 검색 조건으로 유지되는지 검증한다."""
    assert parse_search_terms('Network "request timeout"') == ["Network", "request timeout"]


def test_log_filter_uses_precomputed_casefolded_terms() -> None:
    """미리 계산한 검색어가 로그마다 다시 파싱되지 않고 AND 조건으로 적용되는지 검증한다."""
    entry = parse_logcat_line("07-18 10:44:12.123  1234  5678 E Network: Request Timeout")
    cached_filter = LogFilter(terms=("network", "timeout"), minimum_level="W", package_pids=frozenset({"1234"}))

    assert cached_filter.matches(entry)


@pytest.mark.parametrize(
    ("query", "matching_raw", "wrong_raw"),
    [
        ("Network", "E Network: request timeout", "E Database: request timeout"),
        ("Network timeout", "E Network: request timeout", "E Network: request success"),
        ("Network & timeout", "E Network: request timeout", "E Network: request success"),
        ("ERROR | WARNING", "E App: WARNING while running", "I App: Ignoring event"),
        ("ERROR & !timeout", "E App: ERROR while running", "E App: ERROR timeout"),
        ("!DEBUG", "I App: INFO message", "D App: DEBUG message"),
        ("(ERROR | WARNING) & Network", "E Network: WARNING message", "E App: WARNING message"),
        ("(ERROR | WARNING) & !Network", "E App: ERROR message", "E Network: ERROR message"),
        ("regex:ERROR.*Network", "E App: ERROR while reading Network", "E App: Network before ERROR"),
        ("regex:ERROR|WARNING", "E App: WARNING message", "I App: Ignoring event"),
        ("regex:user_id=\\d+", "I App: user_id=1234", "I App: user_id=guest"),
    ],
)
def test_log_filter_supports_logical_and_regex_queries(
    query: str,
    matching_raw: str,
    wrong_raw: str,
) -> None:
    """논리 연산자와 정규식 검색 문법이 요구사항대로 동작하는지 검증한다.

    Args:
        query: 사용자 입력 필터 검색식.
        matching_raw: 검색식에 일치해야 하는 로그 원문.
        wrong_raw: 검색식에 일치하지 않아야 하는 로그 원문.
    """
    log_filter = LogFilter.from_query(query)

    assert not log_filter.error_message
    assert log_filter.matches(parse_logcat_line(f"07-18 10:44:12.123  1234  5678 {matching_raw}"))
    assert not log_filter.matches(parse_logcat_line(f"07-18 10:44:12.123  1234  5678 {wrong_raw}"))


def test_invalid_filter_query_does_not_raise() -> None:
    """잘못된 필터 검색식이 예외 대신 오류 메시지로 보관되는지 검증한다."""
    log_filter = LogFilter.from_query("(ERROR | WARNING")

    assert log_filter.error_message == "닫는 괄호가 필요합니다."
    assert not log_filter.matches(parse_logcat_line("07-18 10:44:12.123  1234  5678 E App: ERROR"))


def test_invalid_regex_query_does_not_raise() -> None:
    """잘못된 정규식 검색식이 예외 대신 오류 메시지로 보관되는지 검증한다."""
    log_filter = LogFilter.from_query("regex:[")

    assert log_filter.error_message.startswith("Regex 오류:")
    assert not log_filter.matches(parse_logcat_line("07-18 10:44:12.123  1234  5678 E App: ERROR"))
