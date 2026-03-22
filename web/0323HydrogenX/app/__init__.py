import click
from flask import Flask, jsonify, render_template
from dotenv import load_dotenv

from .blueprints.api import api_bp
from .blueprints.auth import auth_bp
from .blueprints.dashboard import dashboard_bp
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


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db_command():
        with app.app_context():
            db.create_all()
        click.echo("Database initialized.")


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
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
