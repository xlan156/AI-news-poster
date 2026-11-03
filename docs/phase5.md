# Phase 5 — News Feed Refinement, Background Scheduling & Windows Integration

## 1. Project Objective

Improve the AI News desktop application by refining its news presentation, source management, background update mechanism, and Windows integration.

The application should behave like a lightweight desktop news assistant: it can automatically start with Windows, periodically check configured AI research organizations for updates, store newly discovered articles in the local database, and immediately display Windows notifications when relevant new content is detected.

The overall design should prioritize:

- A clean, balanced, official-news-first home screen.
- Reliable background operation with minimal user intervention.
- Persistent local news history and searchable articles.
- Configurable update schedules and notification preferences.
- Accurate tracking of publication dates and source health.
- Compatibility with existing functionality and API configurations.

The application should remain functional without requiring a paid LLM API.

---

## 2. News Views and Article Management

### Functional Requirements

**FR-01: Default News View**

The application must open with **Official Updates** selected by default.

Provide three main views:

1. **Official Updates** — News published by currently enabled official organizations, such as OpenAI, Anthropic, DeepMind, DeepSeek, and other configured official sources.
2. **Research Papers** — Research publications collected from sources such as arXiv.
3. **All History** — All previously collected articles, including content from sources that have since been disabled.

The selected view must filter existing database records without triggering unnecessary network requests.

**FR-02: Historical Data Preservation**

Disabling a source must stop future scheduled retrieval from that source, but must not delete its historical articles.

All History must support keyword searching across article titles, summaries, and source names.

Previously stored articles must remain available after application updates or database migrations.

### Acceptance Criteria

- [ ] The initial home screen displays Official Updates instead of arXiv papers.
- [ ] Users can switch between all three views without re-fetching articles.
- [ ] Disabling a source removes its articles from the active Official Updates view.
- [ ] Historical articles remain searchable after the source is disabled.
- [ ] Existing database records are preserved.

---

## 3. Source Quotas and News Ranking

### Functional Requirements

**FR-03: Balanced Home Feed**

Introduce configurable per-source display quotas on the home screen.

For example, each source may contribute a maximum of three articles to the initial visible feed.

This prevents a single high-volume source from dominating the entire home screen.

The quota must only affect presentation. It must not prevent additional articles from being stored, searched, or viewed.

**FR-04: Article Ranking**

Provide two sorting modes:

- **Recommended** — A deterministic combination of article importance and publication recency.
- **Newest First** — Strict reverse-chronological ordering based on publication time, using a documented fallback when publication time is unavailable.

Importance ranking should use transparent, reproducible rules such as source priority, article category, release significance, or configured keywords.

Do not make LLM-based scoring mandatory. AI-assisted ranking may remain optional under the existing API configuration.

### Acceptance Criteria

- [ ] The home screen maintains a balanced distribution across available sources when sufficient content exists.
- [ ] Source quotas are configurable and do not affect database storage.
- [ ] Recommended sorting follows documented and reproducible ranking rules.
- [ ] Newest First correctly orders articles chronologically.
- [ ] All History can display every stored article without home-screen quotas.
- [ ] The app works correctly when one source has many articles and another has few or none.

---

## 4. Publication Metadata and Source Health

### Functional Requirements

**FR-05: Article Timestamps**

Distinguish between the following timestamps:

- `published_at` — Original publication date.
- `updated_at` — Most recent revision date, when available.
- `fetched_at` — Time when the application retrieved the article.

For arXiv articles, preserve the distinction between initial submission and later revisions.

Missing publication metadata must not be silently replaced by retrieval timestamps.

**FR-06: Persistent Source Status**

Each source must maintain the following operational metadata:

- Last attempted fetch time.
- Last successful fetch time.
- Current or most recent fetch status.
- Last failure reason, if applicable.
- Number of newly inserted articles from the latest attempt.

The UI must clearly distinguish:

- Successful fetch with new articles.
- Successful fetch with no new articles.
- Failed fetch.
- Currently fetching, where applicable.

One source failing must not block other sources from updating.

### Acceptance Criteria

- [ ] Publication, update, and retrieval timestamps are correctly stored and distinguished.
- [ ] arXiv revisions do not overwrite the original publication timestamp.
- [ ] Source status persists across application restarts.
- [ ] Successful empty updates are distinguishable from failures.
- [ ] Individual source errors are isolated and logged.
- [ ] Database migrations preserve existing articles.

---

## 5. Official Source Expansion and Reliability

### Functional Requirements

**FR-07: Additional Sources**

Add the following optional sources:

- Google Research: https://research.google/
- Hugging Face Blog: https://huggingface.co/blog

Existing official sources, including Anthropic News and DeepSeek News, must remain supported.

**FR-08: Official Content Filtering**

Hugging Face Blog includes community-authored content. Support filtering by verified official authors, organization ownership, or designated official categories.

Only content that satisfies the configured official-source criteria should appear in Official Updates.

Community articles may be excluded or assigned to another category when appropriate.

**FR-09: Source Parser Reliability**

Maintain consistent article schemas, duplicate detection, error handling, and source status reporting across all providers.

Add tests capable of detecting parser failures resulting from website structure changes.

An unexpected page structure or parsing failure must not be silently treated as a successful fetch with zero updates.

### Acceptance Criteria

- [ ] Google Research and Hugging Face can be enabled or disabled independently.
- [ ] Hugging Face community content does not automatically enter Official Updates.
- [ ] Existing Anthropic and DeepSeek collectors remain functional.
- [ ] New collectors follow the common storage and deduplication interface.
- [ ] Parsing and network failures are handled without crashing the application.

---

## 6. Windows Startup and Background Operation

### Functional Requirements

**FR-10: Launch at Windows Startup**

Add a persistent setting:

`Launch at Windows startup: On / Off`

When enabled, the application must automatically launch after the current Windows user signs in.

Automatic startup should preferably launch the application minimized to the system tray, without opening the main window or interrupting the user.

Users must be able to disable this behavior at any time.

The implementation should use a standard Windows per-user startup mechanism and should not require administrator privileges.

**FR-11: System Tray Integration**

Support continued background operation while the main window is closed or minimized.

The system tray should provide basic actions:

- Open the application.
- Check for updates now.
- Exit the application.

Closing the main window should minimize the application to the system tray when background operation is enabled. Selecting Exit must terminate the application and stop its background tasks.

Prevent duplicate application instances and overlapping schedulers when users launch the application again.

### Acceptance Criteria

- [ ] Launch at Windows startup can be enabled and disabled through Settings.
- [ ] The setting persists across restarts.
- [ ] When enabled, the app launches automatically after Windows sign-in.
- [ ] Automatic startup does not force the main window to appear.
- [ ] Background operation continues while the main window is hidden.
- [ ] The system tray supports Open, Check Now, and Exit.
- [ ] Exiting the application stops the background scheduler.
- [ ] Multiple launches do not create duplicate background instances.

---

## 7. Automatic News Fetching and Windows Notifications

### Functional Requirements

**FR-12: Configurable Background Fetch Interval**

Implement an automatic news-checking scheduler.

Provide a setting:

`Check for news every: 1 / 2 / 3 / 6 / 12 / 24 hours`

A custom positive interval in hours may also be supported.

The scheduler must:

1. Run while the application is active, including system-tray mode.
2. Fetch articles only from enabled sources.
3. Store newly discovered articles in the existing local database.
4. Perform duplicate detection before insertion.
5. Update source status and last-fetch timestamps.
6. Refresh the relevant UI views after successful data changes.
7. Schedule the next check without blocking the UI.

Users must also have a **Check Now** action to trigger an immediate update.

The scheduler should avoid overlapping fetch operations, survive temporary network failures, and handle Windows sleep/resume without generating redundant requests.

Automatic checks do not require the main window to remain open.

**FR-13: Event-Driven Windows Notifications**

When a scheduled or manually initiated fetch successfully discovers and stores new qualifying articles, the application should generate native Windows toast notifications.

The user experience should resemble receiving a new email in Outlook.

Expected flow:

`Scheduled Check → Retrieve Articles → Deduplicate → Save New Articles → Emit New-Article Event → Show Windows Notification`

Notifications should occur shortly after newly discovered articles are successfully committed to the database, rather than waiting for the next UI refresh.

Default behavior:

- Notify for newly discovered articles from enabled official sources.
- Allow Research Papers notifications to be enabled separately.
- Do not notify for previously stored articles.
- Do not notify when a successful fetch returns no new articles.
- Do not generate duplicate notifications after application restarts.
- Do not notify for old articles imported during initial database backfill.

Support grouped notifications to avoid overwhelming the user. For example:

- Single article: `New OpenAI update: [Article Title]`
- Multiple articles: `5 new AI updates from OpenAI, Anthropic, and DeepMind`

Clicking a notification should open the relevant article within the application or navigate to its original URL.

**FR-14: Notification Preferences**

Add configurable notification settings:

- Enable or disable Windows notifications.
- Enable or disable research-paper notifications.
- Select which sources can generate notifications.
- Optionally restrict notifications to high-importance articles.

Notification preferences must be independent of article collection. Disabling a notification must not prevent its article from being saved.

### Acceptance Criteria

- [ ] Users can select a polling interval of 1, 2, 3, 6, 12, or 24 hours.
- [ ] Automatic checks run at the configured interval while the app is running.
- [ ] Check Now triggers an immediate retrieval attempt.
- [ ] New articles are saved before notification events are emitted.
- [ ] Windows toast notifications appear when qualifying new articles are detected.
- [ ] Clicking a notification opens the corresponding content.
- [ ] Previously stored articles do not trigger duplicate notifications.
- [ ] Successful checks with no new articles do not generate notifications.
- [ ] Network failures do not terminate the scheduler.
- [ ] Research-paper notifications can be controlled separately.
- [ ] Notification settings persist after restarting the app.
- [ ] Notification failures do not roll back successfully stored articles.

---

## 8. Settings UI and Existing Feature Compatibility

### Functional Requirements

**FR-15: Centralized Settings**

Group related controls into clearly defined settings sections:

- **General** — Windows startup and background operation.
- **News Feed** — Default view, sorting mode, and source quotas.
- **Sources** — Enabled organizations, source categories, and source health.
- **Updates** — Automatic checking, polling interval, and Check Now.
- **Notifications** — Windows notifications, source filters, and paper notifications.
- **AI Configuration** — Existing Free API / Own API / Disabled modes.

Follow the current desktop application's visual design system and avoid introducing unnecessary dependencies or redesigning unrelated UI components.

**FR-16: Backward Compatibility**

Preserve existing functionality, particularly:

- Local article storage and deduplication.
- Existing notification filtering and relevance logic.
- Current API configuration modes.
- Existing supported sources.
- Historical article search.
- Previously saved user preferences.

Avoid unnecessary changes to the existing retrieval and database architecture. Extend current services where practical instead of introducing parallel implementations.

Chinese and English UI support may remain a secondary enhancement, provided the interface structure supports future localization.

### Acceptance Criteria

- [ ] All new settings are accessible through the existing GUI.
- [ ] Settings remain consistent after application restart.
- [ ] Existing API modes continue to function.
- [ ] Background fetching and notifications work with AI features disabled.
- [ ] Existing user data remains available after upgrading.
- [ ] No paid external API is required for basic news collection and notifications.

---

## 9. End-to-End Acceptance Tests

The phase is considered complete only when the following scenarios pass.

| Test | Scenario | Expected Result |
|---|---|---|
| E2E-01 | First launch with default settings | Official Updates opens; research papers are excluded |
| E2E-02 | Disable an official source | It disappears from the active feed; its historical articles remain searchable |
| E2E-03 | One source produces significantly more news than others | Home-screen quotas preserve source diversity |
| E2E-04 | Fetch new, duplicate, and previously stored articles | Only genuinely new records are inserted |
| E2E-05 | Simulate a source timeout or parsing error | Failure is recorded; other sources continue |
| E2E-06 | Enable Windows startup and sign in again | Application launches in the background |
| E2E-07 | Close the main window with background mode enabled | Application remains active in the system tray |
| E2E-08 | Configure a one-hour interval | Fetch attempts occur at the configured schedule |
| E2E-09 | New official article is successfully inserted | A Windows toast notification is generated |
| E2E-10 | Fetch completes without new content | No notification is generated |
| E2E-11 | Restart the application after receiving notifications | Old articles do not generate duplicate alerts |
| E2E-12 | Click a news notification | Corresponding article opens correctly |
| E2E-13 | Disable research-paper notifications | Papers are still collected, but no paper alerts appear |
| E2E-14 | Resume Windows after sleep or temporarily losing connectivity | Scheduler recovers without overlapping fetches |
| E2E-15 | Restart after configuring startup, update interval, and notifications | All preferences are preserved |
| E2E-16 | Upgrade from the previous database version | Historical records remain intact and searchable |

Where hour-long waits would be impractical, scheduler behavior should be verified using controllable clocks or shortened test intervals.

---

## 10. Definition of Done

This phase is complete when:

1. The application provides a balanced, official-news-first browsing experience with preserved, searchable history.
2. All configured sources expose accurate publication metadata and persistent health information.
3. Google Research and Hugging Face official content are supported without compromising existing collectors.
4. Users can enable Windows startup and keep the application running in the system tray.
5. The application automatically checks for new articles at configurable intervals without blocking the desktop UI.
6. Newly discovered qualifying articles trigger native Windows notifications after successful database insertion.
7. Duplicate articles, empty updates, initial backfills, and failed fetches do not generate misleading notifications.
8. Settings, notification preferences, and source states persist across application restarts.
9. Existing API modes, article storage, and historical data remain compatible.
10. All applicable end-to-end tests pass.

**Implementation priority:** First stabilize article metadata and persistence, then implement the feed improvements and source management, followed by the background scheduler, Windows startup integration, and event-driven notifications. Complete the phase with automated testing and regression verification.

**Scope constraint:** This phase focuses on improving the existing desktop application. Do not introduce cloud hosting, mandatory user accounts, a paid news API, or a separate backend service unless strictly necessary. Prefer a lightweight local-first implementation.