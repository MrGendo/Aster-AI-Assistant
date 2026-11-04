// PoC-2/7：核心服务生命周期管理 + LSUIElement（无 Dock 图标）
// 说明：PoC 用 std::process 直接拉起 Python 服务验证"拉起/崩溃重启/token 握手"语义；
// 正式打包时改用 tauri sidecar 声明（路径解析交给 Tauri），生命周期逻辑不变。
use std::io::Write;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

static TOKEN: OnceLock<String> = OnceLock::new();
static STOP: AtomicBool = AtomicBool::new(false);

struct CoreState {
    child: Option<Child>,
}

/// 生成一次性启动 token（时间戳+pid 混合哈希，PoC 级足够）
fn gen_token() -> String {
    let t = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let pid = std::process::id() as u128;
    format!("{:x}{:x}", t.wrapping_mul(0x9E3779B97F4A7C15), pid)
}

fn python_bin() -> std::path::PathBuf {
    // 编译期定位 poc/.venv/bin/python（CARGO_MANIFEST_DIR = poc/floating-ball/src-tauri）
    // 注意：不能 canonicalize——venv 的 python 是符号链接，解析成基础解释器会丢失 venv
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../.venv/bin/python")
}

fn service_entry() -> std::path::PathBuf {
    let base = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let p = base.join("sidecar/main.py");
    p.canonicalize().unwrap_or(p)
}

/// 拉起核心服务并通过 stdin 传入 token（一次性写入后关闭）
fn spawn_core(token: &str) -> std::io::Result<Child> {
    let mut child = Command::new(python_bin())
        .arg(service_entry())
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()?;
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(token.as_bytes());
        let _ = stdin.write_all(b"\n");
        let _ = stdin.flush();
    }
    Ok(child)
}

/// 监控线程：子进程退出（含被 kill -9）后 1 秒重启，直到应用退出
fn monitor(state: Arc<Mutex<CoreState>>, token: String) {
    loop {
        {
            let mut s = state.lock().unwrap();
            if STOP.load(Ordering::SeqCst) {
                break;
            }
            match spawn_core(&token) {
                Ok(c) => {
                    println!("[aster] 核心服务已拉起");
                    s.child = Some(c);
                }
                Err(e) => println!("[aster] 核心服务启动失败: {e}"),
            }
        }
        loop {
            thread::sleep(Duration::from_millis(300));
            let mut s = state.lock().unwrap();
            if STOP.load(Ordering::SeqCst) {
                if let Some(mut c) = s.child.take() {
                    let _ = c.kill();
                }
                return;
            }
            match s.child.as_mut().map(|c| c.try_wait()) {
                // 已退出 → 走重启路径
                Some(Ok(Some(status))) => {
                    println!("[aster] 核心服务退出: {status}，1 秒后重启");
                    s.child = None;
                    break;
                }
                // 出错 → 同样重启
                Some(Err(e)) => {
                    println!("[aster] try_wait 异常: {e}");
                    s.child = None;
                    break;
                }
                // 子进程被外部接管（kill 场景被 take 走）→ 重启
                None => break,
                // 仍在运行
                Some(Ok(None)) => {}
            }
        }
        thread::sleep(Duration::from_secs(1));
    }
}

#[tauri::command]
fn get_token() -> String {
    TOKEN.get().cloned().unwrap_or_default()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let token = gen_token();
    let _ = TOKEN.set(token.clone());

    let state = Arc::new(Mutex::new(CoreState { child: None }));
    let st = state.clone();
    thread::spawn(move || monitor(st, token));

    let mut app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .invoke_handler(tauri::generate_handler![get_token])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    // PoC-7：LSUIElement —— 无 Dock 图标的后台形态（仅 macOS）
    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Accessory);

    app.run(move |_app, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            STOP.store(true, Ordering::SeqCst);
            if let Ok(mut s) = state.lock() {
                if let Some(mut c) = s.child.take() {
                    let _ = c.kill();
                }
            }
        }
    });
}
