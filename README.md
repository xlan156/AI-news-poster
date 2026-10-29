# AI News Desktop Agent — Phase 2

A lightweight Windows 11 AI news agent. It fetches RSS/Atom feeds, stores articles in SQLite, suppresses first-run notifications, and sends at most three native Windows toasts per refresh. The Phase 1 mock notification demo remains available. There is no LLM, GUI, or always-on installer.

## Requirements and installation (Windows 11)

Use Python 3.11 or newer in a normal interactive desktop session. In PowerShell, from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

If PowerShell blocks activation, run `.\.venv\Scripts\python.exe` instead of `python` in the commands below. The Windows-only `windows-toasts` dependency installs automatically on Windows.

This checkout also supports a project-local Python installation when the `py` launcher is unavailable and `uv` is installed:

```powershell
uv python install 3.11 --install-dir .python --cache-dir .uv-cache
uv venv .venv --python .python\cpython-3.11.13-windows-x86_64-none\python.exe --cache-dir .uv-cache
uv pip install --python .venv\Scripts\python.exe -e . --cache-dir .uv-cache
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ai_news_desktop --demo
```

The versioned path above reflects the Python 3.11.13 installation used for this checkout; adapt it if `uv` installs a newer 3.11 patch release.

## Commands

```powershell
python -m ai_news_desktop --fetch-once
python -m ai_news_desktop --list-latest
python -m ai_news_desktop --notify-new
python -m ai_news_desktop --run
python -m ai_news_desktop --demo
python -m unittest discover -s tests -v
```

If your shell's `python` is not the virtual environment, substitute `.\.venv\Scripts\python.exe`. `--fetch-once` only fetches and stores. `--notify-new` fetches and delivers pending new articles. `--run` repeats that cycle every 60 minutes; press Ctrl+C to stop. `--list-latest` shows the 20 most recently published stored articles. `--demo` still sends three fixed historical examples from `src/ai_news_desktop/data/mock_news.json`.

The first successful fetch of **each feed** establishes its baseline without notifications. Newly discovered articles from later fetches become pending, subject to the age limit and per-cycle cap. A failed send remains pending for a later retry. The database is at `%LOCALAPPDATA%\AI News Desktop\news.sqlite3` by default; use `--db .runtime\news.sqlite3` to keep a separate test database in this checkout. Repeated fetches and restarts reuse the same database.

## Configuration

Copy `src/ai_news_desktop/data/default_config.json` to a writable JSON file and pass `--config path\to\config.json`. Each feed has `name`, `url`, and `enabled`. The other settings are `refresh_minutes` (default 60), `max_notifications_per_cycle` (3), `max_article_age_hours` (48), and `notifications_enabled` (`true`). Use `--db` to select a database path.

The four defaults were checked as live RSS/Atom endpoints: [Hugging Face Blog](https://huggingface.co/blog/feed.xml), [Google Research](https://research.google/blog/rss/), [arXiv cs.AI RSS](https://rss.arxiv.org/rss/cs.AI), and [arXiv cs.LG Atom](https://rss.arxiv.org/atom/cs.LG). arXiv may return a valid but empty feed between announcements; that is not treated as a failure. [arXiv documents its feed URL pattern](https://info.arxiv.org/help/rss.html).

## Architecture

- `models.py` validates a news item and its HTTP(S) URL.
- `data.py` loads the bundled JSON fixtures.
- `notifications.py` exposes a small `NotificationService` protocol and a `WindowsToastNotifier` implementation. A future backend can replace this class without changing the loader or CLI.
- `config.py` reads the bundled defaults or a user JSON file.
- `ingestion.py` fetches feeds with a timeout, parses RSS/Atom, skips malformed entries, and normalizes URLs and UTC timestamps.
- `storage.py` uses SQLite to deduplicate by canonical URL and retain first-seen and notification state.
- `pipeline.py` handles one refresh cycle and the interruptible scheduler.
- `__main__.py` provides the CLI and reports feed and delivery failures.

The toast's `launch_action` is its validated URL, so Windows handles a click through the registered HTTP(S) protocol even after this short-lived demo process exits. An activation callback logs clicks while the process remains alive; it does not open the browser again. The library's default interactable toaster uses the registered Command Prompt AppUserModelID (AUMID), so Windows may label these demo notifications as Command Prompt. A dedicated application identity and installer are future work.

`windows-toasts` returns when a toast is submitted to Windows, before the user sees or clicks it. The agent records `notified` after that call succeeds; it cannot guarantee visual delivery or detect every later asynchronous Windows failure. The original article URL is preserved in the toast launch action.

## Troubleshooting

- **No toast appears:** Run in a signed-in Windows desktop session. Check Settings → System → Notifications, including app notifications and Do not disturb. Confirm `windows-toasts` installed with `python -m pip show windows-toasts`.
- **An error mentions the application identity or activation:** Use the default identity in this demo. A custom AUMID requires registration before it can reliably deliver activation from Notification Center.
- **“The process has no package identity” from this repository's restricted runner:** Run the same `.venv` command in a normal signed-in PowerShell desktop session. The notification call succeeded there during development.
- **Click does not open a page:** Check that your default browser handles HTTPS links, then rerun the demo and click a fresh toast. See command output for delivery errors. The callback log is only expected if the Python process is still active; URL protocol activation should work after exit.
- **`py -3.11` is unavailable:** Install Python 3.11 or newer from python.org, then use the matching `py -3.x` command.
- **Feeds fail:** Check internet access and the logged per-feed error. Other feeds continue. All-feed failure makes a one-shot command exit with an error; the scheduler retries next cycle.
- **No news notification:** The first fetch of each feed is intentionally silent. Later articles must be within the configured age limit, and Windows notifications must be enabled.

The Python notification library documents [protocol launch actions](https://windows-toasts.readthedocs.io/en/latest/user/toast.html), [activation callbacks and the default AUMID](https://windows-toasts.readthedocs.io/en/latest/interactable.html), and [custom AUMID registration](https://windows-toasts.readthedocs.io/en/latest/custom_aumid.html). Microsoft also documents the [desktop app identity requirement](https://learn.microsoft.com/en-us/windows/win32/shell/quickstart-sending-desktop-toast).

## Beyond Phase 2

Future work includes a dedicated Windows application identity, a background startup mechanism, article ranking, and optional LLM summaries.
