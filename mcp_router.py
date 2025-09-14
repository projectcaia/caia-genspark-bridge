from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

class MCPStatusResponse(BaseModel):
    status: str
    version: str
    timestamp: str

@router.get("/mcp", response_model=MCPStatusResponse)
def mcp_root():
    return MCPStatusResponse(
        status="ok",
        version="1.0.0",
        timestamp=datetime.utcnow().isoformat()
    )
