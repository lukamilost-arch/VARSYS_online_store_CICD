import hmac
import os
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("STORE_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.environ.get("STORE_DATABASE", str(Path(app.instance_path) / "store.db")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("STORE_HTTPS") == "1",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config:
        app.config.update(config)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(error):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.cli.command("init-db")
    @click.option("--demo", is_flag=True, help="Add catalog and demo accounts.")
    def init_db(demo):
        Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
        db().executescript(Path(app.root_path, "schema.sql").read_text())
        if demo:
            with db():
                for name, role in [("alice", "customer"), ("bob", "customer"), ("admin", "admin")]:
                    db().execute(
                        "INSERT OR IGNORE INTO users (name, password_hash, role) VALUES (?, ?, ?)",
                        (name, generate_password_hash("fieldstore-demo"), role),
                    )
                db().executemany(
                    "INSERT OR IGNORE INTO products (name, description, price, stock) VALUES (?, ?, ?, ?)",
                    [
                        ("Canvas Tote", "Heavy cotton canvas with a flat base and an inner pocket.", 2400, 12),
                        ("Steel Flask", "A double-wall, 500 ml bottle with a screw-top lid.", 3200, 8),
                        ("Desk Lamp", "A compact reading lamp with an adjustable shade.", 6800, 5),
                        ("Utility Tray", "A shallow powder-coated steel tray for everyday essentials.", 1800, 10),
                    ],
                )
        click.echo("Database ready.")

    @app.before_request
    def load_user():
        if request.endpoint in {"static", "health"}:
            return
        user_id = session.get("user_id")
        g.user = db().execute("SELECT id, name, role FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
        if request.method == "POST":
            token = session.get("csrf")
            submitted = request.form.get("csrf", "").encode()
            if not token or not hmac.compare_digest(token.encode(), submitted):
                abort(400, "Invalid or expired form token. Reload the page and try again.")

    @app.context_processor
    def form_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf": session["csrf"]}

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.endpoint not in {"static", "health"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.template_filter("money")
    def money(cents):
        return f"€{cents // 100:,}.{cents % 100:02d}"

    def require_role(role):
        def decorate(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if g.user is None:
                    return redirect(url_for("login"))
                if g.user["role"] != role:
                    abort(403)
                return view(*args, **kwargs)
            return wrapped
        return decorate

    def number(field, low, high):
        try:
            value = int(request.form.get(field, ""))
        except ValueError:
            abort(400, f"Enter a whole number for {field}.")
        if not low <= value <= high:
            abort(400, f"{field.capitalize()} must be between {low} and {high}.")
        return value

    @app.get("/health")
    def health():
        try:
            db().execute("SELECT 1 FROM products LIMIT 1").fetchone()
        except sqlite3.Error:
            return {"status": "unavailable"}, 503
        return {"status": "ok"}

    @app.get("/")
    def catalog():
        query = request.args.get("q", "").strip()[:100]
        products = db().execute(
            "SELECT * FROM products WHERE instr(lower(name), lower(?)) > 0 OR ? = '' ORDER BY id",
            (query, query),
        ).fetchall()
        return render_template("catalog.html", products=products, query=query)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = db().execute(
                "SELECT * FROM users WHERE name = ?", (request.form.get("username", "").strip(),)
            ).fetchone()
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()
                session["user_id"] = user["id"]
                return redirect(url_for("inventory" if user["role"] == "admin" else "catalog"))
            return render_template("login.html", error="Invalid username or password."), 401
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("catalog"))

    @app.route("/checkout/<int:product_id>", methods=["GET", "POST"])
    @require_role("customer")
    def checkout(product_id):
        connection = db()
        if request.method == "GET":
            product = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if product is None:
                abort(404)
            return render_template("checkout.html", product=product)
        quantity = number("quantity", 1, 99)
        with connection:
            # Serialize stock changes and capture the current price in the same transaction.
            connection.execute("BEGIN IMMEDIATE")
            product = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if product is None:
                abort(404)
            if product["stock"] < quantity:
                abort(409, "There is insufficient stock for this order.")
            connection.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))
            order = connection.execute(
                "INSERT INTO orders (user_id, product_name, unit_price, quantity) VALUES (?, ?, ?, ?)",
                (g.user["id"], product["name"], product["price"], quantity),
            )
        flash("Order placed.")
        return redirect(url_for("order_detail", order_id=order.lastrowid), code=303)

    @app.get("/orders")
    @require_role("customer")
    def orders():
        rows = db().execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", (g.user["id"],)).fetchall()
        return render_template("orders.html", orders=rows)

    @app.get("/orders/<int:order_id>")
    @require_role("customer")
    def order_detail(order_id):
        order = db().execute(
            "SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, g.user["id"])
        ).fetchone()
        if order is None:
            abort(404)
        return render_template("order.html", order=order)

    @app.get("/inventory")
    @require_role("admin")
    def inventory():
        return render_template("inventory.html", products=db().execute("SELECT * FROM products ORDER BY id").fetchall())

    @app.post("/inventory/<int:product_id>")
    @require_role("admin")
    def update_product(product_id):
        price = number("price", 1, 99999999)
        stock = number("stock", 0, 999999)
        with db() as connection:
            result = connection.execute("UPDATE products SET price = ?, stock = ? WHERE id = ?", (price, stock, product_id))
            if result.rowcount == 0:
                abort(404)
        flash("Inventory updated.")
        return redirect(url_for("inventory"), code=303)

    return app
