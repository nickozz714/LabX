//! LabX desktop shell — Phase 1 per the plan: a thin Tauri wrapper around the
//! *existing*, unchanged Docker Compose deployment (backend/, frontend/,
//! docker-compose.yml). No FastAPI/React code changes; this crate only:
//!   1. generates the operator secrets `.env.example` otherwise asks a human
//!      to create by hand (LABX_ADMIN_PASSWORD/JWT_SECRET/FERNET_KEY/
//!      INTERNAL_TOKEN), once, into the OS app-data dir;
//!   2. runs `docker compose up -d` against those files;
//!   3. waits for the backend's unauthenticated health endpoint, then points
//!      the window at the same nginx-served frontend a manual deployment
//!      already uses (http://localhost:8080);
//!   4. keeps running in the system tray on window-close, since a lab may
//!      have a long job in flight — matching Docker Desktop's own UX.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::RngCore;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager};

const HEALTH_URL: &str = "http://localhost:8090/api/system/health";
const APP_URL: &str = "http://localhost:8080";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(180);

fn random_hex(len_bytes: usize) -> String {
    let mut buf = vec![0u8; len_bytes];
    rand::thread_rng().fill_bytes(&mut buf);
    buf.iter().map(|b| format!("{:02x}", b)).collect()
}

fn random_urlsafe(len_bytes: usize) -> String {
    let mut buf = vec![0u8; len_bytes];
    rand::thread_rng().fill_bytes(&mut buf);
    URL_SAFE_NO_PAD.encode(buf)
}

/// A Fernet key IS exactly 32 random bytes, standard (padded) base64 with the
/// URL-safe alphabet — see Python's `cryptography.fernet.Fernet.
/// generate_key()`. No need to shell out to Python for this.
fn fernet_key() -> String {
    let mut buf = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut buf);
    base64::engine::general_purpose::URL_SAFE.encode(buf)
}

/// Where the compose deployment's `.env` lives — checked BEFORE ever
/// generating one. `docker-compose.yml` uses fixed container names
/// (`labx-api`/`labx-web`, no per-project isolation), so an `.env` sitting
/// right next to it (the file a manual `docker compose up` in this same
/// checkout already reads, per the README's "Lokaal draaien" instructions)
/// belongs to the SAME physical deployment as this app would otherwise
/// manage from app-data. Generating a second, different set of secrets in
/// that case doesn't create an independent instance — Docker just recreates
/// the identical containers under the new secrets, silently breaking every
/// Fernet-encrypted value (oauth token, MCP-server auth, Azure profiles)
/// that was written under the old key. Only when no such file exists at all
/// (a real fresh install, e.g. a packaged app with no adjacent repo) does
/// this fall back to generating one in the OS app-data dir.
fn resolve_env_path(deployment_root: &Path, app_data_dir: &Path) -> PathBuf {
    let colocated = deployment_root.join(".env");
    if colocated.exists() {
        colocated
    } else {
        app_data_dir.join(".env")
    }
}

/// Writes the same variables an operator would otherwise hand-generate into
/// `.env` per `.env.example` — only if one doesn't already exist at the
/// resolved path (see `resolve_env_path`), so this never overwrites a
/// returning user's real secrets, whether that's a colocated manual `.env`
/// or one this app generated for itself previously.
fn ensure_env_file(env_path: &Path) -> std::io::Result<()> {
    if env_path.exists() {
        return Ok(());
    }
    if let Some(parent) = env_path.parent() {
        fs::create_dir_all(parent)?;
    }
    // Deliberately NO LABX_ADMIN_PASSWORD: leaving it empty makes the web
    // UI show the first-time account-setup screen, where the user picks
    // their own username + password (stored hashed in the app database) —
    // far better UX than fishing a generated password out of this file.
    let contents = format!(
        "LABX_ADMIN_USERNAME=admin\n\
         LABX_ADMIN_PASSWORD=\n\
         LABX_JWT_SECRET={jwt_secret}\n\
         LABX_FERNET_KEY={fernet_key}\n\
         LABX_INTERNAL_TOKEN={internal_token}\n\
         CLAUDE_CODE_OAUTH_TOKEN=\n\
         LABX_CLI_PATH=claude\n\
         LABX_CLI_DEFAULT_MODEL=claude-sonnet-5\n\
         LABX_DOCKER_HOST=unix:///var/run/docker.sock\n\
         DATA_GUARD_LLM_ENABLED=1\n\
         DATA_GUARD_LLM_MODEL=qwen2.5:1.5b\n\
         DATA_GUARD_LLM_URL=\n\
         LABX_CORS_ORIGINS=*\n\
         LABX_API_URL=http://localhost:8090\n",
        jwt_secret = random_hex(32),
        fernet_key = fernet_key(),
        internal_token = random_urlsafe(32),
    );
    let mut f = fs::File::create(env_path)?;
    f.write_all(contents.as_bytes())?;

    // Kept alongside .env (not inside it) for a future auto-login shell
    // command — deliberately NOT built in this pass, see the plan's
    // "Not in this plan" section. Lives next to the .env we just wrote,
    // wherever that resolved to.
    let local_config = serde_json::json!({ "admin_username": "admin" });
    if let Some(parent) = env_path.parent() {
        fs::write(
            parent.join("local-config.json"),
            serde_json::to_vec_pretty(&local_config).unwrap_or_default(),
        )?;
    }
    Ok(())
}

/// Debug builds (`tauri dev`) run straight from the checked-out repo — no
/// resource bundling involved, so the compose files sit two directories up
/// from this crate. Release builds resolve the same files from the
/// resources Tauri copied into the installed app (see tauri.conf.json).
fn deployment_root(app: &AppHandle) -> PathBuf {
    if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
    } else {
        app.path()
            .resource_dir()
            .expect("resource dir must resolve in a packaged app")
    }
}

fn docker_available() -> Result<(), String> {
    match Command::new("docker").arg("info").output() {
        Ok(o) if o.status.success() => Ok(()),
        Ok(o) => Err(String::from_utf8_lossy(&o.stderr).chars().take(400).collect()),
        Err(e) => Err(format!("docker-commando niet gevonden: {e}")),
    }
}

/// Startup-state shared with the splash page. The page POLLS this via the
/// `startup_state` command instead of relying on pushed JS evals: a push
/// races the page load (the docker check can fail before `window.showError`
/// even exists, silently losing the error — observed as an "eternal
/// spinner" on a Docker-less Windows VM). Polling can't lose anything.
#[derive(Default, Clone, serde::Serialize)]
struct StartupState {
    status: String,
    error_kind: Option<String>,
    error_detail: Option<String>,
}

static STARTUP_STATE: std::sync::Mutex<Option<StartupState>> = std::sync::Mutex::new(None);

fn splash_status(app: &AppHandle, text: &str) {
    if let Ok(mut s) = STARTUP_STATE.lock() {
        let st = s.get_or_insert_with(Default::default);
        st.status = text.to_string();
        st.error_kind = None;
        st.error_detail = None;
    }
    // Push too, for snappiness when the page IS already loaded.
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.eval(&format!("window.setStatus && window.setStatus({})",
                                  serde_json::to_string(text).unwrap_or_default()));
    }
}

fn splash_error(app: &AppHandle, kind: &str, detail: &str) {
    if let Ok(mut s) = STARTUP_STATE.lock() {
        let st = s.get_or_insert_with(Default::default);
        st.error_kind = Some(kind.to_string());
        st.error_detail = Some(detail.to_string());
    }
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.eval(&format!(
            "window.showError && window.showError({}, {})",
            serde_json::to_string(kind).unwrap_or_default(),
            serde_json::to_string(detail).unwrap_or_default()));
    }
}

#[tauri::command]
fn startup_state() -> StartupState {
    STARTUP_STATE.lock().ok().and_then(|s| s.clone()).unwrap_or_default()
}

fn compose_command(env_path: &Path, compose_path: &Path) -> Command {
    let mut cmd = Command::new("docker");
    cmd.arg("compose")
        .arg("--env-file")
        .arg(env_path)
        .arg("-f")
        .arg(compose_path);
    cmd
}

fn compose_up(env_path: &Path, compose_path: &Path, build: bool) -> Result<(), String> {
    let mut cmd = compose_command(env_path, compose_path);
    cmd.arg("up").arg("-d");
    if build {
        cmd.arg("--build");
    }
    match cmd.output() {
        Ok(o) if o.status.success() => Ok(()),
        Ok(o) => {
            let err = String::from_utf8_lossy(&o.stderr);
            Err(err.lines().rev().take(8).collect::<Vec<_>>().into_iter().rev()
                .collect::<Vec<_>>().join("\n"))
        }
        Err(e) => Err(format!("docker compose up mislukt: {e}")),
    }
}

fn compose_down(env_path: &Path, compose_path: &Path) {
    let _ = compose_command(env_path, compose_path).arg("down").status();
}

fn wait_for_health(max_wait: Duration) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed() < max_wait {
        if let Ok(resp) = ureq::get(HEALTH_URL).timeout(Duration::from_secs(2)).call() {
            if resp.status() == 200 {
                return true;
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

static STARTUP_RUNNING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// The full startup sequence, with every phase and every failure reported
/// INTO the splash page — an eternal spinner without explanation is not an
/// acceptable failure mode. Re-runnable via the `startup_retry` command.
fn spawn_startup(handle: AppHandle) {
    use std::sync::atomic::Ordering;
    if STARTUP_RUNNING.swap(true, Ordering::SeqCst) {
        return; // already running — the retry button can't stack attempts
    }
    std::thread::spawn(move || {
        let done = || STARTUP_RUNNING.store(false, Ordering::SeqCst);
        let app_data_dir = handle.path().app_data_dir().expect("app data dir must resolve");
        let root = deployment_root(&handle);
        let compose_path = root.join("docker-compose.yml");

        splash_status(&handle, "Docker wordt gecontroleerd…");
        if let Err(detail) = docker_available() {
            splash_error(&handle, "docker", &detail);
            done();
            return;
        }
        let env_path = resolve_env_path(&root, &app_data_dir);
        if let Err(e) = ensure_env_file(&env_path) {
            splash_error(&handle, "config", &format!(".env aanmaken mislukt: {e}"));
            done();
            return;
        }
        let built_marker = app_data_dir.join(".built");
        let need_build = !built_marker.exists();
        splash_status(&handle, if need_build {
            "Images worden gebouwd — dit duurt de eerste keer een paar minuten…"
        } else {
            "Containers worden gestart…"
        });
        match compose_up(&env_path, &compose_path, need_build) {
            Ok(()) => {
                if need_build {
                    let _ = fs::write(&built_marker, b"1");
                }
            }
            Err(detail) => {
                splash_error(&handle, "compose", &detail);
                done();
                return;
            }
        }
        splash_status(&handle, "Wachten tot LabX gezond is…");
        if wait_for_health(HEALTH_TIMEOUT) {
            if let Some(win) = handle.get_webview_window("main") {
                if let Ok(url) = APP_URL.parse() {
                    let _ = win.navigate(url);
                }
            }
        } else {
            splash_error(&handle, "health",
                         &format!("Geen reactie van {HEALTH_URL} binnen {}s.",
                                  HEALTH_TIMEOUT.as_secs()));
        }
        done();
    });
}

#[tauri::command]
fn startup_retry(app: AppHandle) {
    if let Ok(mut s) = STARTUP_STATE.lock() {
        *s = Some(StartupState { status: "Opnieuw proberen…".into(), ..Default::default() });
    }
    spawn_startup(app);
}

#[tauri::command]
fn open_docker_download() {
    let _ = tauri_plugin_opener::open_url(
        "https://www.docker.com/products/docker-desktop/", None::<String>);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![startup_retry, open_docker_download, startup_state])
        .setup(|app| {
            let handle = app.handle().clone();
            spawn_startup(handle);

            // System tray: closing the window hides it instead of quitting —
            // a lab may have a long job in flight, same convention as Docker
            // Desktop itself. The tray's own menu item is the real quit path
            // and also tears the containers down.
            let quit_item = MenuItem::with_id(app, "quit", "LabX volledig afsluiten", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quit_item])?;
            let tray_root = deployment_root(app.handle());
            let tray_app_data = app.path().app_data_dir().expect("app data dir");
            TrayIconBuilder::new()
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(move |app, event| {
                    if event.id().as_ref() == "quit" {
                        let env_path = resolve_env_path(&tray_root, &tray_app_data);
                        let compose_path = tray_root.join("docker-compose.yml");
                        compose_down(&env_path, &compose_path);
                        app.exit(0);
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
