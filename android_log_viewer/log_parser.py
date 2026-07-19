from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


LOGCAT_PATTERN = re.compile(
    r"^(?P<date>\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEFA])\s+"
    r"(?P<tag>.*?):\s(?P<message>.*)$"
)


@dataclass(frozen=True, slots=True)
class LogEntry:
    """파싱된 logcat 한 줄과 필터 판정에 필요한 필드를 보관한다."""

    raw: str
    level: str = "?"
    pid: str = ""
    tag: str = ""
    message: str = ""

    def matches(self, query: str, minimum_level: str = "V") -> bool:
        """로그 레벨과 공백 기반 AND 검색 조건을 모두 만족하는지 판정한다.

        Args:
            query (str): 공백으로 구분된 필수 검색어 또는 따옴표로 묶인 문장.
            minimum_level (str, optional): 허용할 최소 로그 레벨. 기본값은 ``V``이다.

        Returns:
            bool: 레벨과 모든 검색어 조건을 만족하면 ``True``.
        """
        ranks = {"V": 0, "D": 1, "I": 2, "W": 3, "E": 4, "F": 5, "A": 5, "?": 0}
        if ranks.get(self.level, 0) < ranks.get(minimum_level, 0):
            return False
        if not query:
            return True
        raw = self.raw.casefold()
        return all(term.casefold() in raw for term in parse_search_terms(query))


def parse_search_terms(query: str) -> list[str]:
    """따옴표로 묶인 문장을 유지하면서 필터를 AND 검색어로 분리한다.

    Args:
        query (str): 사용자가 입력한 필터 문자열.

    Returns:
        list[str]: 각각 반드시 일치해야 하는 검색어 목록.
    """
    try:
        return shlex.split(query)
    except ValueError:
        # 사용자가 닫는 따옴표를 입력하는 중에도 필터가 계속 동작하도록 한다.
        return query.split()


def parse_logcat_line(line: str) -> LogEntry:
    """threadtime 형식의 logcat 한 줄을 구조화한다.

    Args:
        line (str): adb logcat에서 읽은 한 줄.

    Returns:
        LogEntry: 파싱된 로그 항목. 비정형 줄은 원문만 보존한다.
    """
    match = LOGCAT_PATTERN.match(line.rstrip("\r\n"))
    if not match:
        return LogEntry(raw=line.rstrip("\r\n"))
    values = match.groupdict()
    return LogEntry(
        raw=line.rstrip("\r\n"),
        level=values["level"],
        pid=values["pid"],
        tag=values["tag"].strip(),
        message=values["message"],
    )
