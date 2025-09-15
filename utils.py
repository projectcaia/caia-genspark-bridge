# utils.py
from typing import Optional
import unicodedata
import re

def trim(
    s: Optional[str],
    n: int = 4000,
    suffix: str = "…",
    prefer_word_boundary: bool = True,
) -> str:
    """
    길이 n을 넘으면 말줄임 처리.
    - 단어 경계(공백)에서 자르려고 시도 후, 실패 시 하드 컷
    - 결합 문자/ZWJ(이모지 조합 등) 중간 절단을 피함
    - suffix 길이를 고려해 실제 본문 길이를 계산
    """
    s = (s or "").strip()

    if n <= 0:
        return ""

    if len(s) <= n:
        return s

    # suffix가 전체 길이를 먹는 극단 케이스 방어
    if len(suffix) >= n:
        return suffix[:n]

    limit = n - len(suffix)
    cut = limit

    if prefer_word_boundary:
        # limit 내에서 마지막 공백 이전까지 자르기 시도
        # candidate 끝쪽의 "공백+마지막 단어 조각"을 찾아 그 시작 지점 이전으로 자름
        candidate = s[:limit]
        m = re.search(r"\s+\S*$", candidate)
        if m:
            proposed = m.start()
            # 너무 짧아지면(60% 미만) 미관상 좋지 않으니 하드 컷 유지
            if proposed >= int(limit * 0.6):
                cut = proposed

    # 결합 문자나 ZWJ(\u200d) 중간 절단 방지
    idx = cut - 1
    while idx >= 0:
        ch = s[idx]
        if unicodedata.combining(ch) or ch == "\u200d":
            idx -= 1
            continue
        break
    cut = max(0, idx + 1)

    chunk = s[:cut].rstrip()
    return chunk + suffix
