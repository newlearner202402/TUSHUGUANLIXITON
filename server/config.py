"""集中配置：数据库连接、JWT、借阅规则常量。改规则只需要改这个文件。"""
import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

WEB_DIR = BASE_DIR / "web"

# ---------- 数据库 ----------
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "library")

# 连接串统一带 charset=utf8mb4，否则中文会变问号
DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
)
# 建库时用的连接串（不带库名）
SERVER_URL = (
    f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/?charset=utf8mb4"
)

# ---------- JWT ----------
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-please-change")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "14"))

# ---------- 服务 ----------
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# ---------- 借阅规则 ----------
# 可借册数上限（users.max_borrow 会按人覆盖这里的默认值）
BORROW_LIMIT = {"student": 5, "teacher": 10, "admin": 10}
# 借期天数
BORROW_DAYS = {"student": 30, "teacher": 60, "admin": 60}
# 续借
MAX_RENEW_TIMES = 2
RENEW_DAYS = 15
# 逾期：只提醒不罚款。改成 True 则逾期未还时也允许继续借书
ALLOW_BORROW_WHEN_OVERDUE = False
