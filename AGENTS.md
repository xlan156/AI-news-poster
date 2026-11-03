# Development and verification workflow

These project instructions apply to future work in this repository, including new Codex sessions. Follow the user's current request first when it specifies a different verification level.

## Choose the smallest useful feedback loop

1. Before editing, identify whether the change affects React UI, Python business logic, Tauri/Windows behavior, or packaging. Check relevant existing tests and the current working tree.
2. For React layout, copy, colors, and ordinary controls, use the Vite/Tauri development window with hot reload. Check the changed page and its affected states there. Do not rebuild the installer for each visual iteration.
3. For Python ingestion, storage, configuration, LLM, or IPC changes, run focused Python tests with temporary data and direct service requests. Mock network and paid APIs. Use the full Python suite when the change crosses modules or before a release.
4. For frontend logic, run TypeScript checks and focused component/interaction tests when they provide meaningful coverage. Use a development window for behavior that needs visual confirmation.
5. For Tauri integration, tray, single-instance, native notifications, window lifecycle, or bundled resource paths, run the desktop program and verify the affected Windows behavior. A browser preview alone cannot verify these features.
6. Build and run the release executable or installer for a release candidate, packaging changes, sidecar changes that depend on bundled paths, installation/upgrade changes, or a bug that appears only in the packaged app. Verify the exact changed behavior in the built artifact.
7. Record what was actually checked and what remains unverified. Do not claim packaged behavior from development-mode results. Stop optional testing once the concrete risk is covered.

## Practical sequence

Use this order when applicable: focused checks → development-mode UI inspection → production frontend build → bundled executable/installer check. Skip stages that cannot reveal a relevant failure. Rebuild the Python sidecar only when Python code or its packaged dependencies changed. Rebuild the NSIS installer only when a deliverable or packaging check is needed.

The Windows build procedure and remaining platform-specific checks are documented in [docs/phase4-desktop.md](docs/phase4-desktop.md). Keep that document current when build or verification steps change.
