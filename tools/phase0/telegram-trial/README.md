# Telegram trial (Phase 0)

Question: can the Rust `grammers` library do what Archive X needs from Telegram (listing a channel, byte-range downloads for streaming, large uploads with FloodWait, updates)? If yes, streaming and Telegram work can move to Rust; if not, a small Python process keeps only Telegram (docs/plan.md, Phase 0).

This is a throw-away test program. It signs in with **its own new session** (`trial.session`, next to the program). It never reads or touches Archive X's session, so Archive X is not disturbed. In Telegram it shows up under *Settings → Devices*; end that session there when you are done (and delete `trial.session`).

## Get the program

GitHub → Actions → **Phase 0 Telegram trial** → Run workflow → open the finished run → download the artifact `telegram-trial-windows` and unzip it.

## Run it (PowerShell, in the unzipped folder)

```powershell
$env:TG_API_ID = "<your api id>"        # the values Archive X uses: Settings, Telegram
$env:TG_API_HASH = "<your api hash>"
.\telegram-trial.exe login               # asks for your phone number and the code Telegram sends
.\telegram-trial.exe channels            # lists chats with their ids; pick the channel id of your video channel
.\telegram-trial.exe scan <channel_id> 2000
.\telegram-trial.exe range <channel_id> <message_id> 0 8        # first 8 MiB of one video
.\telegram-trial.exe range <channel_id> <message_id> 3000 8     # 8 MiB starting 3000 MiB in (a seek)
.\telegram-trial.exe parallel <channel_id> <message_id> 8 8     # 8 connections at once
.\telegram-trial.exe upload 150          # sends a 150 MB test file to Saved Messages, then deletes it
.\telegram-trial.exe updates 30          # for 30 s: post something in a channel or message yourself
```

Send me `phase0-report.json` (it holds one line per command; it contains channel names and sizes, no passwords or session data).

## What the numbers decide

| Result | Meaning |
| --- | --- |
| `range` first chunk under about a second, throughput close to what Archive X's player gets | the streaming path can be written on `grammers` |
| `parallel` with 8 connections clearly faster than 1 | the multi-connection speed-up works |
| `upload` finishes and a FloodWait shows up as an error with `flood_wait: true` | the retry and wait handling can be built on it |
| `updates` count above zero | new-message events can drive the library without polling |

Known gap, found by reading the library: it does not refresh an expired file reference (`FILEREF_UPGRADE_NEEDED`, left as a TODO in its downloader). Archive X does that today; the Rust version would have to.
