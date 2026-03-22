from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..models import ChatTask

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
def home():
    return render_template("landing.html")


@dashboard_bp.get("/workspace")
@login_required
def workspace():
    return render_template("dashboard/workspace.html")


@dashboard_bp.get("/assistant")
@login_required
def assistant():
    tasks = (
        ChatTask.query.filter_by(user_id=current_user.id)
        .order_by(ChatTask.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template("dashboard/index.html", tasks=tasks)


@dashboard_bp.get("/dashboard")
@login_required
def index():
    return workspace()
