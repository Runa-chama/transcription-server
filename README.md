# Transcription Server

A lightweight web front end for faster-whisper, designed to run on a Windows PC
and be published through SSH reverse tunnels and nginx.

## Features

- Upload audio or video in parallel chunks (8 MB chunks, 4 connections).
- Automatic model placement; no processor selector in the UI.
- Outputs SRT, VTT, TXT, and JSON.
- One job at a time with a small queue, plus cancel and retry support.
- Uploaded files and generated results are deleted after 12 hours.
- SAP Fundamental Styles / SAP Horizon UI.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>.

Models are downloaded on first use into the Hugging Face cache and unloaded after
10 minutes of inactivity.

## Background services (Windows)

```powershell
.\scripts\install_tasks.ps1
```

Registers two scheduled tasks at logon:

- `TranscriptionServer`: runs `scripts\run_server.ps1` (uvicorn, auto restart).
- `TranscriptionTunnel`: runs `scripts\run_tunnel.ps1`
  (`ssh -R 127.0.0.1:36419:127.0.0.1:8000 ken`, auto reconnect).

Logs are written to `data\logs`.

## Deployment topology

```text
browser -> https://transcription.runachama.com
        -> nginx on x (TLS, large uploads, no buffering)
        -> x 127.0.0.1:36419
        -> ken 127.0.0.1:36419
        -> PC 127.0.0.1:8000
```

- **PC**: reverse SSH to `ken`, binding `ken` loopback port 36419.
- **ken**: `deploy/ken/transcription-tunnel.sh` keeps a reverse SSH connection to
  `x`, binding `x` loopback port 36419. Started from the user crontab with
  `flock` at reboot and every 5 minutes.
- **x**: `deploy/x/transcription.runachama.com.conf` proxies to
  `127.0.0.1:36419` with `client_max_body_size 0`, request buffering off, and
  long timeouts. The port itself stays on loopback.

### ken crontab

```cron
@reboot /usr/bin/flock -n /tmp/transcription-tunnel.lock /home/s22114/transcription-tunnel.sh >/dev/null 2>&1
*/5 * * * * /usr/bin/flock -n /tmp/transcription-tunnel.lock /home/s22114/transcription-tunnel.sh >/dev/null 2>&1
```

### x nginx and TLS

1. Install `deploy/x/transcription.runachama.com.http.conf` and reload nginx.
2. Issue the certificate:

   ```bash
   certbot certonly --webroot -w /var/www/html -d transcription.runachama.com \
     --non-interactive --agree-tos --email rui1412jp@gmail.com --keep-until-expiring
   ```

3. Replace the vhost with `deploy/x/transcription.runachama.com.conf`, run
   `nginx -t`, and reload nginx.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/system` | UI choices and upload limit |
| `POST` | `/api/uploads/init` | Start a chunked upload |
| `PUT` | `/api/uploads/{id}/chunks/{index}` | Upload one chunk |
| `POST` | `/api/uploads/{id}/complete` | Queue transcription |
| `GET` | `/api/jobs/{id}` | Job status and segments |
| `POST` | `/api/jobs/{id}/cancel` | Cancel a job |
| `GET` | `/api/jobs/{id}/download/{fmt}` | Download an export |
| `GET` | `/healthz` | Health check |

## Notes

- The service binds to `127.0.0.1` only; publish it through the tunnels.
- `data/`, `.env`, logs, and the virtual environment are not committed.
- GPU/CPU selection is internal. The UI intentionally exposes only model,
  language, task, beam width, VAD, prompt, and output formats.
