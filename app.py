"""
File Share — lightweight web app for browsing/downloading files.
All users share a common directory: /home/shared-files/<username>/
Users register with their Linux username + a chosen password.
"""

import json
import mimetypes
import os
import pwd
import shutil
from datetime import datetime
from pathlib import Path
from functools import wraps
from hashlib import sha256

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, session, flash, abort, jsonify,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SHARED_ROOT = Path("/home/shared-files")
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"

PREVIEWABLE_TEXT = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg",
    ".conf", ".log", ".sh", ".bash", ".py", ".js", ".ts", ".html", ".css",
    ".sql", ".env", ".toml", ".rst", ".bat", ".ps1", ".rb", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".hpp", ".makefile", ".dockerfile",
}
PREVIEWABLE_IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico"}
PREVIEWABLE_VIDEO = {".mp4", ".webm", ".ogg"}
PREVIEWABLE_AUDIO = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"}
PREVIEWABLE_PDF = {".pdf"}

# ---------------------------------------------------------------------------
# User store (simple JSON)
# ---------------------------------------------------------------------------

def _load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return {}


def _save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, indent=2))


def _hash_pw(password: str, salt: str = "") -> str:
    return sha256(f"{salt}:{password}".encode()).hexdigest()


def _user_exists_on_system(username: str) -> bool:
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def _get_user_folder(username: str) -> Path:
    """Return (and create) the user's folder under the shared root."""
    folder = SHARED_ROOT / username
    folder.mkdir(parents=True, exist_ok=True)
    # Auto-symlink their home directory if not already present
    home_link = folder / "home"
    home_dir = Path(f"/home/{username}")
    if not home_link.exists() and home_dir.is_dir():
        try:
            home_link.symlink_to(home_dir)
        except OSError:
            pass  # May lack permission if not owner
    return folder

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def safe_join(base: Path, rel: str) -> Path:
    """Resolve path and ensure it's within the user's base dir or a symlinked target."""
    base_path = base.resolve()
    target = (base_path / rel).resolve()
    if str(target).startswith(str(base_path)):
        return target
    # Allow symlinks whose real target is under the user's home directory
    # (e.g. /home/shared-files/hnester/reports -> /home/hnester/reports)
    username = base.name  # base is /home/shared-files/<username>
    home_dir = Path(f"/home/{username}").resolve()
    if str(target).startswith(str(home_dir)):
        return target
    abort(403)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


def file_info(path: Path, rel_root: Path, logical_parent: Path | None = None) -> dict:
    stat = path.stat()
    # Use the logical (non-resolved) path for rel_path so symlinks work
    if logical_parent is not None:
        rel = str(logical_parent / path.name)
    else:
        try:
            rel = str(path.relative_to(rel_root))
        except ValueError:
            rel = path.name
    return {
        "name": path.name,
        "is_dir": path.is_dir(),
        "size": human_size(stat.st_size) if not path.is_dir() else "",
        "size_bytes": stat.st_size if not path.is_dir() else 0,
        "modified": stat.st_mtime,
        "modified_fmt": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "rel_path": rel,
        "ext": path.suffix.lower(),
    }


def get_preview_type(ext: str) -> str | None:
    if ext in PREVIEWABLE_IMAGE:
        return "image"
    if ext in PREVIEWABLE_VIDEO:
        return "video"
    if ext in PREVIEWABLE_AUDIO:
        return "audio"
    if ext in PREVIEWABLE_PDF:
        return "pdf"
    if ext in PREVIEWABLE_TEXT or ext == "":
        return "text"
    return None


def sort_items(items: list, sort_by: str, order: str) -> list:
    reverse = order == "desc"
    if sort_by == "name":
        return sorted(items, key=lambda i: (not i["is_dir"], i["name"].lower()), reverse=reverse)
    elif sort_by == "size":
        return sorted(items, key=lambda i: (not i["is_dir"], i["size_bytes"]), reverse=reverse)
    elif sort_by == "date":
        return sorted(items, key=lambda i: (not i["is_dir"], i["modified"]), reverse=reverse)
    # Default: folders first, then alpha
    return sorted(items, key=lambda i: (not i["is_dir"], i["name"].lower()))

# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not _user_exists_on_system(username):
            flash("That username doesn't exist on this system.", "error")
            return render_template("login.html")

        users = _load_users()
        if username not in users:
            flash("Account not registered yet. Please register first.", "error")
            return render_template("login.html")

        if users[username]["hash"] != _hash_pw(password, users[username]["salt"]):
            flash("Invalid password.", "error")
            return render_template("login.html")

        session["username"] = username
        next_url = request.args.get("next", "/")
        return redirect(next_url)

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not _user_exists_on_system(username):
            flash("That username doesn't exist on this system.", "error")
            return render_template("register.html")

        if len(password) < 4:
            flash("Password must be at least 4 characters.", "error")
            return render_template("register.html")

        if password != confirm:
            flash("Passwords don't match.", "error")
            return render_template("register.html")

        users = _load_users()
        if username in users:
            flash("Account already registered. Please log in.", "error")
            return redirect(url_for("login"))

        salt = os.urandom(16).hex()
        users[username] = {"hash": _hash_pw(password, salt), "salt": salt}
        _save_users(users)

        # Create their shared folder
        _get_user_folder(username)

        session["username"] = username
        flash("Account created! You're now logged in.", "success")
        return redirect(url_for("browse"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# Routes — Browse
# ---------------------------------------------------------------------------

@app.route("/")
@app.route("/browse/")
@app.route("/browse/<path:subpath>")
@login_required
def browse(subpath=""):
    username = session["username"]
    user_root = _get_user_folder(username)
    target = safe_join(user_root, subpath)

    if not target.exists():
        abort(404)

    if target.is_file():
        return send_file(target, as_attachment=True)

    # Directory listing
    show_dotfiles = request.args.get("dotfiles") == "1"
    sort_by = request.args.get("sort", "name")
    order = request.args.get("order", "asc")

    items = []
    try:
        for child in target.iterdir():
            if not show_dotfiles and child.name.startswith("."):
                continue
            try:
                info = file_info(child, user_root, logical_parent=Path(subpath))
                info["preview_type"] = get_preview_type(info["ext"])
                items.append(info)
            except (PermissionError, OSError):
                continue
    except PermissionError:
        flash("Permission denied.", "error")

    items = sort_items(items, sort_by, order)

    # Breadcrumb
    rel = Path(subpath) if subpath else Path()
    breadcrumbs = []
    accum = Path()
    for part in rel.parts:
        accum = accum / part
        breadcrumbs.append({"name": part, "path": str(accum)})

    # Collect directories for the move modal
    def list_dirs(base: Path, rel_root: Path, depth=0):
        dirs = []
        if depth > 5:
            return dirs
        try:
            for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and not child.name.startswith("."):
                    rel = str(child.relative_to(rel_root))
                    dirs.append(rel)
                    dirs.extend(list_dirs(child, rel_root, depth + 1))
        except (PermissionError, OSError):
            pass
        return dirs

    all_dirs = [""] + list_dirs(user_root, user_root)  # "" = root

    return render_template(
        "browse.html",
        items=items,
        breadcrumbs=breadcrumbs,
        current_path=subpath,
        username=username,
        show_dotfiles=show_dotfiles,
        user_root=str(user_root),
        sort_by=sort_by,
        order=order,
        all_dirs=all_dirs,
    )


@app.route("/download/<path:subpath>")
@login_required
def download(subpath):
    username = session["username"]
    user_root = _get_user_folder(username)
    target = safe_join(user_root, subpath)
    if not target.is_file():
        abort(404)
    return send_file(target, as_attachment=True)

# ---------------------------------------------------------------------------
# Routes — Preview
# ---------------------------------------------------------------------------

@app.route("/preview/<path:subpath>")
@login_required
def preview(subpath):
    username = session["username"]
    user_root = _get_user_folder(username)
    target = safe_join(user_root, subpath)

    if not target.is_file():
        abort(404)

    ext = target.suffix.lower()
    ptype = get_preview_type(ext)

    if ptype is None:
        flash("This file type cannot be previewed.", "error")
        return redirect(url_for("browse", subpath=str(Path(subpath).parent)))

    text_content = None
    if ptype == "text":
        try:
            raw = target.read_bytes()
            text_content = raw.decode("utf-8", errors="replace")
            # Limit preview to first 100KB
            if len(text_content) > 102400:
                text_content = text_content[:102400] + "\n\n--- truncated at 100 KB ---"
        except Exception:
            flash("Could not read file.", "error")
            return redirect(url_for("browse", subpath=str(Path(subpath).parent)))

    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"

    # Breadcrumb
    rel = Path(subpath)
    breadcrumbs = []
    accum = Path()
    for part in rel.parent.parts:
        accum = accum / part
        breadcrumbs.append({"name": part, "path": str(accum)})

    return render_template(
        "preview.html",
        filename=target.name,
        subpath=subpath,
        parent_path=str(rel.parent) if str(rel.parent) != "." else "",
        preview_type=ptype,
        text_content=text_content,
        mime_type=mime_type,
        breadcrumbs=breadcrumbs,
        username=username,
    )


@app.route("/raw/<path:subpath>")
@login_required
def raw_file(subpath):
    """Serve file inline (not as attachment) for preview embedding."""
    username = session["username"]
    user_root = _get_user_folder(username)
    target = safe_join(user_root, subpath)
    if not target.is_file():
        abort(404)
    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return send_file(target, mimetype=mime_type, as_attachment=False)

# ---------------------------------------------------------------------------
# Routes — Rename & Move
# ---------------------------------------------------------------------------

@app.route("/rename", methods=["POST"])
@login_required
def rename():
    username = session["username"]
    user_root = _get_user_folder(username)
    old_path = request.form.get("path", "")
    new_name = request.form.get("new_name", "").strip()

    if not new_name or "/" in new_name or "\\" in new_name:
        flash("Invalid name.", "error")
        parent = str(Path(old_path).parent)
        return redirect(url_for("browse", subpath=parent if parent != "." else ""))

    target = safe_join(user_root, old_path)
    if not target.exists():
        abort(404)

    new_target = target.parent / new_name
    if new_target.exists():
        flash(f"'{new_name}' already exists.", "error")
    else:
        try:
            target.rename(new_target)
            flash(f"Renamed to '{new_name}'.", "success")
        except OSError as e:
            flash(f"Rename failed: {e}", "error")

    parent = str(Path(old_path).parent)
    return redirect(url_for("browse", subpath=parent if parent != "." else ""))


@app.route("/move", methods=["POST"])
@login_required
def move():
    username = session["username"]
    user_root = _get_user_folder(username)
    old_path = request.form.get("path", "")
    dest_dir = request.form.get("dest_dir", "")

    target = safe_join(user_root, old_path)
    if not target.exists():
        abort(404)

    dest = safe_join(user_root, dest_dir)
    if not dest.is_dir():
        flash("Destination directory does not exist.", "error")
        parent = str(Path(old_path).parent)
        return redirect(url_for("browse", subpath=parent if parent != "." else ""))

    new_target = dest / target.name
    if new_target.exists():
        flash(f"'{target.name}' already exists in destination.", "error")
    else:
        try:
            shutil.move(str(target), str(new_target))
            flash(f"Moved '{target.name}' to '{dest_dir or 'Home'}'.", "success")
        except OSError as e:
            flash(f"Move failed: {e}", "error")

    parent = str(Path(old_path).parent)
    return redirect(url_for("browse", subpath=parent if parent != "." else ""))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
