import click
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from flask_wtf.csrf import CSRFError

from .blueprints.api import api_bp
from .blueprints.auth import auth_bp
from .blueprints.chat_api import chat_api_bp
from .blueprints.dashboard import dashboard_bp
from .blueprints.research_api import research_api_bp
from .blueprints.chart_api import chart_api_bp as chart_api_blueprint
from .config import Config
from .extensions import csrf, db, login_manager
from .models import User
from .services.task_queue import task_queue


def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)

    register_extensions(app)
    register_blueprints(app)
    register_cli(app)
    register_error_handlers(app)
    register_context(app)

    with app.app_context():
        db.create_all()

    task_queue.init_app(app)
    if not app.config.get("TESTING", False):
        task_queue.start()

    return app


def register_extensions(app: Flask) -> None:
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(chat_api_bp)
    app.register_blueprint(research_api_bp)
    app.register_blueprint(chart_api_blueprint)


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db_command():
        with app.app_context():
            db.create_all()
        click.echo("Database initialized.")


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        message = error.description or "页面令牌已失效，请刷新页面后重试。"
        if request.path.startswith("/api/"):
            return jsonify({"error": message}), 400
        return render_template("500.html"), 400

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_entity_too_large(_error):
        message = "上传内容过大，请缩小文件体积后重试。"
        if request.path.startswith("/api/"):
            return jsonify({"error": message}), 413
        return render_template("500.html"), 413

    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        if request.path.startswith("/api/"):
            message = getattr(error, "description", None) or error.name or "请求失败。"
            return jsonify({"error": message}), error.code or 500
        if getattr(error, "code", None) == 404:
            return render_template("404.html"), 404
        return render_template("500.html"), error.code or 500

    @app.errorhandler(Exception)
    def handle_unexpected_exception(error):
        app.logger.exception("Unhandled exception for path=%s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": f"服务器内部错误（{error.__class__.__name__}），请查看后端日志。"}), 500
        return render_template("500.html"), 500

    @app.get("/healthz")
    def healthz():
        return jsonify(
            {
                "status": "ok",
                "database": "configured",
                "gateway_configured": bool(app.config.get("OPENCLAW_GATEWAY_TOKEN")),
            }
        )


def register_context(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        return {
            "APP_BRAND": app.config["APP_BRAND"],
            "ALLOWED_AGENTS": app.config["OPENCLAW_ALLOWED_AGENTS"],
        }


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))
