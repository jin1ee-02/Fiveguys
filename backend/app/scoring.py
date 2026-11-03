"""Deterministic scoring and temperature update, separate from the LLM."""

from app.schemas import FeatureScores


def conflict_score(features: FeatureScores) -> float:
    """Heuristic 0-100 score. Weights are MVP defaults, not calibrated probabilities.

    The strongest of hostility / sarcasm / blame carries most of the score, and the three together add breadth.
    The earlier plain weighted sum (0.35 h + 0.25 s + 0.25 b ...) measured how many kinds of attack a message had
    rather than how strong it was: a personal attack without sarcasm (h3 b3) stayed at 52.5 and an insult or
    threat alone (h4) at 35, so the thermometer could not reach its upper stages (checked 2026-11-03 on a
    20-message roommate quarrel). Sarcasm and blame count at 0.8 of hostility when they are the strongest signal."""
    h, s, b = features.hostility, features.sarcasm, features.blame
    strongest = max(h, 0.8 * s, 0.8 * b) / 4
    breadth = (h + s + b) / 12
    r = features.repair / 4
    up = max(features.escalation_delta, 0) / 2
    down = max(-features.escalation_delta, 0) / 2
    score = 100 * (0.65 * strongest + 0.25 * breadth + 0.10 * up - 0.25 * r - 0.10 * down)
    return round(max(0.0, min(100.0, score)), 1)


def update_temperature(previous: float, raw_conflict: float, alpha: float = 0.3) -> float:
    return round((1 - alpha) * previous + alpha * raw_conflict, 1)


def rewrite_threshold(temperature: float) -> int:
    if temperature < 40:
        return 70
    if temperature < 60:
        return 60
    if temperature < 80:
        return 45
    return 30


def draft_decision(features: FeatureScores, risk: float, temperature: float) -> str:
    if features.confidence <= 1:
        return "low_confidence"
    return "rewrite_suggested" if risk >= rewrite_threshold(temperature) else "below_threshold"


RELATIONSHIP_OFFSET = {"직장 동료": -10, "연인": -5, "룸메이트": -5, "친구": 0}
