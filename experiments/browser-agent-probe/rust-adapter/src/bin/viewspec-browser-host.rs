//! Private streaming IPC for the shared bridge. No model-facing endpoint.
use serde_json::{Value, json};
use std::{collections::HashSet, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader, Stdin, Stdout},
    sync::Mutex,
    task::JoinSet,
};
use viewspec_browser::{Browser, Error, LaunchOptions, Result};

const MAX_LINE: usize = 64 * 1024 * 1024;
type Output = Arc<Mutex<Stdout>>;

async fn output(writer: &Output, message: Value) -> Result<()> {
    let mut bytes = serde_json::to_vec(&message).map_err(|_| Error::InvalidMessage)?;
    if bytes.len() > 128 * 1024 * 1024 {
        return Err(Error::InvalidMessage);
    }
    bytes.push(b'\n');
    let mut writer = writer.lock().await;
    writer.write_all(&bytes).await?;
    writer.flush().await?;
    Ok(())
}

// The buffer survives select! cancellation, so an event/task completion cannot
// discard a half-read request. take() bounds allocation before the newline arrives.
async fn line(reader: &mut BufReader<Stdin>, buffer: &mut Vec<u8>) -> Result<bool> {
    let remaining = MAX_LINE
        .checked_add(1)
        .and_then(|n| n.checked_sub(buffer.len()))
        .ok_or(Error::InvalidMessage)?;
    let count = (&mut *reader)
        .take(remaining as u64)
        .read_until(b'\n', buffer)
        .await?;
    if buffer.len() > MAX_LINE || (count == 0 && !buffer.is_empty()) {
        return Err(Error::InvalidMessage);
    }
    Ok(count != 0)
}

#[tokio::main]
async fn main() {
    if run().await.is_err() {
        eprintln!("Rust browser host stopped");
        std::process::exit(2);
    }
}

async fn run() -> Result<()> {
    let mut reader = BufReader::new(tokio::io::stdin());
    let writer = Arc::new(Mutex::new(tokio::io::stdout()));
    let mut buffer = Vec::new();
    if !line(&mut reader, &mut buffer).await? {
        return Ok(());
    }
    let launch: Value = serde_json::from_slice(&buffer).map_err(|_| Error::InvalidMessage)?;
    buffer.clear();
    let launch_id = launch["id"].as_u64().ok_or(Error::InvalidArgument)?;
    if launch["op"] != "launch" {
        return Err(Error::InvalidArgument);
    }
    let executable = launch["executable"]
        .as_str()
        .ok_or(Error::InvalidArgument)?;
    if !std::path::Path::new(executable).is_absolute() {
        return Err(Error::InvalidArgument);
    }
    let mut options = LaunchOptions::new(executable);
    let timeout = match launch.get("timeout_ms") {
        None => 35000,
        Some(value) => value
            .as_u64()
            .filter(|n| (1..=60000).contains(n))
            .ok_or(Error::InvalidArgument)?,
    };
    options.command_timeout = Duration::from_millis(timeout);
    options.proxy = match launch.get("proxy") {
        None | Some(Value::Null) => None,
        Some(value) => Some(value.as_str().ok_or(Error::InvalidArgument)?.to_owned()),
    };
    options.certificate_pins = match launch.get("certificate_pins") {
        None | Some(Value::Null) => None,
        Some(value) => Some(value.as_str().ok_or(Error::InvalidArgument)?.to_owned()),
    };
    let browser = match Browser::launch(options).await {
        Ok(browser) => browser,
        Err(error) => {
            output(
                &writer,
                json!({"id":launch_id,"ok":false,"error":error.to_string()}),
            )
            .await?;
            return Err(error);
        }
    };
    let connection = browser.host_connection();
    let mut events = connection.subscribe();
    output(&writer,json!({"id":launch_id,"ok":true,"result":{"processId":browser.process_id(),"eventSequence":connection.event_sequence()}})).await?;
    let event_writer = writer.clone();
    let mut pump = tokio::spawn(async move {
        loop {
            let event = events.recv().await.map_err(|_| Error::Closed)?;
            if event.message["method"] == "Viewspec.transportClosed" {
                output(&event_writer, json!({"fatal":"browser transport closed"})).await?;
                return Err::<(), Error>(Error::Closed);
            }
            output(
                &event_writer,
                json!({"event":event.message,"sequence":event.sequence}),
            )
            .await?;
        }
    });
    let mut tasks = JoinSet::new();
    let mut pending = HashSet::new();
    let result:Result<()> = async {
        loop {
            tokio::select! {
                biased;
                // Release capacity before publishing the response. A caller can
                // immediately replace a completed command without overflowing
                // a window whose completed tasks have not yet been polled.
                done=tasks.join_next(), if !tasks.is_empty()=>{
                    let (id,reply)=done.ok_or(Error::Closed)?.map_err(|_|Error::Closed)?;
                    pending.remove(&id);
                    output(&writer,reply).await?;
                }
                received=line(&mut reader,&mut buffer)=>{
                    if !received? {return Ok(());}
                    let request:Value=serde_json::from_slice(&buffer).map_err(|_|Error::InvalidMessage)?; buffer.clear();
                    let id=request["id"].as_u64().ok_or(Error::InvalidArgument)?;
                    if request["op"]=="close" {
                        pump.abort();
                        let _=tokio::time::timeout(Duration::from_secs(1),connection.command(None,"Browser.close",json!({}))).await;
                        output(&writer,json!({"id":id,"ok":true,"result":{}})).await?;
                        return Ok(());
                    }
                    if request["op"]!="send" || !pending.insert(id) || pending.len()>128 {return Err(Error::InvalidArgument);}
                    let method=request["method"].as_str().ok_or(Error::InvalidArgument)?.to_owned();
                    let session=request["sessionId"].as_str().map(str::to_owned);
                    let params=request.get("params").cloned().unwrap_or(json!({}));
                    let connection=connection.clone();
                    tasks.spawn(async move {
                        let reply=match connection.command(session.as_deref(),&method,params).await {
                            Ok(reply)=>json!({"id":id,"ok":true,"reply":reply,"afterEventSequence":reply["afterEventSequence"]}),
                            Err(error)=>json!({"id":id,"ok":false,"error":error.to_string(),"afterEventSequence":connection.event_sequence()}),
                        };
                        (id,reply)
                    });
                }
                _=&mut pump=>return Err(Error::Closed),
            }
        }
    }.await;
    tasks.abort_all();
    pump.abort();
    drop(browser);
    // A fatal condition may leave stdin open in the parent. Exit rather than wait
    // indefinitely for Tokio's stdin blocking read during runtime shutdown.
    if result.is_err() {
        let _ = tokio::time::timeout(
            Duration::from_secs(1),
            output(&writer, json!({"fatal":"Rust browser host failed"})),
        )
        .await;
    }
    result
}
