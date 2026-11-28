//! Real Chromium tests. No Playwright/Node/Python runtime. The HTTP journal is the write oracle.
//! Run explicitly: VIEWSPEC_CHROMIUM=/path/to/chromium cargo test --test browser -- --ignored
use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    net::{Shutdown, TcpListener, TcpStream},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::Duration,
};
use viewspec_browser::{Browser, Error, LaunchOptions};

const SECRET: &str = "fixture-password-never-return-7462";
#[derive(Default)]
struct Journal {
    writes: Vec<Value>,
    cookies: Vec<String>,
    reads: Vec<String>,
}
struct Fixture {
    origin: String,
    journal: Arc<Mutex<Journal>>,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Fixture {
    fn start() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let journal = Arc::new(Mutex::new(Journal::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let (j, s) = (journal.clone(), stop.clone());
        let worker = thread::spawn(move || {
            let mut connections = Vec::new();
            while !s.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        // Accepted sockets can inherit nonblocking mode on Unix.
                        // Browser preconnects may also sit idle before sending headers;
                        // one must not monopolize the fixture or be mistaken for EOF.
                        stream.set_nonblocking(false).unwrap();
                        let control = stream.try_clone().unwrap();
                        let journal = j.clone();
                        let worker = thread::spawn(move || {
                            serve(&mut stream, &journal);
                            let _ = stream.shutdown(Shutdown::Both);
                        });
                        connections.push((control, worker));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2))
                    }
                    Err(error) => panic!("fixture accept: {error}"),
                }
            }
            for (stream, _) in &connections {
                let _ = stream.shutdown(Shutdown::Both);
            }
            for (_, worker) in connections {
                worker.join().unwrap();
            }
        });
        Self {
            origin,
            journal,
            stop,
            worker: Some(worker),
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.worker.take().unwrap().join().unwrap();
    }
}

fn serve(stream: &mut TcpStream, journal: &Mutex<Journal>) {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let mut bytes = Vec::new();
    let mut buffer = [0u8; 4096];
    let split = loop {
        let count = match stream.read(&mut buffer) {
            Ok(0) | Err(_) => return,
            Ok(count) => count,
        };
        bytes.extend_from_slice(&buffer[..count]);
        if let Some(index) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
            break index + 4;
        }
        assert!(bytes.len() < 65536);
    };
    let headers = String::from_utf8(bytes[..split].to_vec()).unwrap();
    let length = headers
        .lines()
        .filter_map(|line| line.split_once(':'))
        .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
        .map(|(_, v)| v.trim().parse::<usize>().unwrap())
        .unwrap_or(0);
    assert!(length < 1024 * 1024);
    while bytes.len() < split + length {
        let count = stream.read(&mut buffer).unwrap();
        if count == 0 {
            return;
        }
        bytes.extend_from_slice(&buffer[..count]);
    }
    let is_post = headers.starts_with("POST /save ");
    let body = if is_post {
        let value = serde_json::from_slice(&bytes[split..split + length]).unwrap();
        journal.lock().unwrap().writes.push(value);
        "{}".to_owned()
    } else {
        let mut journal = journal.lock().unwrap();
        journal
            .reads
            .push(headers.lines().next().unwrap().to_owned());
        if let Some((_, cookie)) = headers
            .lines()
            .filter_map(|s| s.split_once(':'))
            .find(|(name, _)| name.eq_ignore_ascii_case("cookie"))
        {
            journal.cookies.push(cookie.trim().to_owned());
        }
        let state=journal.writes.last().map(|v|v["fields"].clone()).unwrap_or_else(||json!({"name":"Before","email":"before@example.test","notes":"Original notes","tier":"Preserve me","password":SECRET}));
        HTML.replace("INITIAL_STATE", &state.to_string())
    };
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nSet-Cookie: fixture_session=kept; Path=/; SameSite=Lax\r\n\r\n{body}",
        if is_post {
            "application/json"
        } else {
            "text/html; charset=utf-8"
        },
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
}

const HTML: &str = r#"<!doctype html><title>Rust adapter fixture</title>
<article><h2 id=product>Product A</h2><form id=record>
<label>Name<input name=name></label><label>Email<input name=email type=email></label>
<label>Notes<textarea name=notes></textarea></label><label>Tier<input name=tier></label>
<label>Password<input name=password type=password></label><input name=check type=checkbox>
<button id=save type=button>Save</button><div id=status role=status></div></form></article>
<article><h2>Product B</h2></article><script>
const initial=INITIAL_STATE, events=[];
for(const [key,value] of Object.entries(initial))document.querySelector(`[name="${key}"]`).value=value;
for(const kind of ['input','change'])record.addEventListener(kind,e=>events.push([kind,e.target.name,e.isTrusted]));
save.onclick=async e=>{
 const fields=Object.fromEntries(Object.keys(initial).map(key=>[key,document.querySelector(`[name="${key}"]`).value]));
 await fetch('/save',{method:'POST',body:JSON.stringify({fields,events,click_trusted:e.isTrusted})});
 document.querySelector('#status').textContent='Saved';
};
</script>"#;

async fn launch() -> Browser {
    let binary=std::env::var_os("VIEWSPEC_CHROMIUM").expect("set VIEWSPEC_CHROMIUM to an absolute Chromium executable; browser tests never silently skip");
    Browser::launch(LaunchOptions::new(binary)).await.unwrap()
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn native_batch_separate_save_persistence_and_cookie_reuse() {
    let fixture = Fixture::start();
    let mut browser = launch().await;
    let mut page = browser.new_page().await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    let observation = page
        .observe(&["[name=name]", "[name=email]", "[name=notes]", "#save"])
        .await
        .unwrap();
    let controls = observation.controls();
    let receipt = page
        .prepare_text(&[
            (&controls[0], "Renée 東京"),
            (&controls[1], "after@example.test"),
            (&controls[2], ""),
        ])
        .await
        .unwrap();
    assert_eq!(receipt.fields_verified, 3);
    assert!(!receipt.submission_requested);
    assert!(fixture.journal.lock().unwrap().writes.is_empty());
    assert!(page.click(&controls[3]).await.unwrap().input_dispatched);
    page.evaluate("new Promise(resolve=>{const tick=()=>document.querySelector('#status').textContent==='Saved'?resolve(true):setTimeout(tick,10);tick()})").await.unwrap();
    assert_eq!(
        page.click(&controls[3]).await.unwrap_err(),
        Error::StaleObservation
    );
    {
        let journal = fixture.journal.lock().unwrap();
        assert_eq!(journal.writes.len(), 1);
        let write = &journal.writes[0];
        assert_eq!(
            write["fields"],
            json!({"name":"Renée 東京","email":"after@example.test","notes":"","tier":"Preserve me","password":SECRET})
        );
        assert_eq!(write["click_trusted"], true);
        let events = write["events"].as_array().unwrap();
        assert_eq!(events.len(), 6);
        for field in ["name", "email", "notes"] {
            for kind in ["input", "change"] {
                assert!(events.contains(&json!([kind, field, true])));
            }
        }
    }
    page.navigate(&fixture.origin).await.unwrap();
    assert_eq!(
        page.evaluate("document.querySelector('[name=name]').value")
            .await
            .unwrap(),
        "Renée 東京"
    );
    assert_eq!(fixture.journal.lock().unwrap().writes.len(), 1);
    assert!(
        fixture
            .journal
            .lock()
            .unwrap()
            .cookies
            .iter()
            .any(|s| s.contains("fixture_session=kept"))
    );
    let mut second = browser.new_page().await.unwrap();
    second.navigate(&fixture.origin).await.unwrap();
    assert_eq!(
        second.evaluate("document.cookie").await.unwrap(),
        "fixture_session=kept"
    );
    second.close().await.unwrap();
    browser.close().await.unwrap();
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn stale_routes_replacement_identity_and_latest_observation_are_refused() {
    let fixture = Fixture::start();
    let mut browser = launch().await;
    let mut page = browser.new_page().await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    for script in [
        "history.pushState({},'', '/product-b')",
        "history.pushState({},'', '/away');history.replaceState({},'', '/')",
    ] {
        page.navigate(&fixture.origin).await.unwrap();
        let old = page.observe(&["#save"]).await.unwrap();
        page.evaluate(script).await.unwrap();
        assert_eq!(
            page.click(&old.controls()[0]).await.unwrap_err(),
            Error::StaleObservation
        );
    }
    page.navigate(&fixture.origin).await.unwrap();
    let old = page.observe(&["#save"]).await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    assert_eq!(
        page.click(&old.controls()[0]).await.unwrap_err(),
        Error::StaleObservation
    );
    let old = page.observe(&["#save"]).await.unwrap();
    page.evaluate(
        "document.querySelector('#save').outerHTML='<button id=save type=button>Save</button>'",
    )
    .await
    .unwrap();
    assert_eq!(
        page.click(&old.controls()[0]).await.unwrap_err(),
        Error::TargetChanged
    );
    page.navigate(&fixture.origin).await.unwrap();
    let old = page.observe(&["#save"]).await.unwrap();
    page.evaluate("document.querySelector('#product').textContent='Product B'")
        .await
        .unwrap();
    assert_eq!(
        page.click(&old.controls()[0]).await.unwrap_err(),
        Error::TargetChanged
    );
    let old = page.observe(&["#save"]).await.unwrap();
    let _new = page.observe(&["#save"]).await.unwrap();
    assert_eq!(
        page.click(&old.controls()[0]).await.unwrap_err(),
        Error::StaleObservation
    );
    assert!(fixture.journal.lock().unwrap().writes.is_empty());
    browser.close().await.unwrap();
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn preflight_prevents_partial_batch_and_actionability_is_rechecked() {
    let fixture = Fixture::start();
    let mut browser = launch().await;
    let mut page = browser.new_page().await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    let obs = page
        .observe(&["[name=name]", "[name=email]", "#save"])
        .await
        .unwrap();
    let c = obs.controls();
    page.evaluate("document.querySelector('[name=email]').disabled=true")
        .await
        .unwrap();
    assert_eq!(
        page.prepare_text(&[(&c[0], "Changed"), (&c[1], "changed@example.test")])
            .await
            .unwrap_err(),
        Error::NotActionable
    );
    assert_eq!(
        page.evaluate("document.querySelector('[name=name]').value")
            .await
            .unwrap(),
        "Before"
    );
    assert_eq!(
        page.prepare_text(&[(&c[0], "One"), (&c[0], "Two")])
            .await
            .unwrap_err(),
        Error::InvalidArgument
    );
    page.evaluate("document.querySelector('[name=name]').readOnly=true")
        .await
        .unwrap();
    assert_eq!(
        page.prepare_text(&[(&c[0], "Changed")]).await.unwrap_err(),
        Error::NotActionable
    );
    page.evaluate("const cover=document.createElement('div');cover.style='position:fixed;inset:0;z-index:100';document.body.append(cover)").await.unwrap();
    assert_eq!(page.click(&c[2]).await.unwrap_err(), Error::NotActionable);
    assert!(fixture.journal.lock().unwrap().writes.is_empty());
    browser.close().await.unwrap();
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn focus_navigation_and_blur_rewrites_are_uncertain_never_retried() {
    let fixture = Fixture::start();
    let mut browser = launch().await;
    let mut page = browser.new_page().await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    let obs = page.observe(&["[name=name]"]).await.unwrap();
    page.evaluate(
        "document.querySelector('[name=name]').onfocus=()=>history.pushState({},'', '/product-b')",
    )
    .await
    .unwrap();
    assert_eq!(
        page.prepare_text(&[(&obs.controls()[0], "Wrong product")])
            .await
            .unwrap_err(),
        Error::ActionUncertain
    );
    assert_eq!(
        page.evaluate("document.querySelector('[name=name]').value")
            .await
            .unwrap(),
        "Before"
    );
    page.navigate(&fixture.origin).await.unwrap();
    let obs = page
        .observe(&["[name=name]", "[name=email]"])
        .await
        .unwrap();
    let c = obs.controls();
    page.evaluate("document.querySelector('[name=email]').onblur=()=>document.querySelector('[name=name]').value='Rewritten'").await.unwrap();
    assert_eq!(
        page.prepare_text(&[(&c[0], "Requested"), (&c[1], "after@example.test")])
            .await
            .unwrap_err(),
        Error::ActionUncertain
    );
    assert_eq!(
        page.evaluate("document.querySelector('[name=name]').value")
            .await
            .unwrap(),
        "Rewritten"
    );
    assert!(fixture.journal.lock().unwrap().writes.is_empty());
    browser.close().await.unwrap();
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn cross_page_handles_and_unsupported_controls_fail_closed() {
    let fixture = Fixture::start();
    let mut browser = launch().await;
    let mut a = browser.new_page().await.unwrap();
    let mut b = browser.new_page().await.unwrap();
    a.navigate(&fixture.origin).await.unwrap();
    b.navigate(&fixture.origin).await.unwrap();
    let obs = a.observe(&["#save"]).await.unwrap();
    assert_eq!(
        b.click(&obs.controls()[0]).await.unwrap_err(),
        Error::StaleObservation
    );
    for selector in ["[name=password]", "[name=check]"] {
        assert!(matches!(
            a.observe(&[selector]).await,
            Err(Error::Unsupported)
        ));
    }
    assert!(matches!(
        a.observe(&["input"]).await,
        Err(Error::NotActionable)
    ));
    assert!(matches!(
        a.observe(&["#save", "button#save"]).await,
        Err(Error::NotActionable)
    ));
    assert!(matches!(
        a.navigate("javascript:alert(1)").await,
        Err(Error::Unsupported)
    ));
    assert!(fixture.journal.lock().unwrap().writes.is_empty());
    browser.close().await.unwrap();
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn browser_exit_fails_pending_evaluation_and_drop_reaps_process() {
    let mut browser = launch().await;
    let pid = browser.process_id();
    let mut page = browser.new_page().await.unwrap();
    let killer = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(50)).await;
        // SAFETY: pid is the live child launched by this test, never a caller-provided PID.
        assert_eq!(unsafe { libc::kill(pid as i32, libc::SIGKILL) }, 0);
    });
    assert_eq!(
        page.evaluate("new Promise(()=>{})").await.unwrap_err(),
        Error::Closed
    );
    killer.await.unwrap();
    drop(browser);
    assert_eq!(page.evaluate("1").await.unwrap_err(), Error::Closed);
    // SAFETY: signal 0 only queries whether this just-reaped test child still exists.
    assert_eq!(unsafe { libc::kill(pid as i32, 0) }, -1);
}

#[tokio::test]
async fn missing_browser_executable_is_an_error() {
    assert!(matches!(
        Browser::launch(LaunchOptions::new("/no-such-viewspec-chromium")).await,
        Err(Error::Io)
    ));
}

#[test]
fn fixture_serves_requests_while_another_connection_delays_its_headers() {
    let fixture = Fixture::start();
    let address = fixture.origin.strip_prefix("http://").unwrap();
    let mut idle = TcpStream::connect(address).unwrap();
    // Deliberately send an incomplete header and complete it after another request.
    idle.write_all(b"GET / HTTP/1.1\r\n").unwrap();
    let mut active = TcpStream::connect(address).unwrap();
    active
        .set_read_timeout(Some(Duration::from_secs(1)))
        .unwrap();
    active
        .write_all(b"GET / HTTP/1.1\r\nHost: fixture\r\n\r\n")
        .unwrap();
    let mut response = String::new();
    active.read_to_string(&mut response).unwrap();
    assert!(response.starts_with("HTTP/1.1 200 OK"));
    idle.set_read_timeout(Some(Duration::from_secs(1))).unwrap();
    idle.write_all(b"Host: fixture\r\n\r\n").unwrap();
    response.clear();
    idle.read_to_string(&mut response).unwrap();
    assert!(response.starts_with("HTTP/1.1 200 OK"));
}

#[tokio::test]
async fn invalid_proxy_configuration_is_refused_before_launch() {
    for proxy in [
        "https://127.0.0.1:8443",
        "http://example.com:8080",
        "http://user@127.0.0.1:8080",
        "http://127.0.0.1:8080/path",
    ] {
        let mut options = LaunchOptions::new("/no-such-viewspec-chromium");
        options.proxy = Some(proxy.into());
        assert!(matches!(
            Browser::launch(options).await,
            Err(Error::InvalidArgument)
        ));
    }
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn configured_proxy_cannot_be_bypassed_by_loopback_destinations() {
    let proxy = Fixture::start();
    let destination = Fixture::start();
    let mut options =
        LaunchOptions::new(std::env::var_os("VIEWSPEC_CHROMIUM").expect("set VIEWSPEC_CHROMIUM"));
    options.proxy = Some(proxy.origin.clone());
    let mut browser = Browser::launch(options).await.unwrap();
    let mut page = browser.new_page().await.unwrap();
    page.navigate(&destination.origin).await.unwrap();
    assert!(
        destination.journal.lock().unwrap().reads.is_empty(),
        "loopback traffic bypassed proxy"
    );
    let expected = format!("GET {}/ HTTP/1.1", destination.origin);
    assert!(proxy.journal.lock().unwrap().reads.contains(&expected));
    browser.close().await.unwrap();
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn cancelled_input_requires_new_observation_and_cannot_be_replayed() {
    let fixture = Fixture::start();
    let mut browser = launch().await;
    let mut page = browser.new_page().await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    let obs = page.observe(&["[name=name]", "#save"]).await.unwrap();
    page.evaluate("document.querySelector('[name=name]').onfocus=()=>{fetch('/focus-started');alert('test pause')}").await.unwrap();
    assert!(
        tokio::time::timeout(
            Duration::from_millis(300),
            page.prepare_text(&[(&obs.controls()[0], "Changed")])
        )
        .await
        .is_err()
    );
    assert!(
        fixture
            .journal
            .lock()
            .unwrap()
            .reads
            .iter()
            .any(|r| r.starts_with("GET /focus-started "))
    );
    assert_eq!(
        page.prepare_text(&[(&obs.controls()[0], "Changed")])
            .await
            .unwrap_err(),
        Error::ActionUncertain
    );
    assert_eq!(
        page.click(&obs.controls()[1]).await.unwrap_err(),
        Error::ActionUncertain
    );
    assert!(fixture.journal.lock().unwrap().writes.is_empty());
    browser.close().await.unwrap();
}

#[tokio::test]
#[ignore = "requires VIEWSPEC_CHROMIUM; run explicitly in browser CI"]
async fn fresh_browser_has_no_previous_session_and_javascript_errors_are_sanitized() {
    let fixture = Fixture::start();
    let mut first = launch().await;
    let mut page = first.new_page().await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    page.evaluate("document.cookie='private_session=previous; Path=/'")
        .await
        .unwrap();
    first.close().await.unwrap();
    let mut second = launch().await;
    let mut page = second.new_page().await.unwrap();
    page.navigate(&fixture.origin).await.unwrap();
    assert!(
        !page
            .evaluate("document.cookie")
            .await
            .unwrap()
            .as_str()
            .unwrap()
            .contains("private_session")
    );
    let error = page
        .evaluate("throw new Error('private-value')")
        .await
        .unwrap_err();
    assert_eq!(error, Error::JavaScript);
    assert!(!error.to_string().contains("private-value"));
    second.close().await.unwrap();
}
