from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main() -> int:
    """Qt 애플리케이션과 메인 창을 생성하고 이벤트 루프를 실행한다.

    Returns:
        int: Qt 이벤트 루프의 프로세스 종료 코드.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Android Log Viewer")
    app.setOrganizationName("Android Log Viewer")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
