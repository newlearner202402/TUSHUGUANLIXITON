"""中图法分类。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_staff
from ..models import Category, User
from ..schemas import CategoryIn, CategoryOut

router = APIRouter(prefix="/categories", tags=["分类"])


@router.get("", response_model=list[CategoryOut], summary="分类列表")
def list_categories(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return (
        db.execute(select(Category).order_by(Category.code)).scalars().all()
    )


@router.post("", response_model=CategoryOut, summary="新增分类")
def create_category(
    payload: CategoryIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_staff),
):
    exists = db.execute(
        select(Category).where(Category.code == payload.code)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"分类号 {payload.code} 已存在"
        )
    cat = Category(**payload.model_dump())
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat
