from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Protocol


LOGCAT_PATTERN = re.compile(
    r"^(?P<date>\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEFA])\s+"
    r"(?P<tag>.*?):\s(?P<message>.*)$"
)
LEVEL_RANKS = {"V": 0, "D": 1, "I": 2, "W": 3, "E": 4, "F": 5, "A": 5, "?": 0}


class FilterSyntaxError(ValueError):
    """필터 검색식 문법이 올바르지 않을 때 발생한다."""


class _FilterExpression(Protocol):
    """로그 항목에 적용할 필터 표현식의 공통 인터페이스를 정의한다."""

    def matches(self, entry: LogEntry) -> bool:
        """로그 항목이 표현식을 만족하는지 판정한다.

        Args:
            entry: 필터를 적용할 로그 항목.

        Returns:
            표현식을 만족하면 ``True``.
        """


@dataclass(frozen=True, slots=True)
class _TermExpression:
    """일반 문자열 포함 조건을 표현한다."""

    term: str

    def matches(self, entry: LogEntry) -> bool:
        """로그 원문에 검색어가 포함되는지 판정한다.

        Args:
            entry: 필터를 적용할 로그 항목.

        Returns:
            검색어가 포함되면 ``True``.
        """
        return self.term in entry.raw.casefold()


@dataclass(frozen=True, slots=True)
class _RegexExpression:
    """정규식 검색 조건을 표현한다."""

    pattern: re.Pattern[str]

    def matches(self, entry: LogEntry) -> bool:
        """로그 원문이 정규식과 일치하는지 판정한다.

        Args:
            entry: 필터를 적용할 로그 항목.

        Returns:
            정규식이 일치하면 ``True``.
        """
        return bool(self.pattern.search(entry.raw))


@dataclass(frozen=True, slots=True)
class _NotExpression:
    """하위 표현식의 결과를 반전한다."""

    expression: _FilterExpression

    def matches(self, entry: LogEntry) -> bool:
        """하위 표현식의 반대 결과를 반환한다.

        Args:
            entry: 필터를 적용할 로그 항목.

        Returns:
            하위 표현식이 일치하지 않으면 ``True``.
        """
        return not self.expression.matches(entry)


@dataclass(frozen=True, slots=True)
class _AndExpression:
    """두 표현식을 AND 조건으로 묶는다."""

    left: _FilterExpression
    right: _FilterExpression

    def matches(self, entry: LogEntry) -> bool:
        """두 표현식이 모두 일치하는지 판정한다.

        Args:
            entry: 필터를 적용할 로그 항목.

        Returns:
            두 표현식이 모두 일치하면 ``True``.
        """
        return self.left.matches(entry) and self.right.matches(entry)


@dataclass(frozen=True, slots=True)
class _OrExpression:
    """두 표현식을 OR 조건으로 묶는다."""

    left: _FilterExpression
    right: _FilterExpression

    def matches(self, entry: LogEntry) -> bool:
        """두 표현식 중 하나 이상이 일치하는지 판정한다.

        Args:
            entry: 필터를 적용할 로그 항목.

        Returns:
            두 표현식 중 하나 이상이 일치하면 ``True``.
        """
        return self.left.matches(entry) or self.right.matches(entry)


@dataclass(frozen=True, slots=True)
class _Token:
    """필터 검색식 파싱에 사용할 토큰을 보관한다."""

    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class LogFilter:
    """반복 파싱 없이 재사용할 수 있는 로그 필터 조건을 보관한다."""

    terms: tuple[str, ...] = ()
    minimum_level: str = "V"
    package_pids: frozenset[str] | None = None
    expression: _FilterExpression | None = None
    error_message: str = ""

    @classmethod
    def from_query(
        cls,
        query: str,
        minimum_level: str = "V",
        package_pids: frozenset[str] | None = None,
    ) -> LogFilter:
        """사용자 입력 필터 문자열을 반복 사용 가능한 조건으로 변환한다.

        Args:
            query: 사용자가 입력한 필터 문자열.
            minimum_level: 허용할 최소 로그 레벨.
            package_pids: 패키지 필터에 매칭된 PID 목록.

        Returns:
            파싱된 필터 조건. 문법 오류가 있으면 ``error_message``에 사유를 보관한다.
        """
        stripped_query = query.strip()
        if not stripped_query:
            return cls(minimum_level=minimum_level, package_pids=package_pids)
        if stripped_query.casefold().startswith("regex:"):
            return cls._from_regex(stripped_query[6:], minimum_level, package_pids)
        try:
            expression = _FilterParser(_tokenize_filter_query(stripped_query)).parse()
        except FilterSyntaxError as exc:
            return cls(minimum_level=minimum_level, package_pids=package_pids, error_message=str(exc))
        return cls(minimum_level=minimum_level, package_pids=package_pids, expression=expression)

    @classmethod
    def _from_regex(
        cls,
        pattern_text: str,
        minimum_level: str,
        package_pids: frozenset[str] | None,
    ) -> LogFilter:
        """정규식 필터 조건을 생성한다.

        Args:
            pattern_text: ``regex:`` 뒤에 입력된 정규식 문자열.
            minimum_level: 허용할 최소 로그 레벨.
            package_pids: 패키지 필터에 매칭된 PID 목록.

        Returns:
            정규식 필터 조건. 정규식 오류가 있으면 ``error_message``에 사유를 보관한다.
        """
        try:
            pattern = re.compile(pattern_text, re.IGNORECASE)
        except re.error as exc:
            return cls(minimum_level=minimum_level, package_pids=package_pids, error_message=f"Regex 오류: {exc}")
        return cls(minimum_level=minimum_level, package_pids=package_pids, expression=_RegexExpression(pattern))

    def matches(self, entry: LogEntry) -> bool:
        """캐시된 레벨, 패키지 PID와 검색식을 로그에 적용한다.

        Args:
            entry (LogEntry): 필터 적용 여부를 판정할 로그 항목.

        Returns:
            bool: 저장된 모든 조건을 만족하면 ``True``.
        """
        if LEVEL_RANKS.get(entry.level, 0) < LEVEL_RANKS.get(self.minimum_level, 0):
            return False
        if self.package_pids is not None and (not entry.pid or entry.pid not in self.package_pids):
            return False
        if self.error_message:
            return False
        if self.expression is not None:
            return self.expression.matches(entry)
        if not self.terms:
            return True
        raw = entry.raw.casefold()
        return all(term in raw for term in self.terms)


@dataclass(frozen=True, slots=True)
class LogEntry:
    """파싱된 logcat 한 줄과 필터 판정에 필요한 필드를 보관한다."""

    raw: str
    level: str = "?"
    pid: str = ""
    tag: str = ""
    message: str = ""

    def matches(self, query: str, minimum_level: str = "V") -> bool:
        """로그 레벨과 필터 검색식 조건을 모두 만족하는지 판정한다.

        Args:
            query (str): 일반 검색어, 논리 연산자 또는 ``regex:`` 검색식.
            minimum_level (str, optional): 허용할 최소 로그 레벨. 기본값은 ``V``이다.

        Returns:
            bool: 레벨과 검색식 조건을 만족하면 ``True``.
        """
        log_filter = LogFilter.from_query(query, minimum_level=minimum_level)
        return log_filter.matches(self)


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


def _tokenize_filter_query(query: str) -> list[_Token]:
    """필터 검색식을 연산자와 검색어 토큰으로 분리한다.

    Args:
        query: 사용자가 입력한 필터 문자열.

    Returns:
        파싱에 사용할 토큰 목록.

    Raises:
        FilterSyntaxError: 닫히지 않은 따옴표가 있는 경우.
    """
    tokens: list[_Token] = []
    index = 0
    while index < len(query):
        char = query[index]
        if char.isspace():
            index += 1
            continue
        if char in "&|!()":
            tokens.append(_Token(char, char))
            index += 1
            continue
        if char in "'\"":
            value, index = _read_quoted_token(query, index)
            if value:
                tokens.append(_Token("TERM", value.casefold()))
            continue
        start = index
        while index < len(query) and not query[index].isspace() and query[index] not in "&|!()":
            index += 1
        tokens.append(_Token("TERM", query[start:index].casefold()))
    return tokens


def _read_quoted_token(query: str, start: int) -> tuple[str, int]:
    """따옴표로 감싼 검색어를 하나의 토큰으로 읽는다.

    Args:
        query: 사용자가 입력한 필터 문자열.
        start: 여는 따옴표의 위치.

    Returns:
        검색어와 다음 읽기 위치.

    Raises:
        FilterSyntaxError: 닫는 따옴표가 없는 경우.
    """
    quote = query[start]
    index = start + 1
    chars: list[str] = []
    while index < len(query):
        char = query[index]
        if char == "\\" and index + 1 < len(query):
            chars.append(query[index + 1])
            index += 2
            continue
        if char == quote:
            return "".join(chars), index + 1
        chars.append(char)
        index += 1
    raise FilterSyntaxError("닫히지 않은 따옴표가 있습니다.")


class _FilterParser:
    """토큰 목록을 필터 표현식 트리로 변환한다."""

    def __init__(self, tokens: list[_Token]) -> None:
        """필터 파서를 초기화한다.

        Args:
            tokens: 검색식에서 추출한 토큰 목록.
        """
        self._tokens = tokens
        self._index = 0

    def parse(self) -> _FilterExpression | None:
        """전체 검색식을 파싱한다.

        Returns:
            파싱된 표현식. 토큰이 없으면 ``None``.

        Raises:
            FilterSyntaxError: 검색식 문법이 올바르지 않은 경우.
        """
        if not self._tokens:
            return None
        expression = self._parse_or()
        if self._peek() is not None:
            raise FilterSyntaxError("필터 검색식 문법이 올바르지 않습니다.")
        return expression

    def _parse_or(self) -> _FilterExpression:
        """OR 우선순위의 표현식을 파싱한다.

        Returns:
            파싱된 표현식.
        """
        expression = self._parse_and()
        while self._match("|"):
            expression = _OrExpression(expression, self._parse_and())
        return expression

    def _parse_and(self) -> _FilterExpression:
        """명시적 또는 공백 기반 AND 표현식을 파싱한다.

        Returns:
            파싱된 표현식.
        """
        expression = self._parse_unary()
        while True:
            if self._match("&"):
                expression = _AndExpression(expression, self._parse_unary())
                continue
            token = self._peek()
            if token is not None and token.kind in {"TERM", "!", "("}:
                expression = _AndExpression(expression, self._parse_unary())
                continue
            return expression

    def _parse_unary(self) -> _FilterExpression:
        """NOT 표현식과 기본 표현식을 파싱한다.

        Returns:
            파싱된 표현식.
        """
        if self._match("!"):
            return _NotExpression(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> _FilterExpression:
        """검색어 또는 괄호 그룹을 파싱한다.

        Returns:
            파싱된 표현식.

        Raises:
            FilterSyntaxError: 검색어 또는 닫는 괄호가 필요한 경우.
        """
        token = self._peek()
        if token is None:
            raise FilterSyntaxError("검색어가 필요합니다.")
        if token.kind == "TERM":
            self._index += 1
            return _TermExpression(token.value)
        if self._match("("):
            expression = self._parse_or()
            if not self._match(")"):
                raise FilterSyntaxError("닫는 괄호가 필요합니다.")
            return expression
        raise FilterSyntaxError("검색어가 필요합니다.")

    def _peek(self) -> _Token | None:
        """현재 토큰을 조회한다.

        Returns:
            현재 토큰. 더 이상 토큰이 없으면 ``None``.
        """
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]

    def _match(self, kind: str) -> bool:
        """현재 토큰이 지정한 종류이면 소비한다.

        Args:
            kind: 기대하는 토큰 종류.

        Returns:
            토큰을 소비했으면 ``True``.
        """
        token = self._peek()
        if token is None or token.kind != kind:
            return False
        self._index += 1
        return True


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
