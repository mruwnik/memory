from sqlalchemy import func
from sqlalchemy.orm import Session


def count_rows(db: Session, model, *criteria) -> int:
    return db.query(func.count()).select_from(model).filter(*criteria).scalar() or 0
