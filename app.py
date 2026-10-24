from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Literal
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pwdlib import PasswordHash
from starlette.middleware.sessions import SessionMiddleware
from PIL import Image, ImageOps, UnidentifiedImageError


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIR / "quiet_space.db"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
UPLOAD_DIR = DATA_DIR / "uploads"
SECRET_PATH = DATA_DIR / ".session_secret"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_CATEGORIES = {"feelings", "playful", "silly", "jokes", "poetry", "creative"}
Image.MAX_IMAGE_PIXELS = 40_000_000
UPLOAD_DIR.mkdir(exist_ok=True)
password_hasher = PasswordHash.recommended()


def get_session_secret():
    configured_secret = os.getenv("SESSION_SECRET")
    if configured_secret:
        return configured_secret
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return SECRET_PATH.read_text(encoding="utf-8").strip()


def connect_to_database():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def store_uploaded_image(image_bytes: bytes) -> str:
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Choose an image smaller than 8 MB.")

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(status_code=415, detail="Choose a JPG, PNG, or WebP image.")
            if getattr(image, "n_frames", 1) > 1:
                raise HTTPException(status_code=415, detail="Animated images are not supported yet.")
            image.verify()

        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            image = ImageOps.contain(image, (1600, 1600), method=Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            filename = f"{uuid.uuid4().hex}.webp"
            image.save(UPLOAD_DIR / filename, format="WEBP", quality=84, method=6)
            return filename
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise HTTPException(status_code=415, detail="That image could not be opened. Try another one.")


def remove_uploaded_image(image_path):
    if image_path:
        image_file = UPLOAD_DIR / Path(image_path).name
        image_file.unlink(missing_ok=True)


def create_tables():
    with connect_to_database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        post_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(posts)").fetchall()
        }
        for column_name, definition in (
            ("category", "TEXT NOT NULL DEFAULT 'feelings'"),
            ("user_id", "INTEGER"),
            ("image_path", "TEXT"),
        ):
            if column_name not in post_columns:
                connection.execute(f"ALTER TABLE posts ADD COLUMN {column_name} {definition}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reaction TEXT NOT NULL CHECK (reaction IN ('heart', 'laugh', 'hug')),
                created_at TEXT NOT NULL,
                UNIQUE (post_id, user_id, reaction)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS follows (
                follower_id INTEGER NOT NULL,
                following_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (follower_id, following_id),
                CHECK (follower_id <> following_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "is_moderator" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN is_moderator INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                content_type TEXT NOT NULL CHECK (content_type IN ('post', 'comment')),
                content_id INTEGER NOT NULL,
                content_author TEXT NOT NULL,
                content_snapshot TEXT NOT NULL,
                content_image TEXT,
                reason TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                resolution TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                UNIQUE (reporter_id, content_type, content_id)
            )
            """
        )
        report_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(reports)").fetchall()
        }
        if "content_image" not in report_columns:
            connection.execute("ALTER TABLE reports ADD COLUMN content_image TEXT")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="Quiet Space API", lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    session_cookie="quiet_space_session",
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
)


@app.get("/healthz")
def health_check():
    return {"ok": True}
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
)


class SignUpDetails(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class LoginDetails(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=1, max_length=128)


class NewComment(BaseModel):
    message: str = Field(min_length=1, max_length=300)


class NewReaction(BaseModel):
    reaction: Literal["heart", "laugh", "hug"]


class NewReport(BaseModel):
    content_type: Literal["post", "comment"]
    content_id: int
    reason: Literal["harassment", "private_information", "spam", "other"]
    details: str = Field(default="", max_length=300)


class ResolveReport(BaseModel):
    action: Literal["remove", "dismiss"]


def format_post(row):
    return {
        "id": row["id"],
        "author": row["author"],
        "author_id": row["user_id"],
        "message": row["message"],
        "created_at": row["created_at"],
        "category": row["category"],
        "image_url": f"/uploads/{row['image_path']}" if row["image_path"] else None,
        "reaction_counts": {"heart": 0, "laugh": 0, "hug": 0},
        "my_reactions": [],
        "comment_count": 0,
        "is_following": False,
    }


def find_user_by_id(user_id):
    with connect_to_database() as connection:
        return connection.execute(
            "SELECT id, username, is_moderator FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


@app.post("/api/auth/signup", status_code=201)
def sign_up(details: SignUpDetails, request: Request):
    username = details.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        raise HTTPException(
            status_code=422,
            detail="Use 3–20 characters: letters, numbers, or underscores.",
        )

    password_hash = password_hasher.hash(details.password)
    created_at = datetime.now(timezone.utc).isoformat(timespec="minutes")
    # Privileged roles are assigned out of band by the site owner, never by
    # choosing a particular public username during signup.
    is_moderator = 0
    try:
        with connect_to_database() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, created_at, is_moderator) VALUES (?, ?, ?, ?)",
                (username, password_hash, created_at, is_moderator),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That username is already taken.")

    request.session["user_id"] = user_id
    return {"id": user_id, "username": username, "is_moderator": bool(is_moderator)}


@app.post("/api/auth/login")
def log_in(details: LoginDetails, request: Request):
    with connect_to_database() as connection:
        user = connection.execute(
            "SELECT id, username, password_hash, is_moderator FROM users WHERE username = ? COLLATE NOCASE",
            (details.username.strip(),),
        ).fetchone()

    if user is None or not password_hasher.verify(details.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="That username and password don’t match.")

    request.session.clear()
    request.session["user_id"] = user["id"]
    return {
        "id": user["id"],
        "username": user["username"],
        "is_moderator": bool(user["is_moderator"]),
    }


@app.get("/api/auth/me")
def current_session(request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        request.session.clear()
        return {"id": None, "username": None}
    return {
        "id": user["id"],
        "username": user["username"],
        "is_moderator": bool(user["is_moderator"]),
    }


@app.post("/api/auth/logout")
def log_out(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/posts")
def list_posts(
    request: Request,
    category: str = "all",
    feed: Literal["all", "following"] = "all",
):
    if category != "all" and category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=422, detail="Choose a valid section.")

    with connect_to_database() as connection:
        user_id = request.session.get("user_id")
        current_user = connection.execute(
            "SELECT id, username, is_moderator FROM users WHERE id = ?",
            (user_id,),
        ).fetchone() if user_id is not None else None
        if feed == "following" and current_user is None:
            return []

        query = "SELECT id, author, user_id, message, created_at, category, image_path FROM posts WHERE 1 = 1"
        query_params = []
        if category != "all":
            query += " AND category = ?"
            query_params.append(category)
        if feed == "following":
            query += " AND user_id IN (SELECT following_id FROM follows WHERE follower_id = ?)"
            query_params.append(user_id)
        query += " ORDER BY id DESC"
        rows = connection.execute(query, query_params).fetchall()
        posts = [format_post(row) for row in rows]
        if not posts:
            return posts

        post_ids = [post["id"] for post in posts]
        placeholders = ", ".join("?" for _ in post_ids)
        reaction_rows = connection.execute(
            f"SELECT post_id, reaction, COUNT(*) AS total FROM reactions "
            f"WHERE post_id IN ({placeholders}) GROUP BY post_id, reaction",
            post_ids,
        ).fetchall()
        comment_rows = connection.execute(
            f"SELECT post_id, COUNT(*) AS total FROM comments "
            f"WHERE post_id IN ({placeholders}) GROUP BY post_id",
            post_ids,
        ).fetchall()
        my_reaction_rows = []
        following_ids = set()
        if user_id is not None:
            my_reaction_rows = connection.execute(
                f"SELECT post_id, reaction FROM reactions "
                f"WHERE user_id = ? AND post_id IN ({placeholders})",
                [user_id, *post_ids],
            ).fetchall()
            following_ids = {
                row["following_id"] for row in connection.execute(
                    "SELECT following_id FROM follows WHERE follower_id = ?", (user_id,)
                ).fetchall()
            }

    posts_by_id = {post["id"]: post for post in posts}
    for row in reaction_rows:
        posts_by_id[row["post_id"]]["reaction_counts"][row["reaction"]] = row["total"]
    for row in comment_rows:
        posts_by_id[row["post_id"]]["comment_count"] = row["total"]
    for row in my_reaction_rows:
        posts_by_id[row["post_id"]]["my_reactions"].append(row["reaction"])
    for post in posts:
        post["is_following"] = post["author_id"] in following_ids
        post["can_delete"] = bool(
            current_user
            and (
                current_user["is_moderator"]
                or current_user["id"] == post["author_id"]
            )
        )
    return posts


@app.post("/api/posts", status_code=201)
async def create_post(
    request: Request,
    message: str = Form(),
    category: str = Form(default="feelings"),
    image: UploadFile | None = File(default=None),
):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in before sharing a thought.")

    message = message.strip()
    if not message or len(message) > 500:
        raise HTTPException(status_code=422, detail="Write a thought of up to 500 characters.")
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=422, detail="Choose a valid section.")

    image_path = None
    if image is not None:
        uploaded_bytes = await image.read(MAX_IMAGE_BYTES + 1)
        if uploaded_bytes:
            image_path = store_uploaded_image(uploaded_bytes)
    author = user["username"]
    created_at = datetime.now(timezone.utc).isoformat(timespec="minutes")
    try:
        with connect_to_database() as connection:
            cursor = connection.execute(
                "INSERT INTO posts (author, user_id, message, created_at, category, image_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (author, user["id"], message, created_at, category, image_path),
            )
            row = connection.execute(
                "SELECT id, author, user_id, message, created_at, category, image_path FROM posts WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
    except Exception:
        remove_uploaded_image(image_path)
        raise
    created_post = format_post(row)
    created_post["can_delete"] = True
    return created_post


@app.post("/api/users/{target_user_id}/follow")
def follow_user(target_user_id: int, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to follow someone.")
    if user["id"] == target_user_id:
        raise HTTPException(status_code=400, detail="You already have yourself.")

    created_at = datetime.now(timezone.utc).isoformat(timespec="minutes")
    with connect_to_database() as connection:
        target = connection.execute("SELECT id FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="That account could not be found.")
        connection.execute(
            "INSERT OR IGNORE INTO follows (follower_id, following_id, created_at) VALUES (?, ?, ?)",
            (user["id"], target_user_id, created_at),
        )
    return {"following": True, "user_id": target_user_id}


@app.delete("/api/users/{target_user_id}/follow")
def unfollow_user(target_user_id: int, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to manage who you follow.")
    with connect_to_database() as connection:
        connection.execute(
            "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
            (user["id"], target_user_id),
        )
    return {"following": False, "user_id": target_user_id}


@app.post("/api/posts/{post_id}/reactions")
def toggle_reaction(post_id: int, reaction: NewReaction, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to leave a reaction.")

    created_at = datetime.now(timezone.utc).isoformat(timespec="minutes")
    with connect_to_database() as connection:
        post = connection.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if post is None:
            raise HTTPException(status_code=404, detail="That post could not be found.")

        existing = connection.execute(
            "SELECT id FROM reactions WHERE post_id = ? AND user_id = ? AND reaction = ?",
            (post_id, user["id"], reaction.reaction),
        ).fetchone()
        if existing:
            connection.execute("DELETE FROM reactions WHERE id = ?", (existing["id"],))
            reacted = False
        else:
            connection.execute(
                "INSERT INTO reactions (post_id, user_id, reaction, created_at) VALUES (?, ?, ?, ?)",
                (post_id, user["id"], reaction.reaction, created_at),
            )
            reacted = True
        total = connection.execute(
            "SELECT COUNT(*) FROM reactions WHERE post_id = ? AND reaction = ?",
            (post_id, reaction.reaction),
        ).fetchone()[0]
    return {"reaction": reaction.reaction, "reacted": reacted, "total": total}


@app.get("/api/posts/{post_id}/comments")
def list_comments(post_id: int, request: Request):
    with connect_to_database() as connection:
        post = connection.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if post is None:
            raise HTTPException(status_code=404, detail="That post could not be found.")
        rows = connection.execute(
            "SELECT id, user_id, author, message, created_at FROM comments WHERE post_id = ? ORDER BY id ASC",
            (post_id,),
        ).fetchall()
        user_id = request.session.get("user_id")
        current_user = connection.execute(
            "SELECT id, is_moderator FROM users WHERE id = ?",
            (user_id,),
        ).fetchone() if user_id is not None else None
    comments = [dict(row) for row in rows]
    for comment in comments:
        comment["can_delete"] = bool(
            current_user
            and (
                current_user["is_moderator"]
                or current_user["id"] == comment["user_id"]
            )
        )
        comment.pop("user_id", None)
    return comments


@app.post("/api/posts/{post_id}/comments", status_code=201)
def create_comment(post_id: int, comment: NewComment, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to leave a comment.")

    message = comment.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Write a comment before posting.")

    created_at = datetime.now(timezone.utc).isoformat(timespec="minutes")
    with connect_to_database() as connection:
        post = connection.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
        if post is None:
            raise HTTPException(status_code=404, detail="That post could not be found.")
        cursor = connection.execute(
            "INSERT INTO comments (post_id, user_id, author, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (post_id, user["id"], user["username"], message, created_at),
        )
        row = connection.execute(
            "SELECT id, author, message, created_at FROM comments WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    created_comment = dict(row)
    created_comment["can_delete"] = True
    return created_comment


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to manage your post.")

    with connect_to_database() as connection:
        post = connection.execute(
            "SELECT id, author, user_id, image_path FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        if post is None:
            raise HTTPException(status_code=404, detail="That post could not be found.")
        if not user["is_moderator"] and user["id"] != post["user_id"]:
            raise HTTPException(status_code=403, detail="You can only delete your own post.")
        connection.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
        connection.execute("DELETE FROM reactions WHERE post_id = ?", (post_id,))
        connection.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    remove_uploaded_image(post["image_path"])
    return {"ok": True}


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to manage your comment.")

    with connect_to_database() as connection:
        comment = connection.execute(
            "SELECT id, author, user_id FROM comments WHERE id = ?", (comment_id,)
        ).fetchone()
        if comment is None:
            raise HTTPException(status_code=404, detail="That comment could not be found.")
        if not user["is_moderator"] and user["id"] != comment["user_id"]:
            raise HTTPException(status_code=403, detail="You can only delete your own comment.")
        connection.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    return {"ok": True}


@app.post("/api/reports", status_code=201)
def report_content(report: NewReport, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to report content.")

    table_name = "posts" if report.content_type == "post" else "comments"
    image_column = "image_path" if report.content_type == "post" else "NULL AS image_path"
    with connect_to_database() as connection:
        content = connection.execute(
            f"SELECT author, message, {image_column} FROM {table_name} WHERE id = ?",
            (report.content_id,),
        ).fetchone()
        if content is None:
            raise HTTPException(status_code=404, detail="That content could not be found.")
        try:
            cursor = connection.execute(
                """INSERT INTO reports
                   (reporter_id, content_type, content_id, content_author,
                    content_snapshot, content_image, reason, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user["id"], report.content_type, report.content_id,
                    content["author"], content["message"], content["image_path"], report.reason,
                    report.details.strip(), datetime.now(timezone.utc).isoformat(timespec="minutes"),
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="You have already reported this content.")
    return {"id": cursor.lastrowid, "ok": True}


@app.get("/api/moderation/reports")
def list_open_reports(request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None or not user["is_moderator"]:
        raise HTTPException(status_code=403, detail="Moderator access required.")
    with connect_to_database() as connection:
        rows = connection.execute(
            """SELECT reports.id, reports.content_type, reports.content_id,
                      reports.content_author, reports.content_snapshot, reports.reason,
                      reports.content_image, reports.details, reports.created_at,
                      users.username AS reporter
               FROM reports JOIN users ON users.id = reports.reporter_id
               WHERE reports.status = 'open' ORDER BY reports.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/moderation/reports/{report_id}/resolve")
def resolve_report(report_id: int, resolution: ResolveReport, request: Request):
    user_id = request.session.get("user_id")
    user = find_user_by_id(user_id) if user_id is not None else None
    if user is None or not user["is_moderator"]:
        raise HTTPException(status_code=403, detail="Moderator access required.")

    with connect_to_database() as connection:
        report = connection.execute(
            "SELECT id, content_type, content_id FROM reports WHERE id = ? AND status = 'open'",
            (report_id,),
        ).fetchone()
        if report is None:
            raise HTTPException(status_code=404, detail="That report is already resolved or missing.")

        if resolution.action == "remove":
            if report["content_type"] == "post":
                post = connection.execute(
                    "SELECT image_path FROM posts WHERE id = ?", (report["content_id"],)
                ).fetchone()
                connection.execute("DELETE FROM comments WHERE post_id = ?", (report["content_id"],))
                connection.execute("DELETE FROM reactions WHERE post_id = ?", (report["content_id"],))
                connection.execute("DELETE FROM posts WHERE id = ?", (report["content_id"],))
                if post:
                    remove_uploaded_image(post["image_path"])
            else:
                connection.execute("DELETE FROM comments WHERE id = ?", (report["content_id"],))
            connection.execute(
                "UPDATE reports SET status = 'resolved', resolution = 'removed', resolved_at = ? "
                "WHERE content_type = ? AND content_id = ? AND status = 'open'",
                (datetime.now(timezone.utc).isoformat(timespec="minutes"), report["content_type"], report["content_id"]),
            )
        else:
            connection.execute(
                "UPDATE reports SET status = 'resolved', resolution = 'dismissed', resolved_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(timespec="minutes"), report_id),
            )
    return {"ok": True, "action": resolution.action}


if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def serve_react_app(full_path: str):
        requested_file = FRONTEND_DIST / full_path
        if full_path and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(FRONTEND_DIST / "index.html")
