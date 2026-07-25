"""투명 PNG 원본에서 macOS와 Windows용 앱 아이콘을 생성한다."""

from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = PROJECT_ROOT / "assets" / "app_icon.png"
ICNS_PATH = PROJECT_ROOT / "assets" / "app_icon.icns"
ICO_PATH = PROJECT_ROOT / "assets" / "app_icon.ico"


def build_icons(
    source_path: Path = SOURCE_PATH,
    icns_path: Path = ICNS_PATH,
    ico_path: Path = ICO_PATH,
) -> tuple[Path, Path]:
    """PNG 원본을 정사각형으로 정규화하고 ICNS와 ICO 파일을 생성한다.

    Args:
        source_path (Path, optional): 투명 배경 PNG 원본 경로.
        icns_path (Path, optional): macOS ICNS 출력 경로.
        ico_path (Path, optional): Windows ICO 출력 경로.

    Returns:
        tuple[Path, Path]: 생성된 ICNS와 ICO 파일 경로.

    Raises:
        ValueError: 원본 이미지가 정사각형이 아니거나 알파 채널이 없는 경우.
    """
    with Image.open(source_path) as source:
        image = source.convert("RGBA")
    if image.width != image.height:
        raise ValueError("앱 아이콘 원본은 정사각형이어야 합니다.")
    if image.getextrema()[3][0] != 0:
        raise ValueError("앱 아이콘 원본의 배경에는 투명 영역이 필요합니다.")

    icon = image.resize((1024, 1024), Image.Resampling.LANCZOS)
    icns_path.parent.mkdir(parents=True, exist_ok=True)
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    icon.save(icns_path, format="ICNS")
    icon.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return icns_path, ico_path


def main() -> None:
    """프로젝트 assets 디렉토리에 플랫폼별 아이콘을 생성한다."""
    icns_path, ico_path = build_icons()
    print(f"macOS 아이콘 생성: {icns_path}")
    print(f"Windows 아이콘 생성: {ico_path}")


if __name__ == "__main__":
    main()
