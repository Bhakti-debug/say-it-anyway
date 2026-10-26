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
from functools import lru_cache

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pwdlib import PasswordHash
from starlette.middleware.sessions import SessionMiddleware
from PIL import Image, ImageOps, UnidentifiedImageError


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = "/tmp/quiet-space" if os.getenv("VERCEL") == "1" else str(BASE_DIR)
DATA_DIR = Path(os.getenv("DATA_DIR", DEFAULT_DATA_DIR)).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "post-images").strip()
SUPABASE_STORAGE_ENABLED = bool(SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY)
if SUPABASE_STORAGE_ENABLED and not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
    raise RuntimeError("Set both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to enable photo storage.")
if os.getenv("VERCEL") == "1" and not (DATABASE_URL and SUPABASE_STORAGE_ENABLED):
    raise RuntimeError("Set DATABASE_URL, SUPABASE_URL, and SUPABASE_SERVICE_ROLE_KEY for Vercel.")
DATABASE_PATH = DATA_DIR / "quiet_space.db"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
UPLOAD_DIR = DATA_DIR / "uploads"
SECRET_PATH = DATA_DIR / ".session_secret"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_CATEGORIES = {"feelings", "playful", "silly", "jokes", "poetry", "creative"}
Image.MAX_IMAGE_PIXELS = 40_000_000
if not SUPABASE_STORAGE_ENABLED and os.getenv("VERCEL") != "1":
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
password_hasher = PasswordHash.recommended()


def get_session_secret():
    configured_secret = os.getenv("SESSION_SECRET")
    if configured_secret:
        return configured_secret
    if DATABASE_URL or os.getenv("VERCEL") == "1":
        raise RuntimeError("Set SESSION_SECRET in the hosting provider's environment settings.")
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return SECRET_PATH.read_text(encoding="utf-8").strip()


class PostgresConnection:
    """Keep the app's small SQLite-style execute calls usable with psycopg."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace("?", "%s"), parameters)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.connection.__exit__(exc_type, exc_value, traceback)


def connect_to_database():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            prepare_threshold=None,
            sslmode="require",
        )
        return PostgresConnection(connection)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def database_integrity_errors():
    if DATABASE_URL:
        import psycopg

        return (psycopg.IntegrityError,)
    return (sqlite3.IntegrityError,)


@lru_cache(maxsize=1)
def get_supabase_storage_client():
    if not SUPABASE_STORAGE_ENABLED:
        return None
    from supabase import create_client

    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def get_image_url(image_path):
    if not image_path:
        return None
    storage = get_supabase_storage_client()
    if storage is not None:
        return storage.storage.from_(SUPABASE_STORAGE_BUCKET).get_public_url(image_path)
    return f"/uploads/{Path(image_path).name}"


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
            optimized_image = BytesIO()
            image.save(optimized_image, format="WEBP", quality=84, method=6)
            if SUPABASE_STORAGE_ENABLED:
                get_supabase_storage_client().storage.from_(SUPABASE_STORAGE_BUCKET).upload(
                    path=filename,
                    file=optimized_image.getvalue(),
                    file_options={
                        "content-type": "image/webp",
                        "cache-control": "3600",
                        "upsert": "false",
                    },
                )
            else:
                (UPLOAD_DIR / filename).write_bytes(optimized_image.getvalue())
            return filename
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise HTTPException(status_code=415, detail="That image could not be opened. Try another one.")


def remove_uploaded_image(image_path):
    if image_path:
        if SUPABASE_STORAGE_ENABLED:
            get_supabase_storage_client().storage.from_(SUPABASE_STORAGE_BUCKET).remove([image_path])
            return
        image_file = UPLOAD_DIR / Path(image_path).name
        image_file.unlink(missing_ok=True)


def create_tables():
    if DATABASE_URL:
        create_postgres_tables()
        return

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


def create_postgres_tables():
    """Create the app schema in a new Supabase PostgreSQL project."""
    with connect_to_database() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS posts (
                id BIGSERIAL PRIMARY KEY,
                author TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'feelings',
                user_id BIGINT,
                image_path TEXT
            )"""
        )
        connection.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'feelings'")
        connection.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS user_id BIGINT")
        connection.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS image_path TEXT")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_moderator BOOLEAN NOT NULL DEFAULT FALSE
            )"""
        )
        connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_moderator BOOLEAN NOT NULL DEFAULT FALSE")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_unique ON users (LOWER(username))"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS reactions (
                id BIGSERIAL PRIMARY KEY,
                post_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                reaction TEXT NOT NULL CHECK (reaction IN ('heart', 'laugh', 'hug')),
                created_at TEXT NOT NULL,
                UNIQUE (post_id, user_id, reaction)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS follows (
                follower_id BIGINT NOT NULL,
                following_id BIGINT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (follower_id, following_id),
                CHECK (follower_id <> following_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS comments (
                id BIGSERIAL PRIMARY KEY,
                post_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                author TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS reports (
                id BIGSERIAL PRIMARY KEY,
                reporter_id BIGINT NOT NULL,
                content_type TEXT NOT NULL CHECK (content_type IN ('post', 'comment')),
                content_id BIGINT NOT NULL,
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
            )"""
        )
        connection.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS content_image TEXT")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="Say It Anyway API", lifespan=lifespan)
if not SUPABASE_STORAGE_ENABLED:
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
        "image_url": get_image_url(row["image_path"]),
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
    is_moderator = False
    try:
        with connect_to_database() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, created_at, is_moderator) "
                "VALUES (?, ?, ?, ?) RETURNING id",
                (username, password_hash, created_at, is_moderator),
            )
            user_id = cursor.fetchone()["id"]
    except database_integrity_errors():
        raise HTTPException(status_code=409, detail="That username is already taken.")

    request.session["user_id"] = user_id
    return {"id": user_id, "username": username, "is_moderator": bool(is_moderator)}


@app.post("/api/auth/login")
def log_in(details: LoginDetails, request: Request):
    with connect_to_database() as connection:
        user = connection.execute(
            "SELECT id, username, password_hash, is_moderator FROM users WHERE LOWER(username) = LOWER(?)",
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
                "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                (author, user["id"], message, created_at, category, image_path),
            )
            post_id = cursor.fetchone()["id"]
            row = connection.execute(
                "SELECT id, author, user_id, message, created_at, category, image_path FROM posts WHERE id = ?",
                (post_id,),
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
            "INSERT INTO follows (follower_id, following_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT DO NOTHING",
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
            "SELECT COUNT(*) AS total FROM reactions WHERE post_id = ? AND reaction = ?",
            (post_id, reaction.reaction),
        ).fetchone()["total"]
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
            "INSERT INTO comments (post_id, user_id, author, message, created_at) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (post_id, user["id"], user["username"], message, created_at),
        )
        comment_id = cursor.fetchone()["id"]
        row = connection.execute(
            "SELECT id, author, message, created_at FROM comments WHERE id = ?",
            (comment_id,),
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
        except database_integrity_errors():
            raise HTTPException(status_code=409, detail="You have already reported this content.")
            report_id = None
        else:
            report_id = cursor.fetchone()["id"]
    return {"id": report_id, "ok": True}


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
    reports = [dict(row) for row in rows]
    for report in reports:
        report["image_url"] = get_image_url(report["content_image"])
    return reports


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
