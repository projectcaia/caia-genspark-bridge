
import numpy as np

def l2norm(vec: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(vec) + 1e-12
    return vec / n

def cosine_sim(a: np.ndarray, B: np.ndarray) -> np.ndarray:
    a_norm = l2norm(a)
    B_norm = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-12)
    return (B_norm @ a_norm)
