//! Telegram trial for Archive X (docs/plan.md, Phase 0).
//!
//! Signs in with a NEW session of its own (it never reads Archive X's session, so it cannot disturb it), then measures what the streaming and
//! transfer code needs from a Telegram library: listing channels and messages, downloading byte ranges (one and several at once), uploading a
//! large file, how a FloodWait comes back, and receiving updates. Every command prints one line of JSON and appends it to `phase0-report.json`.
//!
//!   telegram-trial login
//!   telegram-trial channels
//!   telegram-trial scan   <channel_id> [messages]
//!   telegram-trial range  <channel_id> <message_id> [offset_mb] [len_mb]
//!   telegram-trial parallel <channel_id> <message_id> [connections] [mb_each]
//!   telegram-trial upload [mb]
//!   telegram-trial updates [seconds]
//!
//! Needs TG_API_ID and TG_API_HASH in the environment (the same values Archive X uses; Settings > Telegram).

use std::io::{BufRead, Write};
use std::sync::Arc;
use std::time::{Duration, Instant};

use grammers_client::media::Media;
use grammers_client::message::InputMessage;
use grammers_client::{Client, SignInError};
use grammers_mtsender::SenderPool;
use grammers_session::storages::SqliteSession;
use grammers_session::types::PeerId;
use serde_json::{json, Value};

type Res<T> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

const MIB: i32 = 1024 * 1024;

fn prompt(text: &str) -> Res<String> {
    print!("{text}");
    std::io::stdout().flush()?;
    let mut line = String::new();
    std::io::stdin().lock().read_line(&mut line)?;
    Ok(line.trim().to_string())
}

fn report(command: &str, mut value: Value) {
    value["command"] = json!(command);
    value["at"] = json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0));
    println!("{value}");
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open("phase0-report.json") {
        let _ = writeln!(f, "{value}");
    }
}

async fn connect() -> Res<(Client, Arc<SqliteSession>)> {
    let api_id: i32 = std::env::var("TG_API_ID").map_err(|_| "set TG_API_ID")?.trim().parse().map_err(|_| "TG_API_ID must be a number")?;
    let session = Arc::new(SqliteSession::open("trial.session").await?);
    let SenderPool { runner, handle, .. } = SenderPool::new(Arc::clone(&session), api_id);
    let client = Client::new(handle);
    tokio::spawn(runner.run());
    Ok((client, session))
}

fn api_hash() -> Res<String> {
    Ok(std::env::var("TG_API_HASH").map_err(|_| "set TG_API_HASH")?.trim().to_string())
}

async fn login(client: &Client) -> Res<Value> {
    let t = Instant::now();
    if client.is_authorized().await? {
        return Ok(json!({ "already_signed_in": true }));
    }
    let phone = prompt("Phone number (international format): ")?;
    let token = client.request_login_code(&phone, &api_hash()?).await?;
    let code = prompt("Code Telegram sent you: ")?;
    match client.sign_in(&token, &code).await {
        Ok(_) => {}
        Err(SignInError::PasswordRequired(p)) => {
            let hint = p.hint().unwrap_or("").to_string();
            let password = prompt(&format!("Two-step password (hint: {hint}): "))?;
            client.check_password(p, password.trim()).await?;
        }
        Err(e) => return Err(format!("sign-in failed: {e}").into()),
    }
    Ok(json!({ "signed_in": true, "seconds": t.elapsed().as_secs_f64() }))
}

async fn find_peer(client: &Client, bot_api_id: i64) -> Res<grammers_session::types::PeerRef> {
    let wanted = PeerId::from_bot_api_dialog_id(bot_api_id).ok_or("not a valid channel id")?;
    let mut dialogs = client.iter_dialogs();
    while let Some(d) = dialogs.next().await? {
        if d.peer_id() == wanted {
            return Ok(d.peer_ref());
        }
    }
    Err("channel not found among this account's chats".into())
}

async fn channels(client: &Client) -> Res<Value> {
    let t = Instant::now();
    let mut dialogs = client.iter_dialogs();
    let mut out = Vec::new();
    while let Some(d) = dialogs.next().await? {
        out.push(json!({ "id": d.peer_id().bot_api_dialog_id(), "name": d.peer().name().unwrap_or("") }));
    }
    Ok(json!({ "dialogs": out.len(), "seconds": t.elapsed().as_secs_f64(), "first": out.iter().take(30).collect::<Vec<_>>() }))
}

async fn scan(client: &Client, channel: i64, limit: usize) -> Res<Value> {
    let peer = find_peer(client, channel).await?;
    let t = Instant::now();
    let mut it = client.iter_messages(peer).limit(limit);
    let (mut seen, mut videos, mut bytes) = (0usize, 0usize, 0u64);
    let mut sample = Vec::new();
    while let Some(m) = it.next().await? {
        seen += 1;
        if let Some(Media::Document(doc)) = m.media() {
            if doc.mime_type().map(|x| x.starts_with("video")).unwrap_or(false) {
                videos += 1;
                bytes += doc.size().unwrap_or(0) as u64;
                if sample.len() < 5 {
                    sample.push(json!({ "message_id": m.id(), "name": doc.name().unwrap_or(""), "size": doc.size() }));
                }
            }
        }
    }
    let secs = t.elapsed().as_secs_f64();
    Ok(json!({ "messages": seen, "videos": videos, "video_bytes": bytes, "seconds": secs, "messages_per_second": seen as f64 / secs.max(0.001), "sample": sample }))
}

async fn video_of(client: &Client, channel: i64, message_id: i32) -> Res<(grammers_client::media::Document, usize)> {
    let peer = find_peer(client, channel).await?;
    let msgs = client.get_messages_by_id(peer, &[message_id]).await?;
    let m = msgs.into_iter().flatten().next().ok_or("message not found")?;
    match m.media() {
        Some(Media::Document(d)) => {
            let size = d.size().ok_or("document without size")?;
            Ok((d, size))
        }
        _ => Err("that message has no document".into()),
    }
}

/// One byte range: time to the first chunk, then the whole range.
async fn read_range(client: &Client, doc: &grammers_client::media::Document, offset: usize, len: usize) -> Res<(f64, f64, usize)> {
    let skip = (offset / MIB as usize) as i32;
    let mut it = client.iter_download(doc).chunk_size(MIB).skip_chunks(skip);
    let t = Instant::now();
    let mut first = None;
    let mut got = 0usize;
    while got < len {
        match it.next().await? {
            Some(chunk) => {
                first.get_or_insert_with(|| t.elapsed().as_secs_f64());
                got += chunk.len();
            }
            None => break,
        }
    }
    Ok((first.unwrap_or(0.0), t.elapsed().as_secs_f64(), got))
}

async fn range(client: &Client, channel: i64, message_id: i32, offset_mb: usize, len_mb: usize) -> Res<Value> {
    let (doc, size) = video_of(client, channel, message_id).await?;
    let offset = offset_mb * MIB as usize;
    if offset >= size {
        return Err(format!("offset is past the end of the file ({} MiB)", size / MIB as usize).into());
    }
    let (first, total, got) = read_range(client, &doc, offset, len_mb * MIB as usize).await?;
    Ok(json!({ "file_mib": size / MIB as usize, "offset_mib": offset_mb, "requested_mib": len_mb, "received_mib": got as f64 / MIB as f64,
               "first_chunk_seconds": first, "total_seconds": total, "mib_per_second": got as f64 / MIB as f64 / total.max(0.001) }))
}

async fn parallel(client: &Client, channel: i64, message_id: i32, connections: usize, mb_each: usize) -> Res<Value> {
    let (doc, size) = video_of(client, channel, message_id).await?;
    let span = (size / MIB as usize).max(1);
    let t = Instant::now();
    let mut tasks = Vec::new();
    for i in 0..connections {
        let (c, d) = (client.clone(), doc.clone());
        let offset = (span * i / connections.max(1)) * MIB as usize;
        tasks.push(tokio::spawn(async move { read_range(&c, &d, offset, mb_each * MIB as usize).await.map_err(|e| e.to_string()) }));
    }
    let (mut ok, mut failed, mut bytes) = (0, Vec::new(), 0usize);
    for task in tasks {
        match task.await? {
            Ok((_, _, got)) => {
                ok += 1;
                bytes += got;
            }
            Err(e) => failed.push(e),
        }
    }
    let secs = t.elapsed().as_secs_f64();
    Ok(json!({ "connections": connections, "ok": ok, "failed": failed, "received_mib": bytes as f64 / MIB as f64, "seconds": secs, "mib_per_second": bytes as f64 / MIB as f64 / secs.max(0.001) }))
}

async fn upload(client: &Client, mb: usize) -> Res<Value> {
    let path = std::env::temp_dir().join(format!("phase0-upload-{mb}mb.bin"));
    {
        let mut f = std::fs::File::create(&path)?;
        let block: Vec<u8> = (0..MIB as usize).map(|i| (i * 31 % 251) as u8).collect();
        for _ in 0..mb {
            f.write_all(&block)?;
        }
    }
    let t = Instant::now();
    let uploaded = client.upload_file(&path).await?;
    let upload_secs = t.elapsed().as_secs_f64();
    let me = client.get_me().await?;
    let peer = me.to_ref().await?.ok_or("no reference to this account")?;
    let sent = client.send_message(peer, InputMessage::new().text("Archive X Phase 0 upload test").file(uploaded)).await?;
    let total = t.elapsed().as_secs_f64();
    client.delete_messages(peer, &[sent.id()]).await?;
    let _ = std::fs::remove_file(&path);
    Ok(json!({ "mib": mb, "upload_seconds": upload_secs, "send_seconds": total - upload_secs, "mib_per_second": mb as f64 / upload_secs.max(0.001),
               "deleted_afterwards": true }))
}

async fn updates_for(seconds: u64) -> Res<Value> {
    // Receiving updates needs the sender pool's update stream: count what arrives while you send yourself a message or post in a channel.
    let api_id: i32 = std::env::var("TG_API_ID")?.trim().parse()?;
    let session = Arc::new(SqliteSession::open("trial.session").await?);
    let SenderPool { runner, handle, updates } = SenderPool::new(Arc::clone(&session), api_id);
    let client = Client::new(handle.clone());
    tokio::spawn(runner.run());
    let mut stream = client.stream_updates(updates, Default::default()).await?;
    let deadline = Instant::now() + Duration::from_secs(seconds);
    let mut count = 0usize;
    while Instant::now() < deadline {
        match tokio::time::timeout(deadline.saturating_duration_since(Instant::now()), stream.next()).await {
            Ok(Ok(_)) => count += 1,
            Ok(Err(e)) => return Err(e.into()),
            Err(_) => break,
        }
    }
    Ok(json!({ "seconds": seconds, "updates_received": count }))
}

async fn run() -> Res<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = args.first().map(String::as_str).unwrap_or("help");
    let arg = |i: usize| args.get(i).map(String::as_str);
    let num = |i: usize, d: i64| -> i64 { arg(i).and_then(|v| v.parse().ok()).unwrap_or(d) };
    if command == "help" {
        println!("{}", include_str!("main.rs").lines().take_while(|l| l.starts_with("//!")).map(|l| l.trim_start_matches("//!").trim_start_matches(' ')).collect::<Vec<_>>().join("\n"));
        return Ok(());
    }
    if command == "updates" {
        report("updates", updates_for(num(1, 30) as u64).await?);
        return Ok(());
    }
    let (client, _session) = connect().await?;
    let value = match command {
        "login" => login(&client).await?,
        "channels" => channels(&client).await?,
        "scan" => scan(&client, num(1, 0), num(2, 500) as usize).await?,
        "range" => range(&client, num(1, 0), num(2, 0) as i32, num(3, 100) as usize, num(4, 8) as usize).await?,
        "parallel" => parallel(&client, num(1, 0), num(2, 0) as i32, num(3, 4) as usize, num(4, 8) as usize).await?,
        "upload" => upload(&client, num(1, 100) as usize).await?,
        other => return Err(format!("unknown command {other}; run: telegram-trial help").into()),
    };
    report(command, value);
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(e) = run().await {
        let text = e.to_string();
        // A flood wait is part of what is being tested: show it plainly.
        report("error", json!({ "error": text, "flood_wait": text.to_lowercase().contains("flood") }));
        std::process::exit(1);
    }
}
