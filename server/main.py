"""FastAPI 应用入口：挂载 /api 路由，最后挂载三端静态页面。"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR
from .routers import auth, books, borrow, categories, invites, stats, users

app = FastAPI(
    title="图书管理系统",
    version="1.0.0",
    description=(
        "教师端 / 学生端共用的服务端。\n\n"
        "页面入口：`/teacher/`、`/student/`"
    ),
)


class NoCacheStaticFiles(StaticFiles):
    """静态资源禁用缓存，避免改了 JS 页面不刷新。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


# 校验出错时，字段名与 Pydantic 提示都换成中文，避免前端直接抛出英文报错
_FIELD_CN = {
    "username": "学号/工号", "password": "密码", "old_password": "原密码",
    "new_password": "新密码", "real_name": "姓名", "role": "角色",
    "client": "客户端类型", "refresh_token": "刷新令牌",
    "title": "书名", "author": "作者", "publisher": "出版社", "isbn": "ISBN",
    "clc_code": "中图法分类号", "pub_date": "出版日期", "price": "定价",
    "summary": "内容简介", "copies": "初始副本数量", "count": "副本数量",
    "location": "馆藏位置", "barcode": "馆藏条码", "barcode_prefix": "条码前缀",
    "code": "分类号", "name": "名称", "status": "状态",
    "invite_code": "邀请码", "valid_days": "有效天数", "note": "备注",
    "text": "名单内容", "ids": "图书列表", "year": "届数",
    "class_count": "班级数量", "per_class": "每班人数", "dept": "院系",
}


def _humanize(err: dict) -> str:
    loc = [str(p) for p in err.get("loc", [])[1:]]
    raw = loc[-1] if loc else ""
    field = _FIELD_CN.get(raw, raw or "参数")
    ctx = err.get("ctx") or {}
    kind = err.get("type", "")
    if kind == "less_than_equal":
        return f"{field}不能大于 {ctx.get('le')}"
    if kind == "greater_than_equal":
        return f"{field}不能小于 {ctx.get('ge')}"
    if kind in ("int_parsing", "int_type"):
        return f"{field}必须是整数"
    if kind in ("float_parsing", "float_type"):
        return f"{field}必须是数字"
    if kind in ("date_parsing", "date_from_datetime_parsing"):
        return f"{field}格式不正确，应为 YYYY-MM-DD"
    if kind == "missing":
        return f"{field}不能为空"
    if kind in ("string_too_short", "too_short"):
        if ctx.get("min_length") in (0, 1):
            return f"{field}不能为空"
        return f"{field}长度不足（至少 {ctx.get('min_length')} 个字符）"
    if kind in ("string_too_long", "too_long"):
        return f"{field}太长（最多 {ctx.get('max_length')} 个字符）"
    if kind == "enum":
        return f"{field}取值不合法"
    return f"{field}：{err.get('msg', '格式不正确')}"


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """把校验错误压成一句人话，前端直接展示。"""
    errors = exc.errors()
    detail = _humanize(errors[0]) if errors else "参数格式不正确"
    return JSONResponse(status_code=422, content={"detail": detail})


@app.get("/health", tags=["系统"], summary="健康检查")
def health():
    return {"ok": True, "service": "library-server"}


for router in (
    auth.router,
    categories.router,
    books.router,
    books.copies_router,
    borrow.router,
    users.router,
    invites.router,
    stats.router,
):
    app.include_router(router, prefix="/api")

# 静态目录必须放在所有 /api 路由之后，否则会吞掉 API 路由
app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="web")
