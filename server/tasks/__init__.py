import threading
import time
from typing import Callable, List, Tuple

# Store references to running routine threads
ROUTINES: List[Tuple[str, threading.Thread]] = []


def _run_periodic(func: Callable, interval: int) -> None:
    """Run ``func`` every ``interval`` seconds in a loop."""
    while True:
        try:
            func()
        except Exception as e:  # pragma: no cover - best effort logging
            print(f"[RoutineError] {func.__name__} failed: {e}")
        time.sleep(interval)


def initialize_main_chat_routines() -> None:
    """Initialize background chat maintenance routines."""
    from server.tasks import auto_tasks

    # (name, callable, interval_seconds)
    jobs: List[Tuple[str, Callable, int]] = [
        ("auto_delete", auto_tasks.auto_delete, 60 * 60),  # hourly cleanup
        ("auto_reply", auto_tasks.auto_reply, 60),          # every minute
        ("report_high_priority", auto_tasks.report_high_priority, 60),  # every minute
    ]

    for name, func, interval in jobs:
        thread = threading.Thread(
            target=_run_periodic, args=(func, interval), daemon=True
        )
        thread.start()
        ROUTINES.append((name, thread))
