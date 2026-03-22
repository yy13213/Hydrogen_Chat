import os
import secrets


EMBEDDED_OPENCLAW_GATEWAY_TOKEN = "33fdab2ebec3494dd0ed21dd67ae69f68538fb367b6506e4"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///openclaw_webapp.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    APP_BRAND = os.getenv("APP_BRAND", "HydrogenX")

    OPENCLAW_BASE_URL = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789").rstrip("/")
    # 按你的要求，默认把 gateway token 直接写在代码里；如需切换，仍可用环境变量覆盖。
    OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN") or EMBEDDED_OPENCLAW_GATEWAY_TOKEN
    OPENCLAW_DEFAULT_AGENT = os.getenv("OPENCLAW_DEFAULT_AGENT", "main")
    OPENCLAW_ALLOWED_AGENTS = [
        agent.strip()
        for agent in os.getenv("OPENCLAW_ALLOWED_AGENTS", "main,hydrogen").split(",")
        if agent.strip()
    ]
    OPENCLAW_MAX_CONCURRENT = int(os.getenv("OPENCLAW_MAX_CONCURRENT", "4"))
    OPENCLAW_MODEL = os.getenv("OPENCLAW_MODEL", "openclaw")
    OPENCLAW_TIMEOUT = int(os.getenv("OPENCLAW_TIMEOUT", "180"))
    OPENCLAW_CONNECT_TIMEOUT = int(os.getenv("OPENCLAW_CONNECT_TIMEOUT", "10"))
    OPENCLAW_READ_TIMEOUT = int(os.getenv("OPENCLAW_READ_TIMEOUT", str(OPENCLAW_TIMEOUT)))
    OPENCLAW_RETRY_TIMES = int(os.getenv("OPENCLAW_RETRY_TIMES", "2"))
    OPENCLAW_RETRY_BACKOFF = float(os.getenv("OPENCLAW_RETRY_BACKOFF", "1.0"))

    OPENCLAW_ARTIFACTS_DIR = os.getenv("OPENCLAW_ARTIFACTS_DIR", "instance/artifacts")
