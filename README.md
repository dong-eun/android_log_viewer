# Android Log Viewer

Python 3.12와 PySide6로 만든 macOS/Windows용 Android logcat 데스크톱 뷰어입니다.

## 주요 기능

- 연결된 Android 기기 조회, 선택 및 새로고침
- 로그 시작 버튼을 누른 시점 이후의 logcat만 실시간 표시(기기 버퍼 유지)
- 텍스트 검색과 로그 레벨(Verbose/Debug/Info/Warn/Error) 필터
- 공백으로 구분한 여러 검색어의 AND 필터 (`aaa bbb` = `aaa` AND `bbb`)
- `package:` 입력 시 연결 기기에 설치된 패키지 자동완성 및 패키지별 PID 필터
- Android Studio Logcat과 유사한 레벨별 색상 구분
- 최하단에서는 새 로그 자동 추적, 사용자가 위로 스크롤하면 현재 위치 고정
- 화면 로그만 지우기(수신 로그, 저장 대상 및 기기 logcat 버퍼는 유지)
- 화면 내용과 무관하게 기기의 전체 logcat 버퍼를 `YYYYMMDDhhmm_기기명.txt`로 저장
- 선택한 기기의 `dumpsys` TXT 및 `bugreport` ZIP 저장
- 로그 최대 10,000줄 유지로 장시간 실행 시 메모리 사용 제한

## 사전 준비

1. Python 3.12를 설치합니다.
2. Android SDK Platform-Tools를 설치합니다.
3. 기기에서 **개발자 옵션 > USB 디버깅**을 켭니다.
4. 터미널에서 `adb devices` 실행 후 기기의 디버깅 허용 팝업을 승인합니다.

앱은 PATH의 `adb`를 먼저 사용하며, 찾지 못하면 `ANDROID_HOME`, `ANDROID_SDK_ROOT`, macOS 및 Windows의 기본 SDK 경로를 확인합니다.

## 개발 환경에서 실행

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
python main.py
```

### 함수 주석 생성

VS Code에서 `njpwerner.autodocstring` 확장을 설치하고 Google docstring 형식을 사용합니다. 개인별 VS Code 설정은 저장소에 포함하지 않습니다.

함수 정의 아래에서 `"""`를 입력한 후 Enter를 누르거나 다음 단축키를 사용합니다.

- macOS: `Cmd+Shift+2`
- Windows: `Ctrl+Shift+2`

생성된 `Args`, `Returns`, `Raises` 항목의 설명은 한국어로 작성합니다.

테스트:

```bash
python -m pip install -e '.[dev]'
pytest
```

## 배포 파일 만들기

PyInstaller 결과물은 빌드한 운영체제용으로만 생성됩니다. 따라서 macOS 앱은 macOS에서, Windows EXE는 Windows에서 각각 빌드해야 합니다.

```bash
python -m pip install -e '.[dev]'
pyinstaller --clean --noconfirm android_log_viewer.spec
```

- macOS: `dist/AndroidLogViewer.app`
- Windows: `dist/AndroidLogViewer/AndroidLogViewer.exe`

ADB는 앱에 포함하지 않습니다. 각 사용자의 Android SDK Platform-Tools를 사용하므로 최신 버전을 별도로 설치해야 합니다.

### GitHub Actions에서 Windows 배포 파일 만들기

GitHub 저장소의 **Actions > Build Windows executable > Run workflow**에서 수동으로 실행할 수 있습니다. `v`로 시작하는 태그(예: `v0.1.0`)를 푸시해도 자동으로 실행됩니다.

빌드가 완료되면 workflow 실행 화면의 **Artifacts**에서 `AndroidLogViewer-windows-x64`를 내려받습니다. 아티팩트 안의 `AndroidLogViewer-windows-x64.zip`을 압축 해제한 뒤 `AndroidLogViewer/AndroidLogViewer.exe`를 실행합니다. 같은 폴더의 DLL과 Qt 파일이 필요하므로 EXE만 따로 이동하지 마세요.

## 사용 방법

1. Android 기기를 USB로 연결하고 **새로고침**을 누릅니다.
2. 목록에서 상태가 `device`인 기기를 선택한 후 **로그 시작**을 누릅니다.
   - 시작 이전에 기기 버퍼에 쌓인 과거 로그는 불러오지 않습니다.
   - 앱 화면과 메모리만 새 세션으로 초기화하며 기기의 실제 logcat 버퍼는 삭제하지 않습니다.
3. Filter 입력 또는 Level 목록으로 화면에 표시할 로그를 좁힙니다.
   - `package:`를 입력하면 설치 패키지 목록이 나타납니다. 예: `package:com.example.app`
   - 패키지의 기본 프로세스와 `com.example.app:worker` 같은 보조 프로세스 로그도 함께 표시됩니다.
   - 검색어를 공백으로 나누면 모든 단어가 포함된 로그만 표시됩니다. 예: `Network timeout`은 `Network`와 `timeout`을 모두 포함한 로그를 찾습니다.
   - 공백을 포함한 문장 자체를 찾으려면 큰따옴표를 사용합니다. 예: `Network "request timeout"`
4. **화면 로그 지우기**는 현재 화면만 비웁니다. 이후 들어오는 로그는 계속 표시되고, 지우기 전 로그도 TXT 저장 대상에 포함됩니다. Android 기기의 logcat 버퍼를 삭제하는 `adb logcat -c`는 실행하지 않습니다.
   - 로그 최하단을 보고 있을 때는 새 로그를 자동으로 따라갑니다.
   - 사용자가 위로 스크롤하면 새 로그가 들어와도 현재 위치를 유지합니다.
   - 다시 최하단으로 이동하면 자동 로그 추적이 재개됩니다.
5. **기기 전체 로그 저장**은 화면의 필터, 화면 지우기 및 로그 시작 시점과 관계없이 저장 버튼을 누른 시점의 기기 전체 logcat 버퍼를 UTF-8 TXT로 저장합니다. 로그를 읽기만 하며 기기 버퍼는 삭제하지 않습니다.
6. **dumpsys 저장**, **bugreport 저장**은 백그라운드에서 수행되며 완료 시 알림을 표시합니다.
