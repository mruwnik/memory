"""
FastAPI application for the knowledge base.
"""
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session

from memory.common.db import get_scoped_session
from memory.common.db.models import SourceItem


app = FastAPI(title="Knowledge Base API")


def get_db():
    """Database session dependency"""
    db = get_scoped_session()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}


@app.get("/sources")
def list_sources(
    tag: str = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """List source items, optionally filtered by tag"""
    query = db.query(SourceItem)
    
    if tag:
        query = query.filter(SourceItem.tags.contains([tag]))
    
    return query.limit(limit).all()


@app.get("/sources/{source_id}")
def get_source(source_id: int, db: Session = Depends(get_db)):
    """Get a specific source by ID"""
    source = db.query(SourceItem).filter(SourceItem.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source 