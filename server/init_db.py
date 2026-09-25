"""初始化：建库 + 建表 + 种子数据。可重复执行，已有数据不会重复插入。

用法：  .venv\\Scripts\\python.exe -m server.init_db
"""
import sys
from datetime import date

import pymysql
from sqlalchemy import select, text

from .config import BORROW_LIMIT, DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER
from .database import Base, SessionLocal, engine
from .models import Book, BookCopy, Category, User
from .security import hash_password

# 中图法常用分类
CATEGORIES = [
    ("A", "马列主义、毛泽东思想、邓小平理论", 1),
    ("B", "哲学、宗教", 1),
    ("B84", "心理学", 2),
    ("C", "社会科学总论", 1),
    ("D", "政治、法律", 1),
    ("E", "军事", 1),
    ("F", "经济", 1),
    ("F23", "会计", 2),
    ("G", "文化、科学、教育、体育", 1),
    ("G64", "高等教育", 2),
    ("H", "语言、文字", 1),
    ("H319", "英语", 2),
    ("I", "文学", 1),
    ("I247", "中国当代小说", 2),
    ("J", "艺术", 1),
    ("K", "历史、地理", 1),
    ("K248", "明代史", 2),
    ("N", "自然科学总论", 1),
    ("O", "数理科学和化学", 1),
    ("O13", "高等数学", 2),
    ("P", "天文学、地球科学", 1),
    ("Q", "生物科学", 1),
    ("R", "医药、卫生", 1),
    ("R2", "中国医学", 2),
    ("S", "农业科学", 1),
    ("T", "工业技术", 1),
    ("TP", "自动化技术、计算机技术", 2),
    ("TP311", "程序设计、软件工程", 3),
    ("U", "交通运输", 1),
    ("V", "航空、航天", 1),
    ("X", "环境科学、安全科学", 1),
    ("Z", "综合性图书", 1),
]

# 账号：(学号/工号, 密码, 姓名, 角色, 院系)
USERS = [
    ("admin", "admin123", "系统管理员", "admin", "图书馆"),
    ("T1001", "123456", "张建国", "teacher", "计算机学院"),
    ("T1002", "123456", "王淑芬", "teacher", "外国语学院"),
    ("2024001", "123456", "李明", "student", "计算机学院"),
    ("2024002", "123456", "赵小雨", "student", "文学院"),
]

# 示例图书：(书名, 作者, 出版社, ISBN, 中图法号, 出版年, 定价, 副本数, 位置)
BOOKS = [
    ("Python编程：从入门到实践（第3版）", "埃里克·马瑟斯", "人民邮电出版社",
     "9787115546081", "TP311", 2023, 109.80, 3, "A区-3排-2层"),
    ("算法导论（第3版）", "Thomas H. Cormen", "机械工业出版社",
     "9787111407010", "TP311", 2013, 128.00, 2, "A区-3排-3层"),
    ("深入理解计算机系统", "Randal E. Bryant", "机械工业出版社",
     "9787111544937", "TP311", 2016, 139.00, 2, "A区-3排-4层"),
    ("活着", "余华", "作家出版社",
     "9787506365437", "I247", 2012, 28.00, 3, "B区-1排-1层"),
    ("明朝那些事儿（全集）", "当年明月", "浙江人民出版社",
     "9787213042720", "K248", 2011, 358.00, 2, "B区-2排-1层"),
    ("高等数学（第七版）上册", "同济大学数学系", "高等教育出版社",
     "9787040396614", "O13", 2014, 47.60, 4, "C区-1排-2层"),
    ("新概念英语（第二册）", "L.G. Alexander", "外语教学与研究出版社",
     "9787560022864", "H319", 1997, 35.90, 3, "C区-2排-1层"),
]


def ensure_database() -> None:
    """库不存在就建，字符集统一 utf8mb4。"""
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
    finally:
        conn.close()


def seed() -> None:
    db = SessionLocal()
    try:
        # 分类
        if db.scalar(select(Category.id).limit(1)) is None:
            db.add_all(
                [
                    Category(code=code, name=name, level=level)
                    for code, name, level in CATEGORIES
                ]
            )
            db.commit()
            print(f"  分类：新增 {len(CATEGORIES)} 条")

        # 用户
        if db.scalar(select(User.id).limit(1)) is None:
            for username, password, real_name, role, dept in USERS:
                db.add(
                    User(
                        username=username,
                        password_hash=hash_password(password),
                        real_name=real_name,
                        role=role,
                        dept=dept,
                        max_borrow=BORROW_LIMIT[role],
                    )
                )
            db.commit()
            print(f"  用户：新增 {len(USERS)} 条")

        # 图书 + 副本
        if db.scalar(select(Book.id).limit(1)) is None:
            # 同一分类号下的流水号要连续累加，否则条码会重复
            counters: dict[str, int] = {}
            for title, author, publisher, isbn, clc, year, price, copies, location in BOOKS:
                book = Book(
                    title=title,
                    author=author,
                    publisher=publisher,
                    isbn=isbn,
                    clc_code=clc,
                    pub_date=date(year, 1, 1),
                    price=price,
                    total_copies=copies,
                    available_copies=copies,
                )
                db.add(book)
                db.flush()
                start = counters.get(clc, 0)
                for i in range(1, copies + 1):
                    db.add(
                        BookCopy(
                            book_id=book.id,
                            barcode=f"{clc}{start + i:06d}",
                            location=location,
                            status="available",
                        )
                    )
                counters[clc] = start + copies
            db.commit()
            print(f"  图书：新增 {len(BOOKS)} 种，共 {sum(b[7] for b in BOOKS)} 册")
    finally:
        db.close()


def migrate_drop_kiosk_role() -> None:
    """移除定点端遗留：删掉 kiosk 账号，并把 user_role 枚举里的 kiosk 去掉。

    `create_all` 不会修改已存在的列，所以老库必须显式 ALTER。
    新建的库枚举本来就不含 kiosk，这里会自动跳过。
    """
    with engine.begin() as conn:
        removed = conn.execute(text("DELETE FROM users WHERE role = 'kiosk'")).rowcount
        if removed:
            print(f"  迁移：删除 {removed} 个定点机账号")

        col = conn.execute(
            text(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = 'users' AND COLUMN_NAME = 'role'"
            ),
            {"db": DB_NAME},
        ).scalar()
        if col and "kiosk" in col:
            conn.execute(
                text(
                    "ALTER TABLE users MODIFY role "
                    "ENUM('student','teacher','admin') NOT NULL"
                )
            )
            print("  迁移：user_role 枚举已去掉 kiosk")


def migrate_add_session_id() -> None:
    """给 users 加 session_id 列（顶号用）。

    老库的 users 表已经存在，`create_all` 不会补新列，只能显式 ALTER。
    新建的库 create_all 已经带上了这一列，这里会自动跳过。
    """
    with engine.begin() as conn:
        col = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = 'users' AND COLUMN_NAME = 'session_id'"
            ),
            {"db": DB_NAME},
        ).scalar()
        if col is None:
            conn.execute(text("ALTER TABLE users ADD COLUMN session_id VARCHAR(64) NULL"))
            print("  迁移：users 已加 session_id 列")


def main() -> int:
    print(f"目标数据库：{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
    ensure_database()
    print("  数据库：已就绪")
    Base.metadata.create_all(engine)
    print("  数据表：已就绪")
    migrate_drop_kiosk_role()
    migrate_add_session_id()
    seed()
    print("\n初始化完成。默认账号：")
    print("  管理员   admin   / admin123")
    print("  教师     T1001   / 123456")
    print("  学生     2024001 / 123456")
    return 0


if __name__ == "__main__":
    sys.exit(main())
