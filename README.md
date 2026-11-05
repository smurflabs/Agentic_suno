<div align="center">

<img src="docs/logo.png" alt="Agentic" width="288">

**Agentic Suno: a small HTTP API for making songs with your Suno account, used by Agentic's Music tab.**

[![License: GPL v3](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)
![FastAPI](https://img.shields.io/badge/FastAPI-0.162-009688)

[Setup](#setup) · [Endpoints](#endpoints) · [Use with Agentic](#use-with-agentic) · [Agentic server](https://github.com/smurflabs/Agentic)

</div>

---

> **Unofficial.** This project is not affiliated with or endorsed by Suno. It talks to the same internal web API that suno.com's own pages use, with your own signed-in account. That API can change at any time, and automated use may be against Suno's terms. Use it with your own account, at your own risk. Songs use your account's credits, and the official downloads use its downloads.

## What is it?

Agentic Suno is a [FastAPI](https://fastapi.tiangolo.com) server that wraps Suno's web API, so programs can make songs and lyrics and download the results. The [Agentic server](https://github.com/smurflabs/Agentic) uses it for its **Music** tab and its music tools, but any HTTP client can use it.

- **Sign in once, in real Chrome.** `login.py` opens Google Chrome so you can sign in to suno.com normally. Google refuses sign-ins from automated browsers, which is why real Chrome is needed. The session is saved locally.
- **Runs headless after that.** The session token lasts about an hour, and the server renews it by itself through Suno's sign-in service (Clerk). If that fails, it opens the saved Chrome profile headless instead. You only sign in again when the Suno session itself ends.
- **Protected by an API key.** Every request except `/health` and `/docs` needs the key from `api_key.txt`, which is created on the first start.
- **Playable files.** Suno scrambles the audio its own player streams, so `/clip/{id}/download` fetches Suno's official download: a normal MP3 or M4A file.

## Setup

You need Python 3.10 or later (tested with 3.13), Google Chrome and a Suno account.

```bash
git clone https://github.com/smurflabs/Agentic_suno.git
cd Agentic_suno
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

python login.py                   # sign in to suno.com in the Chrome window; it closes by itself
python test_client.py             # optional: account, credits and recent songs (spends nothing)
python server.py                  # http://127.0.0.1:8000, interactive docs at /docs
```

On the first start the server writes a random API key to `api_key.txt`. Send it with every request as `X-API-Key: <key>` or `Authorization: Bearer <key>`.

| Environment variable | Default | Effect |
|---|---|---|
| `SUNO_HOST` | `127.0.0.1` | Address to listen on |
| `SUNO_PORT` | `8000` | Port |
| `SUNO_API_KEY` | (contents of `api_key.txt`) | The API key to require. `off` turns the check off; only do that on a trusted machine |
| `SUNO_CHROME` | the usual Chrome location | Path to the Chrome executable, for `login.py` and the fallback token renewal |

### Keeping it running

- **Windows:** `run-server.cmd` restarts the server whenever it stops and writes `server.log`. Start it at boot with a scheduled task:

  ```bat
  schtasks /Create /TN SunoAPI /SC ONSTART /RU SYSTEM /TR "\"C:\path\to\Agentic_suno\run-server.cmd\""
  ```

- **Linux and macOS:** `run-server.sh` does the same. With systemd:

  ```ini
  # /etc/systemd/system/agentic-suno.service
  [Unit]
  Description=Agentic Suno API
  After=network-online.target

  [Service]
  WorkingDirectory=/opt/Agentic_suno
  ExecStart=/opt/Agentic_suno/.venv/bin/python server.py
  Restart=always
  User=suno

  [Install]
  WantedBy=multi-user.target
  ```

## Endpoints

| Method and path | What it does |
|---|---|
| `GET /health` | Whether the saved session works (renews the token if needed). No API key needed |
| `GET /me` | Account information |
| `GET /credits` | Credits left, monthly usage, downloads left and pending batches |
| `POST /generate` | Make songs: `{prompt, tags?, title?, lyrics?, instrumental?, model?, wait?, timeout_s?}`. Returns the batch id and clip ids; with `wait: true` it waits until they are finished |
| `GET /clips?limit=20` | Recent songs in your library |
| `GET /clips/status?ids=a,b` | Several songs at once: status, audio and cover URLs, lyrics |
| `GET /clip/{id}` | One song |
| `GET /clip/{id}/download?format=mp3` | Suno's official download (`mp3` or `m4a`). The first download of a song uses one of your account's downloads; later ones are free. Answers 402 when none are left |
| `GET /clip/{id}/audio` | The raw stream, only for songs Suno does not scramble (otherwise 409: use `/download`) |
| `POST /lyrics` | AI-written lyrics: `{instruction}` |

The default model is `chirp-goose` (v6 mini). Full request and response shapes are at `/docs`.

```bash
curl -H "X-API-Key: $(cat api_key.txt)" http://127.0.0.1:8000/credits
```

## Use with Agentic

1. Run this server on the Agentic machine, or on another machine Agentic can reach.
2. In Agentic's `appsettings.json`, set `"Music": { "BaseUrl": "http://127.0.0.1:8000" }`.
3. In the Agentic web app, open **Music → Settings** and paste the key from `api_key.txt`. It is kept in Agentic's encrypted vault. You can also set the environment variable `AGENTIC_Music__ApiKey`.

The Music tab then makes songs, writes lyrics with AI, and keeps a library for each user. Downloads use Suno's official files.

## Security

- **`auth_state.json` and `chrome-profile/`** hold your signed-in Suno session. Anyone with them can use your account. `api_key.txt` protects this server.
- **Never share or commit these files.** `.gitignore` already leaves them out.
- **Network access:** the server listens on `127.0.0.1` by default. If you open it to the network, keep the API key on and put it behind a firewall or HTTPS reverse proxy.

## License

Agentic Suno is free software: you can redistribute it and/or modify it under the terms of the [GNU General Public License](LICENSE) as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
