# Phase 4 desktop architecture and verification

## Choice of stack

The desktop shell uses Tauri 2, React, TypeScript, and CSS tokens. Python remains the only owner of feed ingestion, scheduling, SQLite writes, LLM analysis, and native Windows notifications. Tauri is the best fit for the MXU-like desktop layout: it supports a precise web-based design system and a native Windows tray with lower bundled runtime overhead than Electron. PySide6 would simplify Python integration but require a different widget styling approach for this design; Avalonia would add a .NET process and a cross-language bridge. Tauri also adds a Rust and WebView2 build dependency, but the Windows installer bundles the Python service and frontend so end users do not need Python, Node.js, or Rust.

## Design system

The UI uses a 216 px navigation sidebar, compact 39 px navigation rows, grouped content panels, 7–11 px corner radii, 1 px subtle borders, and restrained blue accents. Dark and light palettes share semantic CSS tokens for background, surface, text, muted text, borders, hover, and status colors. Windows scaling is handled through CSS pixels and a minimum window size. The news list favors short rows with source, category, date, title, and summary. These patterns were informed by [MaaEnd](https://github.com/MaaEnd/MaaEnd) and its [MXU frontend](https://github.com/MaaXYZ/MXU), which uses Tauri, React, and TypeScript with theme switching and task/status surfaces. No MaaFramework code or branding is included.

## Process and data flow

1. Tauri starts one bundled PyInstaller service at launch. In development it starts the project's Python virtual environment.
2. Tauri sends newline-delimited JSON requests through the child's stdin and reads structured responses from stdout. No TCP port is opened. Python logs use stderr.
3. A single Python worker handles both UI requests and scheduled refreshes. It owns the SQLite connection and serializes writes. The stdin reader only queues requests.
4. The Python service sends Windows toasts and records delivery through the existing pipeline. Tauri never sends news toasts. Clicking a toast still launches the article's original HTTPS URL.
5. User data and settings live under `%LOCALAPPDATA%\AI News Desktop`, outside the installation folder. Existing `news.sqlite3` remains in place; the read-state table is added without replacing existing tables.
6. Tauri's single-instance plugin prevents duplicate GUI instances. Closing the window hides it to the tray by default. Explicit Exit kills and waits for the Python child.

The shell currently waits for a long-running refresh response before it can request a new state snapshot. Navigation remains responsive because Tauri runs IPC on a blocking worker thread, but actions are serialized until refresh completes.

## Build on Windows 11

Prerequisites for maintainers: Python 3.11, Node.js 22, pnpm 9, stable Rust, and Visual Studio C++ Build Tools. The [GitHub Actions workflow](../.github/workflows/windows-desktop.yml) installs these and produces an NSIS installer artifact without publishing a release.

```powershell
python -m pip install -e '.[secrets]' pyinstaller pytest
python -m pytest -q
python tools/generate_icon.py
python -m PyInstaller --noconfirm --clean --onefile --name ai-news-service --collect-data ai_news_desktop --hidden-import keyring.backends.Windows tools/service_entry.py
New-Item -ItemType Directory -Force desktop/src-tauri/service | Out-Null
Copy-Item dist/ai-news-service.exe desktop/src-tauri/service/ai-news-service.exe
Set-Location desktop
pnpm install --frozen-lockfile
pnpm build
pnpm tauri build
```

The installer is at `desktop/src-tauri/target/release/bundle/nsis/AI News Desktop_0.4.0_x64-setup.exe`. It was built locally on Windows 11 after installing Rust and Microsoft C++ Build Tools. The project uses Tauri's `downloadBootstrapper` WebView2 mode. Windows 11 normally includes WebView2; if it is absent, the installer needs internet access to download it. See [Tauri's Windows installer guide](https://v2.tauri.app/distribute/windows-installer/). An unsigned installer may show a Windows SmartScreen warning.

## Windows verification

The packaged application was launched in a native Windows 11 window and visually checked on all six pages. The overview and news pages loaded the existing 2,859-article database. The news page showed the full database count; a title around the 1,001st article was found through the UI search. Legacy arXiv rows now display their source and the research category. The article drawer, source controls, AI settings, notification settings, and dark/light theme switch were also inspected in the running application. The original dark theme and close-to-tray preferences were restored after the check. Python tests passed (45 tests and 15 subtests), and TypeScript and Vite production builds passed.

Further Windows verification required:

- Install and launch on a clean Windows 11 account with no Python or Node.js.
- Verify the packaged Python service starts, exits with the GUI, and leaves no orphan process.
- Verify close-to-tray, tray Show/Refresh/Pause/Settings/Exit, and single-instance behavior.
- Verify a native toast appears while hidden, opens the correct original article, and is not sent twice.
- Verify 100%, 125%, and 150% display scaling and dark/light/system themes.
- Measure cold startup time, idle memory, and idle CPU on the packaged executable.

## Known gaps

The installer and bundled Python service were built locally, and the installer was extracted to verify that it contains both executables. The Python service exe passed an IPC startup/shutdown smoke test. Start-at-login, daily digest, log viewer, language switching, and persisted per-source error history are not implemented. Start-at-login and the log viewer are labelled as pending in the UI. The overview's last successful refresh also resets on application restart. Custom RSS sources can be added; custom webpage adapters require editing configuration. The Python native toast implementation needs validation from the packaged executable before a public release.
