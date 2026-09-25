"""全部 ORM 模型。

编码分工：
  books.isbn        —— 书目级身份，可空，不唯一（同 ISBN 可能有多版本）
  book_copies.barcode —— 副本级唯一，借还书的唯一凭据
  books.clc_code    —— 中图法分类号，检索维度，非唯一
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    """统一用本地时间（MySQL 配了 default-time-zone='+08:00'）。"""
    return datetime.now()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, comment="学号/工号")
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    real_name: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("student", "teacher", "admin", name="user_role"), nullable=False
    )
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="1正常 0禁用")
    dept: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="院系/部门")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    max_borrow: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="当前有效的登录会话；重新登录会覆盖，旧的 token 随即失效"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("username", name="uk_username"),
        Index("idx_role", "role"),
    )

    records: Mapped[list["BorrowRecord"]] = relationship(back_populates="user")


class Category(Base):
    """中图法分类。"""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, comment="中图法分类号，如 TP311")
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("code", name="uk_category_code"),
        Index("idx_parent_id", "parent_id"),
    )


class Book(Base):
    """书目：一种书一行。"""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    isbn: Mapped[str | None] = mapped_column(String(17), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    author: Mapped[str | None] = mapped_column(String(100), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pub_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    clc_code: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="中图法分类号")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_copies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_copies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        Index("idx_title", "title"),
        Index("idx_author", "author"),
        Index("idx_isbn", "isbn"),
        Index("idx_clc_code", "clc_code"),
    )

    copies: Mapped[list["BookCopy"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )


class BookCopy(Base):
    """馆藏副本：一册书一行，借还的最小单位。"""

    __tablename__ = "book_copies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    barcode: Mapped[str] = mapped_column(String(32), nullable=False, comment="馆藏条码，唯一")
    location: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="馆藏位置")
    status: Mapped[str] = mapped_column(
        Enum(
            "available",
            "borrowed",
            "lost",
            "repair",
            "discarded",
            name="copy_status",
        ),
        nullable=False,
        default="available",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("barcode", name="uk_barcode"),
        Index("idx_copy_book_id", "book_id"),
        Index("idx_copy_status", "status"),
    )

    book: Mapped["Book"] = relationship(back_populates="copies")


class BorrowRecord(Base):
    """借阅记录。"""

    __tablename__ = "borrow_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    copy_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("book_copies.id"), nullable=False)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("books.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    borrow_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    return_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    renew_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        Enum("borrowed", "returned", name="record_status"), nullable=False, default="borrowed"
    )
    operator_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="经办人（管理员代借时）"
    )
    remark: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # 生成列：未还 = 1，已还 = NULL。配合下面的唯一索引，
    # 从数据库层保证「同一副本同时只能有一条在借记录」（UNIQUE 允许多个 NULL）。
    active_flag: Mapped[int | None] = mapped_column(
        Integer,
        Computed("CASE WHEN return_at IS NULL THEN 1 ELSE NULL END", persisted=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("copy_id", "active_flag", name="uk_copy_active"),
        Index("idx_user_status", "user_id", "status"),
        Index("idx_due_at", "due_at"),
        Index("idx_copy_status_rec", "copy_id", "status"),
    )

    user: Mapped["User"] = relationship(back_populates="records")
    book: Mapped["Book"] = relationship()
    copy: Mapped["BookCopy"] = relationship()


class InviteCode(Base):
    """教师注册邀请码：一码一用，有有效期，仅管理员可生成。"""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, comment="邀请码，12 位大写字母数字")
    note: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="备注，如发给谁")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True, comment="使用者"
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="生成人")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("code", name="uk_invite_code"),
        Index("idx_invite_used_by", "used_by"),
    )
