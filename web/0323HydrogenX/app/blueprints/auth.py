from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.workspace"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if len(username) < 3:
            errors.append("用户名至少需要 3 个字符。")
        if "@" not in email or "." not in email:
            errors.append("请输入有效邮箱。")
        if len(password) < 8:
            errors.append("密码至少需要 8 个字符。")
        if password != confirm_password:
            errors.append("两次输入的密码不一致。")
        if User.query.filter_by(username=username).first():
            errors.append("该用户名已被注册。")
        if User.query.filter_by(email=email).first():
            errors.append("该邮箱已被注册。")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("auth/register.html")

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()

        flash("注册成功，请登录。", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.workspace"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("用户名或密码错误。", "danger")
            return render_template("auth/login.html")

        user.last_login_at = datetime.utcnow()
        db.session.commit()

        login_user(user, remember=True)
        flash("登录成功。", "success")
        next_url = request.args.get("next")
        return redirect(next_url or url_for("dashboard.workspace"))

    return render_template("auth/login.html")


@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    flash("你已安全退出登录。", "info")
    return redirect(url_for("auth.login"))
