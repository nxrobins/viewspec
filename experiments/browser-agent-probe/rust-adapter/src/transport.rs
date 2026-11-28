//! CDP's NUL-delimited pipe protocol. One reader owns response routing and navigation epochs.
use crate::{Error, Result};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{
        Arc, Mutex, OnceLock, Weak,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{UnixStream, unix::OwnedWriteHalf},
    sync::{Mutex as AsyncMutex, broadcast, oneshot},
    task::AbortHandle,
};

const MAX_COMMAND: usize = 64 * 1024 * 1024;
// Chromium can duplicate a 25 MiB upload as escaped text (up to 6x) and
// base64 entries in one event. Drop redundant text after decoding, before IPC.
const MAX_MESSAGE: usize = 256 * 1024 * 1024;
type Pending = HashMap<u64, oneshot::Sender<Result<Value>>>;

/// Private host event. Page data is deliberately raw here; the Lift boundary owns projection.
#[derive(Clone, Debug)]
pub struct HostEvent {
    pub sequence: u64,
    pub message: Value,
}

#[derive(Default)]
struct PageState {
    epoch: u64,
    top_frame: Option<String>,
    detached: bool,
}

struct Inner {
    writer: AsyncMutex<OwnedWriteHalf>,
    pending: Mutex<Pending>,
    pages: Mutex<HashMap<String, PageState>>,
    next: AtomicU64,
    closed: AtomicBool,
    timeout: Duration,
    reader: OnceLock<AbortHandle>,
    event_sequence: AtomicU64,
    events: broadcast::Sender<HostEvent>,
}

impl Inner {
    fn fail(&self, error: Error) {
        if !self.closed.swap(true, Ordering::SeqCst) {
            let sequence = self.event_sequence.fetch_add(1, Ordering::SeqCst) + 1;
            let _ = self.events.send(HostEvent { sequence, message: json!({"method":"Viewspec.transportClosed","params":{"error":error.to_string()}}) });
        }
        for (_, sender) in self.pending.lock().unwrap().drain() {
            let _ = sender.send(Err(error.clone()));
        }
    }

    fn receive(&self, mut value: Value) -> Result<()> {
        if let Some(id) = value.get("id").and_then(Value::as_u64) {
            if value.get("result").is_none() && value.get("error").is_none() {
                return Err(Error::InvalidMessage);
            }
            value["afterEventSequence"] = json!(self.event_sequence.load(Ordering::SeqCst));
            if let Some(sender) = self.pending.lock().unwrap().remove(&id) {
                let _ = sender.send(Ok(value));
            }
            return Ok(()); // A timed-out command's late response must not become another command's result.
        }
        let method = value["method"].as_str().ok_or(Error::InvalidMessage)?;
        if matches!(method, "Network.requestWillBeSent" | "Fetch.requestPaused")
            && let Some(request) = value["params"]["request"].as_object_mut()
        {
            let complete_entries = request
                .get("postDataEntries")
                .and_then(Value::as_array)
                .is_some_and(|entries| {
                    !entries.is_empty() && entries.iter().all(|entry| entry["bytes"].is_string())
                });
            if complete_entries {
                request.remove("postData");
            }
        }
        let method = value["method"].as_str().ok_or(Error::InvalidMessage)?;
        let params = &value["params"];
        let session = if method == "Target.detachedFromTarget" {
            params["sessionId"].as_str()
        } else {
            value["sessionId"].as_str()
        };
        if let Some(session) = session {
            let mut pages = self.pages.lock().unwrap();
            let page = pages.entry(session.to_owned()).or_default();
            match method {
                "Page.frameNavigated" if params["frame"].get("parentId").is_none() => {
                    page.top_frame = params["frame"]["id"].as_str().map(str::to_owned);
                    page.epoch += 1;
                }
                "Page.navigatedWithinDocument"
                    if params["frameId"].as_str() == page.top_frame.as_deref() =>
                {
                    page.epoch += 1
                }
                "Runtime.executionContextsCleared" => page.epoch += 1,
                "Target.detachedFromTarget" | "Inspector.detached" | "Inspector.targetCrashed" => {
                    page.detached = true;
                    page.epoch += 1;
                }
                _ => (),
            }
        }
        let sequence = self.event_sequence.fetch_add(1, Ordering::SeqCst) + 1;
        // Dropping a subscriber is normal. A live host must treat lag as fatal, never
        // continue after losing navigation, interception or request-lifecycle evidence.
        let _ = self.events.send(HostEvent {
            sequence,
            message: value,
        });
        Ok(())
    }
}

impl Drop for Inner {
    fn drop(&mut self) {
        if let Some(reader) = self.reader.get() {
            reader.abort();
        }
    }
}

#[derive(Clone)]
pub(crate) struct Connection(Arc<Inner>);

// Removing the pending entry on cancellation also bounds memory when callers abandon commands.
struct PendingGuard {
    inner: Arc<Inner>,
    id: u64,
}
impl Drop for PendingGuard {
    fn drop(&mut self) {
        self.inner.pending.lock().unwrap().remove(&self.id);
    }
}

impl Connection {
    pub fn new(stream: UnixStream, timeout: Duration) -> Self {
        let (read, write) = stream.into_split();
        let inner = Arc::new(Inner {
            writer: AsyncMutex::new(write),
            pending: Mutex::new(HashMap::new()),
            pages: Mutex::new(HashMap::new()),
            next: AtomicU64::new(1),
            closed: AtomicBool::new(false),
            timeout,
            reader: OnceLock::new(),
            event_sequence: AtomicU64::new(0),
            events: broadcast::channel(8192).0,
        });
        let task = tokio::spawn(read_messages(read, Arc::downgrade(&inner)));
        inner.reader.set(task.abort_handle()).unwrap();
        Self(inner)
    }

    pub fn epoch(&self, session: &str) -> Result<u64> {
        if self.0.closed.load(Ordering::SeqCst) {
            return Err(Error::Closed);
        }
        let pages = self.0.pages.lock().unwrap();
        match pages.get(session) {
            Some(page) if page.detached => Err(Error::Closed),
            Some(page) => Ok(page.epoch),
            None => Ok(0),
        }
    }

    pub fn shutdown(&self) {
        self.0.fail(Error::Closed);
        if let Some(reader) = self.0.reader.get() {
            reader.abort();
        }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<HostEvent> {
        self.0.events.subscribe()
    }

    pub fn event_sequence(&self) -> u64 {
        self.0.event_sequence.load(Ordering::SeqCst)
    }

    pub async fn call(&self, session: Option<&str>, method: &str, params: Value) -> Result<Value> {
        let outcome = self.command(session, method, params).await?;
        if let Some(error) = outcome.get("error") {
            return Err(Error::Protocol {
                method: method.to_owned(),
                code: error["code"].as_i64().unwrap_or(-1),
            });
        }
        Ok(outcome["result"].clone())
    }

    pub async fn command(
        &self,
        session: Option<&str>,
        method: &str,
        params: Value,
    ) -> Result<Value> {
        if self.0.closed.load(Ordering::SeqCst) {
            return Err(Error::Closed);
        }
        let id = self.0.next.fetch_add(1, Ordering::SeqCst);
        let mut request = json!({"id":id,"method":method,"params":params});
        if let Some(session) = session {
            request["sessionId"] = json!(session);
        }
        let mut bytes = serde_json::to_vec(&request).map_err(|_| Error::InvalidMessage)?;
        if bytes.len() >= MAX_COMMAND {
            return Err(Error::InvalidMessage);
        }
        bytes.push(0);
        let (sender, receiver) = oneshot::channel();
        self.0.pending.lock().unwrap().insert(id, sender);
        let _pending = PendingGuard {
            inner: self.0.clone(),
            id,
        };
        let outcome = tokio::time::timeout(self.0.timeout, async {
            {
                let mut writer = self.0.writer.lock().await;
                if self.0.closed.load(Ordering::SeqCst) {
                    return Err(Error::Closed);
                }
                // A partial/cancelled write corrupts framing. Its guard closes the connection.
                let mut guard = WriteGuard {
                    inner: &self.0,
                    complete: false,
                };
                writer.write_all(&bytes).await.map_err(|_| Error::Closed)?;
                guard.complete = true;
            }
            receiver.await.map_err(|_| Error::Closed)?
        })
        .await
        .map_err(|_| Error::Timeout(method.to_owned()))??;
        Ok(outcome)
    }
}

struct WriteGuard<'a> {
    inner: &'a Inner,
    complete: bool,
}
impl Drop for WriteGuard<'_> {
    fn drop(&mut self) {
        if !self.complete {
            self.inner.fail(Error::Closed);
        }
    }
}

async fn read_messages(mut read: tokio::net::unix::OwnedReadHalf, weak: Weak<Inner>) {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    let result: Result<()> = async {
        loop {
            let count = read.read(&mut chunk).await.map_err(|_| Error::Closed)?;
            if count == 0 {
                return Err(Error::Closed);
            }
            for part in chunk[..count].split_inclusive(|byte| *byte == 0) {
                if buffer.len() + part.len() > MAX_MESSAGE {
                    return Err(Error::InvalidMessage);
                }
                buffer.extend_from_slice(part);
                if buffer.last() == Some(&0) {
                    buffer.pop();
                    let value =
                        serde_json::from_slice(&buffer).map_err(|_| Error::InvalidMessage)?;
                    let Some(inner) = weak.upgrade() else {
                        return Ok(());
                    };
                    inner.receive(value)?;
                    buffer.clear();
                    if buffer.capacity() > 4 * 1024 * 1024 {
                        buffer.shrink_to(1024 * 1024);
                    }
                }
            }
        }
    }
    .await;
    if let (Err(error), Some(inner)) = (result, weak.upgrade()) {
        inner.fail(error);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncBufReadExt, BufReader};

    async fn request(reader: &mut BufReader<tokio::net::unix::OwnedReadHalf>) -> Value {
        let mut bytes = Vec::new();
        reader.read_until(0, &mut bytes).await.unwrap();
        bytes.pop();
        serde_json::from_slice(&bytes).unwrap()
    }
    #[tokio::test]
    async fn routes_out_of_order_responses_and_records_route_events_before_reply() {
        let (client, server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_secs(1));
        let server = tokio::spawn(async move {
            let (read, mut write) = server.into_split();
            let mut read = BufReader::new(read);
            let a = request(&mut read).await;
            let b = request(&mut read).await;
            let frames = [
                json!({"method":"Page.frameNavigated","sessionId":"s","params":{"frame":{"id":"f"}}}),
                json!({"method":"Page.navigatedWithinDocument","sessionId":"s","params":{"frameId":"f"}}),
                json!({"id":b["id"],"result":{"name":b["method"]}}),
                json!({"id":a["id"],"result":{"name":a["method"]}}),
            ];
            for frame in frames {
                let mut data = serde_json::to_vec(&frame).unwrap();
                data.push(0);
                // Exercise frames split across reads as well as several messages in one read.
                for part in data.chunks(7) {
                    write.write_all(part).await.unwrap();
                }
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        });
        let (a, b) = tokio::join!(
            conn.call(Some("s"), "A", json!({})),
            conn.call(Some("s"), "B", json!({}))
        );
        assert_eq!(a.unwrap()["name"], "A");
        assert_eq!(b.unwrap()["name"], "B");
        assert_eq!(conn.epoch("s").unwrap(), 2);
        server.await.unwrap();
    }
    #[tokio::test]
    async fn timeout_and_cancellation_remove_pending_calls_without_retry() {
        let (client, server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_millis(30));
        assert!(matches!(
            conn.call(None, "Wait", json!({})).await,
            Err(Error::Timeout(_))
        ));
        assert!(conn.0.pending.lock().unwrap().is_empty());
        let copy = conn.clone();
        let task = tokio::spawn(async move { copy.call(None, "Cancelled", json!({})).await });
        tokio::task::yield_now().await;
        task.abort();
        let _ = task.await;
        assert!(conn.0.pending.lock().unwrap().is_empty());
        drop(server);
    }
    #[tokio::test]
    async fn eof_and_malformed_messages_fail_pending_commands() {
        for malformed in [false, true] {
            let (client, mut server) = UnixStream::pair().unwrap();
            let conn = Connection::new(client, Duration::from_secs(1));
            let task = tokio::spawn(async move {
                if malformed {
                    server.write_all(b"bad-json\0").await.unwrap();
                }
            });
            let error = conn.call(None, "Test", json!({})).await.unwrap_err();
            assert!(matches!(error, Error::Closed | Error::InvalidMessage));
            task.await.unwrap();
        }
    }

    #[tokio::test]
    async fn late_response_cannot_satisfy_a_new_command() {
        let (client, server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_millis(100));
        let server = tokio::spawn(async move {
            let (read, mut write) = server.into_split();
            let mut read = BufReader::new(read);
            let first = request(&mut read).await;
            let second = request(&mut read).await; // Only sent after the first times out.
            for (request, value) in [(first, "late"), (second, "current")] {
                let mut bytes =
                    serde_json::to_vec(&json!({"id":request["id"],"result":{"value":value}}))
                        .unwrap();
                bytes.push(0);
                write.write_all(&bytes).await.unwrap();
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        });
        assert!(matches!(
            conn.call(None, "First", json!({})).await,
            Err(Error::Timeout(_))
        ));
        assert_eq!(
            conn.call(None, "Second", json!({})).await.unwrap()["value"],
            "current"
        );
        assert!(conn.0.pending.lock().unwrap().is_empty());
        server.await.unwrap();
    }

    #[tokio::test]
    async fn partial_write_timeout_closes_connection_instead_of_reusing_broken_framing() {
        let (client, _unread_server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_millis(10));
        assert!(matches!(
            conn.call(None, "Large", json!({"text":"x".repeat(2 * 1024 * 1024)}))
                .await,
            Err(Error::Timeout(_))
        ));
        assert_eq!(
            conn.call(None, "Next", json!({})).await.unwrap_err(),
            Error::Closed
        );
        assert!(conn.0.pending.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn binary_request_entries_replace_only_complete_duplicate_text() {
        let (client, _server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_secs(2));
        let mut events = conn.subscribe();
        for (entries, removed) in [
            (json!([{"bytes":"AA=="}]), true),
            (json!([{"bytes":"AA=="}, {}]), false),
            (json!([]), false),
            (Value::Null, false),
        ] {
            conn.0
                .receive(json!({"method":"Fetch.requestPaused","params":{"request":{
                    "postData":"payload", "postDataEntries":entries.clone()
                }}}))
                .unwrap();
            let event = events.recv().await.unwrap();
            let request = &event.message["params"]["request"];
            assert_eq!(request.get("postData").is_none(), removed);
            assert_eq!(request["postDataEntries"], entries);
        }
    }

    #[tokio::test]
    async fn oversized_incoming_frame_is_bounded_and_closes_connection() {
        let (client, mut server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_secs(10));
        let task = tokio::spawn(async move {
            let _ = server.write_all(&vec![b'x'; MAX_MESSAGE + 1]).await;
            tokio::time::sleep(Duration::from_millis(10)).await;
        });
        assert_eq!(
            conn.call(None, "Test", json!({})).await.unwrap_err(),
            Error::InvalidMessage
        );
        assert_eq!(
            conn.call(None, "Next", json!({})).await.unwrap_err(),
            Error::Closed
        );
        task.await.unwrap();
    }

    #[tokio::test]
    async fn protocol_errors_do_not_echo_payloads_and_detached_sessions_stay_closed() {
        let (client, server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_secs(1));
        let task = tokio::spawn(async move {
            let (read, mut write) = server.into_split();
            let mut read = BufReader::new(read);
            let request = request(&mut read).await;
            for frame in [
                json!({"method":"Target.detachedFromTarget","params":{"sessionId":"s"}}),
                json!({"id":request["id"],"error":{"code":-32000,"message":"secret-page-value"}}),
            ] {
                let mut bytes = serde_json::to_vec(&frame).unwrap();
                bytes.push(0);
                write.write_all(&bytes).await.unwrap();
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        });
        let error = conn.call(Some("s"), "Test", json!({})).await.unwrap_err();
        assert_eq!(
            error,
            Error::Protocol {
                method: "Test".into(),
                code: -32000
            }
        );
        assert!(!error.to_string().contains("secret-page-value"));
        assert_eq!(conn.epoch("s").unwrap_err(), Error::Closed);
        task.await.unwrap();
    }

    #[tokio::test]
    async fn dropping_connection_releases_reader_and_pipe() {
        let (client, mut server) = UnixStream::pair().unwrap();
        let conn = Connection::new(client, Duration::from_secs(1));
        let weak = Arc::downgrade(&conn.0);
        drop(conn);
        assert!(weak.upgrade().is_none());
        let mut byte = [0];
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(1), server.read(&mut byte))
                .await
                .unwrap()
                .unwrap(),
            0
        );
    }
}
