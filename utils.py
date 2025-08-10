# utils.py
def trim(s: str, n: int = 4000) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n-3] + "..."
