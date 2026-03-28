import os
import sqlite3
import shutil
import re
import uuid
import logging
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth

# Включаем логирование
logging.basicConfig(level=logging.INFO)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key="EBANI_SUPA_SECRET_KEY")
templates = Jinja2Templates(directory="templates")

# Инициализация папок
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
AVATARS_DIR = os.path.join(STATIC_DIR, "avatars")

if not os.path.exists(AVATARS_DIR):
    os.makedirs(AVATARS_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def init_db():
    conn = sqlite3.connect("database.db")
    # Таблица товаров
    conn.execute('CREATE TABLE IF NOT EXISTS apps (id TEXT PRIMARY KEY, name TEXT, image_url TEXT)')
    # Таблица юзеров
    conn.execute('''CREATE TABLE IF NOT EXISTS users 
                    (email TEXT PRIMARY KEY, username TEXT UNIQUE, picture TEXT, 
                     balance REAL DEFAULT 0.0, is_admin INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

GOOGLE_CLIENT_ID = "527753630471-tbh1klclgcfu0acge29dfogdkh2sep0u.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

oauth = OAuth()
oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    # ВОЗВРАЩЕНО: Оригинальный адрес конфигурации Google
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

def is_logged_in(request: Request):
    return request.session.get("user") is not None

# --- ОСНОВНЫЕ РОУТЫ ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    apps = conn.execute("SELECT * FROM apps").fetchall()
    conn.close()
    
    user_data = None
    if is_logged_in(request):
        user_data = {
            "name": request.session.get("user"),
            "picture": request.session.get("user_picture")
        }
    return templates.TemplateResponse("index.html", {"request": request, "apps": apps, "user": user_data})

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

@app.get("/profile")
async def profile_redirect(request: Request):
    username = request.session.get("user")
    if not username:
        return RedirectResponse(url="/login")
    return RedirectResponse(url=f"/profile/{username}")

@app.get("/profile/{username}", response_class=HTMLResponse)
async def profile_page(request: Request, username: str):
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    user_info = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not user_info:
        return HTMLResponse("<h2>Пользователь не найден</h2><a href='/'>На главную</a>", status_code=404)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user_info})

# --- АДМИН-ПАНЕЛЬ ---

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return templates.TemplateResponse("admin.html", {"request": request, "users": users})

@app.post("/admin/add_product")
async def add_product(name: str = Form(...), image_url: str = Form(...)):
    product_id = str(uuid.uuid4())[:8]
    conn = sqlite3.connect("database.db")
    conn.execute("INSERT INTO apps (id, name, image_url) VALUES (?, ?, ?)", (product_id, name, image_url))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/ban/{username}")
async def ban_user(username: str):
    conn = sqlite3.connect("database.db")
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin", status_code=303)

# --- AUTH CALLBACKS ---

@app.get("/auth/google")
async def auth_google(request: Request):
    # ОБНОВЛЕНО: Твой реальный адрес на Render
    redirect_uri = "https://supa-market.onrender.com/auth/callback" 
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = token.get('userinfo')
        email = userinfo.get("email")
        picture = userinfo.get("picture")

        conn = sqlite3.connect("database.db")
        res = conn.execute("SELECT username, picture FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        request.session["user_email"] = email
        request.session["user_picture"] = picture 

        if res:
            request.session["user"] = res[0]
            request.session["user_picture"] = res[1] 
            return RedirectResponse(url=f"/profile/{res[0]}")
        return RedirectResponse(url="/set_username")
    except Exception as e:
        logging.error(f"Auth error: {e}")
        return RedirectResponse(url="/login")

@app.get("/set_username", response_class=HTMLResponse)
async def set_username_page(request: Request):
    if "user_email" not in request.session:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("set_username.html", {"request": request})

@app.post("/register")
async def register(request: Request, username: str = Form(...), profile_pic: UploadFile = File(None)):
    email = request.session.get("user_email")
    google_picture = request.session.get("user_picture")
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    clean_username = re.sub(r'\W+', '', username).lower()
    if len(clean_username) < 3:
        return HTMLResponse("Ник слишком короткий! <a href='/set_username'>Назад</a>")

    final_picture = google_picture
    if profile_pic and profile_pic.filename:
        try:
            file_extension = profile_pic.filename.split(".")[-1]
            file_name = f"{uuid.uuid4()}.{file_extension}"
            file_path = os.path.join(AVATARS_DIR, file_name)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(profile_pic.file, buffer)
            final_picture = f"/static/avatars/{file_name}"
        except Exception as e:
            logging.error(f"File save error: {e}")

    try:
        conn = sqlite3.connect("database.db")
        conn.execute("INSERT INTO users (email, username, picture, balance) VALUES (?, ?, ?, ?)", 
                     (email, clean_username, final_picture, 0.0))
        conn.commit()
        conn.close()
        request.session["user"] = clean_username
        request.session["user_picture"] = final_picture
        return RedirectResponse(url=f"/profile/{clean_username}", status_code=303)
    except sqlite3.IntegrityError:
        return HTMLResponse("Этот ник уже занят! <a href='/set_username'>Назад</a>")
    except Exception as e:
        logging.error(f"Database error: {e}")
        return HTMLResponse(f"Ошибка базы данных: {e}")

@app.get("/chats")
async def chats_page(request: Request):
    return "Раздел чатов в разработке."

@app.get("/app/{slug}")
async def app_page(request: Request, slug: str):
    return f"Маркет: {slug}. Пользователь: {request.session.get('user')}"
