#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};
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
    process: Arc<Mutex<Process>>,
    tray_on_close: AtomicBool,
    exiting: AtomicBool,
}

fn spawn_backend(app: &tauri::App) -> Result<Process, String> {
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
    let mut child = cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|e| format!("Could not start news service: {e}"))?;
    let stdin = child.stdin.take().ok_or("Backend stdin unavailable")?;
    let stdout = child.stdout.take().ok_or("Backend stdout unavailable")?;
    Ok(Process { child, stdin, stdout: BufReader::new(stdout), next_id: 0 })
}

#[tauri::command]
async fn backend_command(state: tauri::State<'_, Backend>, method: String, params: serde_json::Value) -> Result<serde_json::Value, String> {
    let process = state.process.clone();
    let result = tauri::async_runtime::spawn_blocking(move || process.lock().map_err(|e| e.to_string())?.call(&method, params))
        .await.map_err(|e| e.to_string())??;
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
    let method = method.to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        if let Ok(mut guard) = process.lock() { let _ = guard.call(&method, params); }
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); }
        }))
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![backend_command])
        .setup(|app| {
            let process = spawn_backend(app).map_err(std::io::Error::other)?;
            app.manage(Backend { process: Arc::new(Mutex::new(process)), tray_on_close: AtomicBool::new(true), exiting: AtomicBool::new(false) });
            if let Ok(mut service) = app.state::<Backend>().process.lock() {
                if let Ok(snapshot) = service.call("get_state", serde_json::json!({})) {
                    let prefs = &snapshot["config"]["preferences"];
                    if let Some(value) = prefs["minimize_to_tray"].as_bool() {
                        app.state::<Backend>().tray_on_close.store(value, Ordering::Relaxed);
                    }
                    if prefs["start_minimized"].as_bool() == Some(true) {
                        if let Some(window) = app.get_webview_window("main") { let _ = window.hide(); }
                    }
                }
            }
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
                        tauri::async_runtime::spawn_blocking(move || {
                            if let Ok(mut guard) = process.lock() {
                                if let Ok(state) = guard.call("get_state", serde_json::json!({})) {
                                    let enabled = !state["status"]["monitoring"].as_bool().unwrap_or(true);
                                    let _ = guard.call("set_monitoring", serde_json::json!({"enabled": enabled}));
                                }
                            }
                        });
                    },
                    "settings" => { if let Some(w) = app.get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); let _ = app.emit("navigate", "settings"); } },
                    "exit" => { app.state::<Backend>().exiting.store(true, Ordering::Relaxed); app.exit(0); },
                    _ => {}
                }).build(app)?;
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
            if let tauri::RunEvent::Exit = event {
                let state = app.state::<Backend>();
                if let Ok(mut process) = state.process.lock() { let _ = process.child.kill(); let _ = process.child.wait(); };
            }
        });
}
