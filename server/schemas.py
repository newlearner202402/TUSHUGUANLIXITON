"""Pydantic 出入参模型。"""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------- 通用 ----------------
class Page(BaseModel):
    total: int
    page: int
    size: int
    items: list


# ---------------- 认证 ----------------
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)
    client: str = Field(default="web", description="web / teacher / student")


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    real_name: str
    username: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class RegisterIn(BaseModel):
    """教师自助注册。role 一律由服务端写死为 teacher，不接受前端传入。"""

    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    real_name: str = Field(min_length=1, max_length=50)
    invite_code: str = Field(min_length=1, max_length=16)
    dept: str | None = Field(default=None, max_length=50)
    phone: str | None = Field(default=None, max_length=20)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    real_name: str
    role: str
    status: int
    dept: str | None = None
    phone: str | None = None
    max_borrow: int


# ---------------- 分类 ----------------
class CategoryIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=50)
    parent_id: int | None = None
    level: int = 1


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    parent_id: int | None = None
    level: int


# ---------------- 图书 ----------------
class BookIn(BaseModel):
    isbn: str | None = Field(default=None, max_length=17)
    title: str = Field(min_length=1, max_length=200)
    author: str | None = Field(default=None, max_length=100)
    publisher: str | None = Field(default=None, max_length=100)
    pub_date: date | None = None
    price: float | None = None
    clc_code: str | None = Field(default=None, max_length=20)
    summary: str | None = None
    # 新建图书时可直接带出 N 个副本
    copies: int = Field(default=0, ge=0, le=500, description="同时生成的副本数量")
    location: str | None = Field(default=None, max_length=50)


class BookUpdate(BaseModel):
    isbn: str | None = None
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    pub_date: date | None = None
    price: float | None = None
    clc_code: str | None = None
    summary: str | None = None


class CopyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    barcode: str
    location: str | None = None
    status: str


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    isbn: str | None = None
    title: str
    author: str | None = None
    publisher: str | None = None
    pub_date: date | None = None
    price: float | None = None
    clc_code: str | None = None
    summary: str | None = None
    total_copies: int
    available_copies: int
    category_name: str | None = None


class BookDetail(BookOut):
    copies_list: list[CopyOut] = []


class CopyCreateIn(BaseModel):
    count: int = Field(default=1, ge=1, le=500)
    location: str | None = Field(default=None, max_length=50)
    barcode_prefix: str | None = Field(default=None, max_length=20)


class BatchDeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class BatchSkippedItem(BaseModel):
    id: int
    title: str | None = None
    reason: str


class BatchDeleteResult(BaseModel):
    deleted: int
    skipped: list[BatchSkippedItem] = []


class CopyUpdateIn(BaseModel):
    location: str | None = None
    status: str | None = None


# ---------------- 借还 ----------------
class BorrowIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=32)
    # 教师/管理员代借时填读者的学号/工号；学生自己借不用填
    username: str | None = None


class BorrowByBookIn(BaseModel):
    """自助借书（学生端）：按书目借，具体借哪一册由服务端挑一册在馆的。"""

    book_id: int


class ReturnIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=32)
    # 教师/管理员代还时填读者的学号/工号；学生自己还不用填
    username: str | None = None


class BorrowRecordOut(BaseModel):
    id: int
    book_id: int
    copy_id: int
    barcode: str
    title: str
    author: str | None = None
    username: str
    real_name: str
    borrow_at: datetime
    due_at: datetime
    return_at: datetime | None = None
    renew_count: int
    status: str
    overdue_days: int = 0


class BorrowResult(BaseModel):
    ok: bool
    message: str
    record: BorrowRecordOut | None = None


# ---------------- 用户管理 ----------------
class UserCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    real_name: str = Field(min_length=1, max_length=50)
    role: str = Field(default="student")
    dept: str | None = None
    phone: str | None = None
    max_borrow: int | None = Field(default=None, ge=0, le=100)


class UserUpdateIn(BaseModel):
    real_name: str | None = None
    role: str | None = None
    status: int | None = None
    dept: str | None = None
    phone: str | None = None
    max_borrow: int | None = Field(default=None, ge=0, le=100)


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)


class ImportResult(BaseModel):
    created: int
    skipped: int
    errors: list[str] = []


class BatchRangeIn(BaseModel):
    """按学号范围生成学生账号：4 位届数 + 2 位班号 + 2 位序号。"""

    year: int = Field(ge=2000, le=2100, description="入学届数，如 2024")
    class_count: int = Field(ge=1, le=99, description="班级数量")
    per_class: int = Field(ge=1, le=99, description="每班人数")
    dept: str | None = Field(default=None, max_length=50)


class BatchPasteIn(BaseModel):
    """粘贴名单建号，每行「学号,姓名(,手机号)」，姓名可省略。"""

    text: str
    role: str = Field(default="student", description="目前仅支持 student")
    dept: str | None = Field(default=None, max_length=50)


# ---------------- 邀请码 ----------------
class InviteCreateIn(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    note: str | None = Field(default=None, max_length=100, description="备注，如发给谁")
    valid_days: int = Field(default=7, ge=1, le=365)


class InviteOut(BaseModel):
    id: int
    code: str
    note: str | None = None
    expires_at: datetime | None = None
    used_at: datetime | None = None
    created_at: datetime
    status: str = "unused"
    used_by_name: str | None = None


# ---------------- 统计 ----------------
class StatsOverview(BaseModel):
    total_books: int
    total_copies: int
    available_copies: int
    borrowed_copies: int
    total_users: int
    total_students: int
    total_teachers: int
    active_borrows: int
    overdue_count: int
    today_borrows: int
    today_returns: int
