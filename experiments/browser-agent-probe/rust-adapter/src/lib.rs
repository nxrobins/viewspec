//! Trusted-host browser primitives, not an agent-facing API or a security sandbox.
//! Chromium only; Unix launch; top-document native text fields and buttons.
#![cfg(unix)]

mod error;
mod transport;
pub use error::{Error, Result};
pub use transport::HostEvent;

use serde_json::{Value, json};
use std::{
    collections::HashSet,
    os::{
        fd::{AsRawFd, FromRawFd, OwnedFd},
        unix::{net::UnixStream, process::CommandExt},
    },
    path::PathBuf,
    process::{Child, Command, Stdio},
    time::Duration,
};
use tempfile::TempDir;
use transport::Connection;

const EDITOR: &str = include_str!("../../editor_inpage.js");
const LIFT: &str = include_str!("../../lift_inpage.js");
const DRIVER: &str = include_str!("driver.js");

pub struct LaunchOptions {
    pub executable: PathBuf,
    pub command_timeout: Duration,
    /// Trusted host's loopback policy proxy, fixed before the browser starts.
    pub proxy: Option<String>,
    /// Explicit test/operator SPKI pins; never a blanket certificate bypass.
    pub certificate_pins: Option<String>,
}

impl LaunchOptions {
    pub fn new(executable: impl Into<PathBuf>) -> Self {
        Self {
            executable: executable.into(),
            command_timeout: Duration::from_secs(5),
            proxy: None,
            certificate_pins: None,
        }
    }
}

/// Owns a fresh disposable profile and Chromium process. Drop kills and reaps the child.
/// No user profile, credentials, remote port, Playwright runtime, or SIGIL dependency.
pub struct Browser {
    child: Child,
    connection: Connection,
    profile: Option<TempDir>,
    context: String,
}

impl Browser {
    pub async fn launch(options: LaunchOptions) -> Result<Self> {
        if options.command_timeout.is_zero() {
            return Err(Error::InvalidArgument);
        }
        if let Some(pins) = &options.certificate_pins
            && (pins.len() > 1024
                || !pins.split(',').all(|pin| {
                    pin.len() == 44
                        && pin.ends_with('=')
                        && pin[..43]
                            .bytes()
                            .all(|c| c.is_ascii_alphanumeric() || c == b'+' || c == b'/')
                }))
        {
            return Err(Error::InvalidArgument);
        }
        if let Some(proxy) = &options.proxy {
            let address = url::Url::parse(proxy).map_err(|_| Error::InvalidArgument)?;
            if address.scheme() != "http"
                || address.host_str() != Some("127.0.0.1")
                || address.port().is_none()
                || !address.username().is_empty()
                || address.password().is_some()
                || address.path() != "/"
                || address.query().is_some()
                || address.fragment().is_some()
            {
                return Err(Error::InvalidArgument);
            }
        }
        let profile = tempfile::tempdir()?;
        let (host, child_socket) = UnixStream::pair()?;
        // Reserve a source above the destination FDs before fork. Otherwise dup2(…, 3)
        // could overwrite the source needed by dup2(…, 4). The original stays CLOEXEC.
        // SAFETY: child_socket is live; fcntl duplicates it into an owned FD >= 5.
        let fd = unsafe { libc::fcntl(child_socket.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 5) };
        if fd < 0 {
            return Err(Error::Io);
        }
        // SAFETY: fcntl returned a new descriptor owned exclusively by this scope.
        let source = unsafe { OwnedFd::from_raw_fd(fd) };
        let mut command = Command::new(options.executable);
        command
            .args([
                "--headless=new",
                "--remote-debugging-pipe",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-startup-window",
                "--disable-default-apps",
                "--disable-background-networking",
                "--disable-back-forward-cache",
                "--disable-field-trial-config",
                "--disable-component-update",
                "--disable-sync",
                // Chromium honours only the last --disable-features, so every feature is listed
                // here. A full Chrome build otherwise contacts Google in the background through
                // the policy gate: autofill crowdsourcing on pages with forms, and the omnibox
                // AI-mode eligibility check at startup. The Playwright baseline's headless shell
                // has neither service.
                "--disable-features=MediaRouter,OptimizationHints,PreconnectToSearch,\
                 AutofillServerCommunication,AimServerEligibilityEnabled,\
                 AimServerRequestOnStartupEnabled",
                "--disable-extensions",
                "--disable-popup-blocking",
                "--disable-quic",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--window-size=1280,900",
            ])
            .arg(format!("--user-data-dir={}", profile.path().display()))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if let Some(proxy) = options.proxy {
            command.arg(format!("--proxy-server={proxy}"));
            command.arg("--proxy-bypass-list=<-loopback>");
        }
        if let Some(pins) = options.certificate_pins {
            command.arg(format!("--ignore-certificate-errors-spki-list={pins}"));
        }
        // SAFETY: pre_exec only calls async-signal-safe dup2, then constructs an OS error
        // on failure. Both descriptors refer to one full-duplex private socket, with
        // Chromium reading FD 3 and writing FD 4 using its NUL-framed pipe protocol.
        unsafe {
            command.pre_exec(move || {
                if libc::dup2(fd, 3) < 0 || libc::dup2(fd, 4) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        host.set_nonblocking(true)?;
        let stream = tokio::net::UnixStream::from_std(host)?;
        let child = command.spawn()?;
        drop(command);
        drop(source);
        drop(child_socket);
        let connection = Connection::new(stream, options.command_timeout);
        let browser = Self {
            child,
            connection,
            profile: Some(profile),
            context: String::new(),
        };
        browser
            .connection
            .call(None, "Browser.getVersion", json!({}))
            .await?;
        Ok(browser)
    }

    pub fn process_id(&self) -> u32 {
        self.child.id()
    }

    /// Privileged plumbing for the shared bridge, never an agent/guest capability.
    pub fn host_connection(&self) -> HostConnection {
        HostConnection(self.connection.clone())
    }

    pub async fn new_page(&mut self) -> Result<Page> {
        if self.context.is_empty() {
            let context = self
                .connection
                .call(
                    None,
                    "Target.createBrowserContext",
                    json!({"disposeOnDetach":true}),
                )
                .await?;
            self.context = string(&context, "browserContextId")?;
        }
        let target = self
            .connection
            .call(
                None,
                "Target.createTarget",
                json!({"url":"about:blank","browserContextId":self.context}),
            )
            .await?;
        let target = string(&target, "targetId")?;
        let attached = self
            .connection
            .call(
                None,
                "Target.attachToTarget",
                json!({"targetId":target,"flatten":true}),
            )
            .await?;
        let session = string(&attached, "sessionId")?;
        self.connection
            .call(Some(&session), "Page.enable", json!({}))
            .await?;
        self.connection
            .call(Some(&session), "Runtime.enable", json!({}))
            .await?;
        Ok(Page {
            connection: self.connection.clone(),
            target,
            session,
            observation: 0,
            used: HashSet::new(),
            input_in_flight: false,
        })
    }

    /// Explicit graceful shutdown, followed by kill/wait if the browser has not exited.
    pub async fn close(mut self) -> Result<()> {
        // Browser.close can close the pipe before replying. Cleanup is authoritative.
        let _ = self.connection.call(None, "Browser.close", json!({})).await;
        self.stop();
        Ok(())
    }

    fn stop(&mut self) {
        self.connection.shutdown();
        if !matches!(self.child.try_wait(), Ok(Some(_))) {
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
        // Remove the profile only after Chromium exits, so it cannot recreate files.
        self.profile.take();
    }
}
impl Drop for Browser {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Raw trusted-host connection. Replies include the event sequence that must be
/// delivered before resolving them. This prevents an IPC writer scheduling race
/// from hiding a navigation behind its corresponding command response.
#[derive(Clone)]
pub struct HostConnection(Connection);
impl HostConnection {
    pub fn subscribe(&self) -> tokio::sync::broadcast::Receiver<HostEvent> {
        self.0.subscribe()
    }
    pub fn event_sequence(&self) -> u64 {
        self.0.event_sequence()
    }
    pub async fn command(
        &self,
        session: Option<&str>,
        method: &str,
        params: Value,
    ) -> Result<Value> {
        self.0.command(session, method, params).await
    }
}

/// The host serializes each page's actions through &mut self. Page handles may outlive
/// Browser, but their transport is closed and cannot attach to another process.
pub struct Page {
    connection: Connection,
    target: String,
    session: String,
    observation: u64,
    used: HashSet<usize>,
    // Left set when an action future is cancelled or fails after effects can begin.
    // Only a successful new observation permits another action.
    input_in_flight: bool,
}

#[derive(Clone)]
struct Stamp {
    session: String,
    observation: u64,
    epoch: u64,
    context: u64,
    document: Value,
    url: Value,
}

/// Opaque, observation-qualified control. No caller-provided selector is resolved at action time.
pub struct Control {
    stamp: Stamp,
    index: usize,
    target: Value,
}
pub struct Observation {
    controls: Vec<Control>,
}
impl Observation {
    pub fn controls(&self) -> &[Control] {
        &self.controls
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct PreparationReceipt {
    pub fields_verified: usize,
    /// The driver did not activate Save. A page's own input/blur handlers can still write.
    pub submission_requested: bool,
}

/// Confirms dispatch only. It does not claim persistence or that repeating Save is safe.
#[derive(Debug, PartialEq, Eq)]
pub struct ClickReceipt {
    pub input_dispatched: bool,
}

impl Page {
    async fn call(&self, method: &str, params: Value) -> Result<Value> {
        self.connection
            .call(Some(&self.session), method, params)
            .await
    }

    /// Trusted-host escape hatch, deliberately not exposed as an MCP/agent tool.
    /// It can read secrets and execute arbitrary page code. Lift's policy/projection
    /// layer must mediate any future agent-facing integration.
    pub async fn evaluate(&mut self, expression: &str) -> Result<Value> {
        self.evaluate_in(None, expression).await
    }

    async fn evaluate_in(&self, context: Option<u64>, expression: &str) -> Result<Value> {
        let mut params = json!({"expression":expression,"returnByValue":true,"awaitPromise":true});
        if let Some(context) = context {
            params["contextId"] = json!(context);
        }
        let reply = self.call("Runtime.evaluate", params).await?;
        if reply.get("exceptionDetails").is_some() {
            return Err(Error::JavaScript);
        }
        Ok(reply["result"].get("value").cloned().unwrap_or(Value::Null))
    }

    pub async fn navigate(&mut self, url: &str) -> Result<()> {
        let parsed = url::Url::parse(url).map_err(|_| Error::InvalidArgument)?;
        if !matches!(parsed.scheme(), "http" | "https")
            || !parsed.username().is_empty()
            || parsed.password().is_some()
        {
            return Err(Error::Unsupported);
        }
        self.observation += 1;
        self.used.clear();
        let reply = self.call("Page.navigate", json!({"url":url})).await?;
        if let Some(error) = reply.get("errorText") {
            // Chromium error constants are useful diagnostics; never echo an arbitrary URL/payload.
            let code = error.as_str().filter(|text| {
                text.starts_with("net::ERR_")
                    && text[9..]
                        .chars()
                        .all(|c| c.is_ascii_uppercase() || c == '_')
            });
            return Err(Error::Navigation(code.unwrap_or("unknown").to_owned()));
        }
        if reply["isDownload"] == true {
            return Err(Error::Unsupported);
        }
        let loader = reply.get("loaderId").and_then(Value::as_str);
        let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
        loop {
            let tree = self.call("Page.getFrameTree", json!({})).await?;
            // Don't mistake the old document's readyState for readiness of the new one.
            if loader.is_none_or(|loader| tree["frameTree"]["frame"]["loaderId"] == loader)
                && self.evaluate("document.readyState").await? == "complete"
            {
                return Ok(());
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(Error::Timeout("navigation readiness".into()));
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    /// Resolve trusted host selectors once, then capture the shared Lift identity.
    /// No iframe traversal, shadow selectors, successor lookup or custom widgets yet.
    pub async fn observe(&mut self, selectors: &[&str]) -> Result<Observation> {
        if selectors.is_empty() || selectors.len() > 64 || selectors.iter().any(|s| s.len() > 1024)
        {
            return Err(Error::InvalidArgument);
        }
        self.observation += 1;
        self.used.clear();
        let tree = self.call("Page.getFrameTree", json!({})).await?;
        let frame = string(&tree["frameTree"]["frame"], "id")?;
        let world = self
            .call(
                "Page.createIsolatedWorld",
                json!({"frameId":frame,"worldName":"viewspec-rust-driver"}),
            )
            .await?;
        let context = world["executionContextId"]
            .as_u64()
            .ok_or(Error::InvalidMessage)?;
        let epoch = self.connection.epoch(&self.session)?;
        if self.evaluate_in(Some(context),"Boolean(window.__viewspecLift && window.__viewspecEditor && window.__viewspecRustDriver)").await? != true {
            self.evaluate_in(Some(context),&format!("{EDITOR}\n{LIFT}\n{DRIVER}")).await?;
        }
        let capture = self
            .evaluate_in(
                Some(context),
                &format!("window.__viewspecRustDriver.observe({})", json!(selectors)),
            )
            .await?;
        status(&capture)?;
        if self.connection.epoch(&self.session)? != epoch {
            return Err(Error::StaleObservation);
        }
        let stamp = Stamp {
            session: self.session.clone(),
            observation: self.observation,
            epoch,
            context,
            document: capture["document"].clone(),
            url: capture["url"].clone(),
        };
        let targets = capture["targets"].as_array().ok_or(Error::InvalidMessage)?;
        self.input_in_flight = false;
        Ok(Observation {
            controls: targets
                .iter()
                .enumerate()
                .map(|(index, target)| Control {
                    stamp: stamp.clone(),
                    index,
                    target: target.clone(),
                })
                .collect(),
        })
    }

    fn check_stamp(&self, control: &Control) -> Result<()> {
        if control.stamp.session != self.session
            || control.stamp.observation != self.observation
            || self.connection.epoch(&self.session)? != control.stamp.epoch
            || self.used.contains(&control.index)
        {
            return Err(Error::StaleObservation);
        }
        Ok(())
    }

    async fn guard(
        &self,
        control: &Control,
        operation: &str,
        value: Option<&str>,
    ) -> Result<Value> {
        self.check_stamp(control)?;
        let arguments = json!({"document":control.stamp.document,"url":control.stamp.url,"target":control.target,"operation":operation,"value":value});
        let result = self
            .evaluate_in(
                Some(control.stamp.context),
                &format!("window.__viewspecRustDriver.step({arguments})"),
            )
            .await?;
        status(&result)?;
        self.check_stamp(control)?;
        Ok(result)
    }

    /// Preflight all fields, then native input + exact readback. Omitted fields are untouched.
    /// A failure after focus/input starts is uncertain; this function never retries.
    pub async fn prepare_text(&mut self, edits: &[(&Control, &str)]) -> Result<PreparationReceipt> {
        if self.input_in_flight {
            return Err(Error::ActionUncertain);
        }
        if edits.is_empty() || edits.len() > 64 || edits.iter().any(|(_, v)| v.len() > 65536) {
            return Err(Error::InvalidArgument);
        }
        let mut unique = HashSet::new();
        for (control, _) in edits {
            if !unique.insert(control.index) {
                return Err(Error::InvalidArgument);
            }
            self.guard(control, "check_text", None).await?;
        }
        self.input_in_flight = true;
        let result:Result<()> = async {
            for (control,value) in edits {
                self.guard(control,"focus_text",None).await?;
                self.guard(control,"check_focus",None).await?;
                if value.is_empty() {
                    self.call("Input.dispatchKeyEvent",json!({"type":"keyDown","key":"Backspace","code":"Backspace","windowsVirtualKeyCode":8})).await?;
                    self.call("Input.dispatchKeyEvent",json!({"type":"keyUp","key":"Backspace","code":"Backspace","windowsVirtualKeyCode":8})).await?;
                } else { self.call("Input.insertText",json!({"text":value})).await?; }
                self.guard(control,"blur_text",Some(value)).await?;
            }
            // A blur handler can change an earlier field: verify the whole batch at the end.
            for (control,value) in edits { self.guard(control,"verify_text",Some(value)).await?; }
            Ok(())
        }.await;
        result.map_err(|_| Error::ActionUncertain)?;
        self.input_in_flight = false;
        Ok(PreparationReceipt {
            fields_verified: edits.len(),
            submission_requested: false,
        })
    }

    pub async fn click(&mut self, control: &Control) -> Result<ClickReceipt> {
        if self.input_in_flight {
            return Err(Error::ActionUncertain);
        }
        self.guard(control, "check_button", None).await?;
        self.input_in_flight = true;
        self.guard(control, "scroll_button", None)
            .await
            .map_err(|_| Error::ActionUncertain)?;
        let point = self
            .guard(control, "point", None)
            .await
            .map_err(|_| Error::ActionUncertain)?;
        self.call(
            "Input.dispatchMouseEvent",
            json!({"type":"mouseMoved","x":point["x"],"y":point["y"]}),
        )
        .await
        .map_err(|_| Error::ActionUncertain)?;
        // Hover handlers may replace/move the target. Recheck its identity and geometry.
        let point = self
            .guard(control, "point", None)
            .await
            .map_err(|_| Error::ActionUncertain)?;
        self.used.insert(control.index); // A Save handle is consumed even if dispatch loses its reply.
        for kind in ["mousePressed", "mouseReleased"] {
            self.call("Input.dispatchMouseEvent",json!({"type":kind,"button":"left","buttons":if kind=="mousePressed"{1}else{0},"clickCount":1,"x":point["x"],"y":point["y"]})).await.map_err(|_|Error::ActionUncertain)?;
        }
        self.input_in_flight = false;
        Ok(ClickReceipt {
            input_dispatched: true,
        })
    }

    pub async fn close(self) -> Result<()> {
        self.connection
            .call(None, "Target.closeTarget", json!({"targetId":self.target}))
            .await?;
        Ok(())
    }
}

fn string(value: &Value, key: &str) -> Result<String> {
    value[key]
        .as_str()
        .map(str::to_owned)
        .ok_or(Error::InvalidMessage)
}
fn status(value: &Value) -> Result<()> {
    match value["status"].as_str() {
        Some("ok") => Ok(()),
        Some("stale") => Err(Error::StaleObservation),
        Some("changed") => Err(Error::TargetChanged),
        Some("unsupported") => Err(Error::Unsupported),
        Some("blocked") => Err(Error::NotActionable),
        _ => Err(Error::InvalidMessage),
    }
}
