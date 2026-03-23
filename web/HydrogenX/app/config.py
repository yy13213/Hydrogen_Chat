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

    HYDROGEN_UPLOADS_DIR = os.getenv("HYDROGEN_UPLOADS_DIR", "instance/uploads")
    HYDROGEN_MAX_UPLOAD_SIZE_BYTES = int(os.getenv("HYDROGEN_MAX_UPLOAD_SIZE_BYTES", str(10 * 1024 * 1024)))
    HYDROGEN_MAX_FILE_TEXT_CHARS = int(os.getenv("HYDROGEN_MAX_FILE_TEXT_CHARS", "12000"))
    HYDROGEN_MAX_TOTAL_CONTEXT_CHARS = int(os.getenv("HYDROGEN_MAX_TOTAL_CONTEXT_CHARS", "40000"))
    HYDROGEN_MAX_FILES_PER_TASK = int(os.getenv("HYDROGEN_MAX_FILES_PER_TASK", "5"))
    HYDROGEN_XLSX_MAX_ROWS_PER_SHEET = int(os.getenv("HYDROGEN_XLSX_MAX_ROWS_PER_SHEET", "200"))
    HYDROGEN_XLSX_MAX_COLS_PER_ROW = int(os.getenv("HYDROGEN_XLSX_MAX_COLS_PER_ROW", "20"))
    HYDROGEN_ALLOWED_UPLOAD_EXTENSIONS = {
        extension.strip().lower()
        for extension in os.getenv(
            "HYDROGEN_ALLOWED_UPLOAD_EXTENSIONS",
            ".txt,.md,.markdown,.json,.csv,.tsv,.pdf,.docx,.xlsx,.pptx,.py,.js,.ts,.html,.css,.xml,.yaml,.yml,.ini,.log,.sql",
        ).split(",")
        if extension.strip()
    }

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(25 * 1024 * 1024)))
