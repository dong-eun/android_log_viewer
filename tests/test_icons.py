from pathlib import Path

from PIL import Image

from scripts.build_icons import build_icons


def test_build_icons_creates_macos_and_windows_formats(tmp_path) -> None:
    """투명 PNG 원본에서 유효한 macOS와 Windows 아이콘이 생성되는지 검증한다."""
    source_path = Path(__file__).resolve().parent.parent / "assets" / "app_icon.png"
    icns_path = tmp_path / "app_icon.icns"
    ico_path = tmp_path / "app_icon.ico"

    build_icons(source_path, icns_path, ico_path)

    with Image.open(icns_path) as icns:
        assert icns.format == "ICNS"
        assert icns.size == (1024, 1024)
    with Image.open(ico_path) as ico:
        assert ico.format == "ICO"
        assert ico.size == (256, 256)
