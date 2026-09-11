"""Матчер: нормализация названий, поиск кандидатов, подтверждение, обновление цен."""
from app.matcher.matcher import (
    auto_match,
    confirm,
    find_candidates,
    refresh_prices,
    score_candidate,
)
from app.matcher.normalize import (
    display_name,
    normalize_name,
    parse_weight,
    similarity,
    weight_ok,
)

__all__ = [
    "find_candidates", "confirm", "auto_match", "refresh_prices", "score_candidate",
    "normalize_name", "display_name", "parse_weight", "similarity", "weight_ok",
]
