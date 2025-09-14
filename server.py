# server.py (최상위 디렉토리)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp_router import router as mcp_router

app = FastAPI()
app.include_router(mcp_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 필요 시 제한 가능
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
