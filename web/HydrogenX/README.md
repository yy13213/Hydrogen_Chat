# HydrogenX

新增能力：支持 OpenClaw 生成文件下载链接，并对不同用户的消息与文件进行隔离。

# HydrogenX

本项目现已改造成 HydrogenX 门户，其中 Hydrogen Assistant 为已接入的 OpenClaw 氢能科研助手模块。

# OpenClaw Flask Portal

A multi-user Flask web portal for securely using OpenClaw through a backend proxy.

## Features

- User registration, login, logout
- Password hashing with Werkzeug
- Session-based access control with Flask-Login
- Backend-only OpenClaw access, with per-user `x-openclaw-session-key`
- Task queue for concurrent multi-user requests
- SQLite persistence for users and chat tasks
- Bootstrap 5 responsive UI for desktop and mobile
- CSRF protection for forms and JSON POST requests
- Better gateway diagnostics for 401/403/404/502/503/504 responses
- Retry on transient gateway failures and network timeouts

## Architecture

```text
Browser -> Flask Web App -> OpenClaw Gateway (127.0.0.1:18789) -> Gemini
```

OpenClaw is never called directly from the browser. The Flask backend owns the gateway token and generates per-user session keys.

## Project Structure

```text
openclaw_flask_portal/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── extensions.py
│   ├── models.py
│   ├── blueprints/
│   │   ├── api.py
│   │   ├── auth.py
│   │   └── dashboard.py
│   ├── services/
│   │   ├── openclaw_client.py
│   │   └── task_queue.py
│   ├── static/
│   │   ├── css/style.css
│   │   └── js/app.js
│   └── templates/
│       ├── base.html
│       ├── auth/login.html
│       ├── auth/register.html
│       └── dashboard/index.html
├── .env.example
├── requirements.txt
├── run.py
└── README.md
```

## Quick Start

### 1) Prepare Python environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment variables

```bash
cp .env.example .env
```

Then edit `.env` as needed:

- `SECRET_KEY`: your Flask session secret
- `OPENCLAW_BASE_URL`: usually `http://127.0.0.1:18789`
- `OPENCLAW_ALLOWED_AGENTS`: for example `main,hydrogen`

`OPENCLAW_GATEWAY_TOKEN` is now embedded in `app/config.py` by default as requested. You can still override it via environment variable if you want to switch tokens later.

### 3) Initialize the database

```bash
flask --app run.py init-db
```

### 4) Run the app

```bash
python run.py
```

Open the site at `http://127.0.0.1:5000`.

## What changed for your curl-compatible path

The backend call now intentionally stays very close to this working request shape:

```bash
curl -X POST http://127.0.0.1:18789/v1/responses   -H "Authorization: Bearer 33fdab2ebec3494dd0ed21dd67ae69f68538fb367b6506e4"   -H "Content-Type: application/json"   -H "x-openclaw-agent-id: main"   -H "x-openclaw-session-key: web:user:demo001"   -d '{
    "model": "openclaw",
    "input": "Hello, how are you?",
    "stream": false,
    "user": "web-user-demo001"
  }'
```

Portal-side improvements:

- session key now prefers a stable user label derived from username, closer to `web:user:demo001`
- `user` field is derived from the same suffix, closer to `web-user-demo001`
- 502/503/504 errors now include clearer diagnostics and response snippets
- transient failures are retried automatically
- connect timeout and read timeout are split for easier troubleshooting

## OpenClaw prerequisites

Before starting this portal, confirm your OpenClaw gateway is healthy:

```bash
source ~/.bashrc
nvm use 24
systemctl --user daemon-reload
systemctl --user restart openclaw-gateway.service
systemctl --user status openclaw-gateway.service
ss -lntp | grep 18789
```

The gateway should be listening only on `127.0.0.1:18789`.

## Common reasons for 502 in this portal

- The gateway itself is up, but its upstream model/backend is not healthy
- The gateway token accepted by your manual curl is not the same token used by Flask
- The Flask app is generating a different `session_key` / `user` combination than the upstream expects
- Too many concurrent portal tasks are hitting a gateway configured with lower `maxConcurrent`
- The upstream takes too long and returns a timeout-like 502/504 chain

## Queueing strategy

This project includes an in-process dispatcher plus a thread pool. Requests are stored in the database with these states:

- `queued`
- `running`
- `completed`
- `failed`

The worker concurrency is controlled by `OPENCLAW_MAX_CONCURRENT`, which should normally align with your OpenClaw `maxConcurrent` configuration.

### Production note

The included queue is intentionally lightweight and works well for a single Flask process. For production deployment with multiple Gunicorn workers or multiple servers, move the queue to Redis + Celery or RQ, and keep the database as the source of truth for task state.

## Security notes

You explicitly asked to embed the gateway token in code instead of treating it as a secret. That change has been applied, but for shared repositories or public deployments it is still safer to move it back to environment variables.

## Suggested next upgrades

- Email verification
- Password reset
- Redis-backed queue
- Admin dashboard and metrics
- Streaming output support
- Per-user quotas and rate limiting
