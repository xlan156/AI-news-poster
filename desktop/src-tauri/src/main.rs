#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
#[cfg(all(windows, not(debug_assertions)))]
use std::os::windows::process::CommandExt;
use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};
use std::sync::OnceLock;
use std::time::Instant;
use tauri::{Emitter, Manager};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;

struct Process {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    next_id: u64,
}

impl Process {
    fn call(&mut self, method: &str, params: serde_json::Value) -> Result<serde_json::Value, String> {
        self.next_id += 1;
        let request = serde_json::json!({"id":self.next_id,"method":method,"params":params});
        writeln!(self.stdin, "{request}").map_err(|e| format!("Backend unavailable: {e}"))?;
        self.stdin.flush().map_err(|e| e.to_string())?;
        let mut line = String::new();
        if self.stdout.read_line(&mut line).map_err(|e| e.to_string())? == 0 {
            return Err("News service stopped unexpectedly. Restart the application.".into());
        }
        let response: serde_json::Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        if response["id"] != self.next_id { return Err("Backend response mismatch".into()); }
        if let Some(message) = response["error"].as_str() { return Err(message.into()); }
        Ok(response["result"].clone())
    }
}

struct Backend {
    process: Arc<Mutex<Option<Process>>>,
    app: tauri::AppHandle,
    tray_on_close: AtomicBool,
    exiting: AtomicBool,
}

fn startup_trace(stage: &str) {
    if std::env::var_os("AI_NEWS_STARTUP_TRACE").is_some() {
        static START: OnceLock<Instant> = OnceLock::new();
        let start = START.get_or_init(Instant::now);
        eprintln!("startup +{}ms {stage}", start.elapsed().as_millis());
    }
}

fn spawn_backend(app: &tauri::AppHandle) -> Result<Process, String> {
    #[cfg(debug_assertions)]
    let mut cmd = {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let python = root.join(".venv/Scripts/python.exe");
        let mut command = Command::new(if python.exists() { python } else { "python".into() });
        command.current_dir(root).args(["-m", "ai_news_desktop.desktop_service"]);
        command
    };
    #[cfg(not(debug_assertions))]
    let mut cmd = {
        let executable = app.path().resource_dir().map_err(|e| e.to_string())?.join("service/ai-news-service.exe");
        Command::new(executable)
    };
    #[cfg(all(windows, not(debug_assertions)))]
    cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW; keep piped stdin/stdout for IPC.
    let mut child = cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|e| format!("Could not start news service: {e}"))?;
    let stdin = child.stdin.take().ok_or("Backend stdin unavailable")?;
    let stdout = child.stdout.take().ok_or("Backend stdout unavailable")?;
    Ok(Process { child, stdin, stdout: BufReader::new(stdout), next_id: 0 })
}

fn call_backend(app: &tauri::AppHandle, process: &Mutex<Option<Process>>, method: &str, params: serde_json::Value) -> Result<serde_json::Value, String> {
    let mut guard = process.lock().map_err(|e| e.to_string())?;
    if let Some(current) = guard.as_mut() {
        if matches!(current.child.try_wait(), Ok(Some(_))) { *guard = None; }
    }
    if guard.is_none() { *guard = Some(spawn_backend(app)?); }
    let result = guard.as_mut().expect("backend initialized").call(method, params);
    if result.is_err() {
        if let Some(current) = guard.as_mut() {
            if matches!(current.child.try_wait(), Ok(Some(_))) { *guard = None; }
        }
    }
    result
}

fn startup_preferences() -> (bool, bool) {
    let prefs = std::env::var_os("LOCALAPPDATA")
        .map(std::path::PathBuf::from)
        .and_then(|base| std::fs::read(base.join("AI News Desktop/preferences.json")).ok())
        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok());
    let tray_on_close = prefs.as_ref().and_then(|p| p["minimize_to_tray"].as_bool()).unwrap_or(true);
    let start_minimized = prefs.as_ref().and_then(|p| p["start_minimized"].as_bool()).unwrap_or(false);
    (tray_on_close, start_minimized)
}

#[tauri::command]
async fn backend_command(state: tauri::State<'_, Backend>, method: String, params: serde_json::Value) -> Result<serde_json::Value, String> {
    let process = state.process.clone();
    let app = state.app.clone();
    let trace_state = method == "get_state";
    if trace_state { startup_trace("get_state request"); }
    let result = tauri::async_runtime::spawn_blocking(move || call_backend(&app, &process, &method, params))
        .await.map_err(|e| e.to_string())??;
    if trace_state { startup_trace("get_state response"); }
    if let Some(preferences) = result.get("config").and_then(|c| c.get("preferences")) {
        if let Some(value) = preferences.get("minimize_to_tray").and_then(|v| v.as_bool()) {
            state.tray_on_close.store(value, Ordering::Relaxed);
        }
    }
    if let Some(value) = result.get("minimize_to_tray").and_then(|v| v.as_bool()) {
        state.tray_on_close.store(value, Ordering::Relaxed);
    }
    Ok(result)
}

fn tray_call(app: &tauri::AppHandle, method: &str, params: serde_json::Value) {
    let process = app.state::<Backend>().process.clone();
    let app = app.clone();
    let method = method.to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        let _ = call_backend(&app, &process, &method, params);
    });
}

fn main() {
    startup_trace("main");
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); }
        }))
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![backend_command])
        .setup(|app| {
            startup_trace("setup start");
            let (tray_on_close, start_minimized) = startup_preferences();
            startup_trace(&format!("preferences loaded: tray_on_close={tray_on_close} start_minimized={start_minimized}"));
            startup_trace("spawn backend start");
            let process = spawn_backend(app.handle()).ok();
            startup_trace("spawn backend complete");
            app.manage(Backend { process: Arc::new(Mutex::new(process)), app: app.handle().clone(), tray_on_close: AtomicBool::new(tray_on_close), exiting: AtomicBool::new(false) });
            let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
            let refresh = MenuItem::with_id(app, "refresh", "立即更新", true, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "暂停 / 恢复监控", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "设置", true, None::<&str>)?;
            let exit = MenuItem::with_id(app, "exit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &refresh, &pause, &settings, &exit])?;
            TrayIconBuilder::new().icon(app.default_window_icon().ok_or(std::io::Error::other("window icon unavailable"))?.clone()).menu(&menu).show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => { if let Some(w) = app.get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); } },
                    "refresh" => tray_call(app, "refresh", serde_json::json!({})),
                    "pause" => {
                        let process = app.state::<Backend>().process.clone();
                        let app = app.clone();
                        tauri::async_runtime::spawn_blocking(move || {
                            if let Ok(state) = call_backend(&app, &process, "get_state", serde_json::json!({})) {
                                let enabled = !state["status"]["monitoring"].as_bool().unwrap_or(true);
                                let _ = call_backend(&app, &process, "set_monitoring", serde_json::json!({"enabled": enabled}));
                            }
                        });
                    },
                    "settings" => { if let Some(w) = app.get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); let _ = app.emit("navigate", "settings"); } },
                    "exit" => { app.state::<Backend>().exiting.store(true, Ordering::Relaxed); app.exit(0); },
                    _ => {}
                }).build(app)?;
            if let Some(window) = app.get_webview_window("main") {
                startup_trace(&format!("window before preference: {:?}", window.is_visible()));
                if start_minimized { window.hide()?; } else { window.show()?; }
                startup_trace(&format!("window after preference: {:?}", window.is_visible()));
            }
            startup_trace("setup complete");
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let state = window.app_handle().state::<Backend>();
                if !state.exiting.load(Ordering::Relaxed) && state.tray_on_close.load(Ordering::Relaxed) {
                    api.prevent_close(); let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Could not create desktop application")
        .run(|app, event| {
            if let tauri::RunEvent::Ready = event {
                if let Some(window) = app.get_webview_window("main") {
                    startup_trace(&format!("window at ready: {:?}", window.is_visible()));
                }
            }
            if let tauri::RunEvent::Exit = event {
                let state = app.state::<Backend>();
                if let Ok(mut process) = state.process.lock() {
                    if let Some(child) = process.as_mut() { let _ = child.child.kill(); let _ = child.child.wait(); }
                };
            }
        });
}
