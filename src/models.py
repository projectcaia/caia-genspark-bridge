
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class ERSP(BaseModel):
    event: str = ""
    interpretation: str = ""
    lesson: str = ""
    if_then: str = ""

class Memory(BaseModel):
    id: str
    type: str = "existence"
    actor: str = "Caia"
    content: str = ""
    fulltext: Optional[str] = None
    importance: str = "medium"
    ersp: Optional[ERSP] = None
    tags: List[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
