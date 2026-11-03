"""Deterministic scoring and temperature update, separate from the LLM."""

from app.schemas import FeatureScores


# Score of a message whose strongest signal sits at each level of the 0-4 rubric (hostility 3 = direct attack,
# 4 = strong insult or threat). Anchored to the rubric so one insult reads as an insult on the thermometer.
SEVERITY = (0, 20, 42, 68, 90)


def conflict_score(features: FeatureScores) -> float:
    """Heuristic 0-100 score. Anchors are MVP defaults, not calibrated probabilities.

    The strongest of hostility / sarcasm / blame sets the level (SEVERITY, interpolated); the other signals,
    escalation and repair move it from there (the other signals add at most 10). Sarcasm and blame count at 0.7 of
    hostility when they lead: with 0.8 and no cap, polite workplace sarcasm ("네네 항상 급한 업무가 있으시죠^^",
    sarcasm 4, blame 3) scored 98, as high as swearing.
    History: a plain weighted sum (until 2026-11-03) measured how many kinds of attack a message had; the next
    formula (0.65 strongest + 0.25 breadth) still left an insult alone ("닥쳐 한마디만 더 하면 차단한다", h3) at 60
    and kept the thermometer below its top stage through a quarrel with swearing (checked 2026-11-03, gpt-6-luna)."""
    h, s, b = features.hostility, features.sarcasm, features.blame
    level = max(h, 0.7 * s, 0.7 * b)
    low = int(level)
    base = float(SEVERITY[low]) if low >= 4 else SEVERITY[low] + (SEVERITY[low + 1] - SEVERITY[low]) * (level - low)
    breadth = min(10.0, 2 * (h + s + b - level))
    up, down = max(features.escalation_delta, 0), max(-features.escalation_delta, 0)
    score = float(base) + breadth + 4 * up - 8 * features.repair - 3 * down
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
