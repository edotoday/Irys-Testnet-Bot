import random
import string
import math
import hashlib
from typing import Tuple, Dict

MAX_SAFE_INTEGER = float(9007199254740991)
CONST = float(0x178ba57548d)


def generate_session_id(timestamp: int) -> str:
    prefix = "game_"
    rand_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=9))
    return f"{prefix}{timestamp}_{rand_str}"


def generate_sprite_game_stats(
    time_sec: int = 15,
    acc_range: Tuple[float, float] = (70.0, 100.0),
    net_wpm_range: Tuple[int, int] = (20, 80),
    seed: int | None = None
) -> Dict:
    if seed is not None:
        random.seed(seed)

    minutes = time_sec / 60.0

    target_net_wpm = random.randint(*net_wpm_range)
    target_acc_pct = random.uniform(*acc_range)
    target_acc = target_acc_pct / 100.0

    correct_float = target_net_wpm * 5 * minutes
    correct = max(1, int(round(correct_float)))

    total_float = correct / max(1e-9, target_acc)
    total = int(round(total_float))
    if total < correct:
        total = correct
    incorrect = total - correct

    wpm = int(round((correct / 5.0) / minutes)) if minutes > 0 else 0
    accuracy_pct = round((correct / total) * 100.0, 2) if total > 0 else 100.0

    return {
        "wpm": wpm,
        "accuracy": accuracy_pct,
        "time": int(time_sec),
        "correctChars": correct,
        "incorrectChars": incorrect,
        "progressData": []
    }


def generate_anti_cheat_hash(wallet_address: str, game_stats: dict) -> str:
    wpm = game_stats["wpm"]
    acc = game_stats["accuracy"]
    time_ = game_stats["time"]
    s = game_stats["correctChars"]
    i = game_stats["incorrectChars"]

    l = s + i
    n = 0 + 23 * wpm + 89 * acc + 41 * time_ + 67 * s + 13 * i + 97 * l

    o = 0
    for idx, ch in enumerate(wallet_address):
        o += ord(ch) * (idx + 1)

    n += 31 * o
    tmp = (CONST * float(n)) % MAX_SAFE_INTEGER

    c = math.floor(tmp)
    a = f"{wallet_address.lower()}_{wpm}_{acc}_{time_}_{s}_{i}_{c}"

    h = hashlib.sha256(a.encode("utf-8")).hexdigest()[:32]
    return h


def generate_random_string(length: int = 17):
    characters = string.ascii_uppercase + string.ascii_lowercase + string.digits
    random_string = ''.join(random.choice(characters) for _ in range(length))
    return random_string
