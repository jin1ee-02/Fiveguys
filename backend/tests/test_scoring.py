import unittest

from app.schemas import FeatureScores
from app.scoring import conflict_score, draft_decision, rewrite_threshold, update_temperature


def features(**changes):
    values = {
        "hostility": 0,
        "sarcasm": 0,
        "blame": 0,
        "repair": 0,
        "escalation_delta": 0,
        "confidence": 3,
        "rationale": "테스트",
    }
    values.update(changes)
    return FeatureScores(**values)


class ScoringTests(unittest.TestCase):
    def test_neutral_message_is_not_conflated_with_negative_emotion(self):
        self.assertEqual(conflict_score(features()), 0)
        self.assertEqual(update_temperature(50, 0), 35)

    def test_attack_scores_higher_than_repair(self):
        attack = features(hostility=3, sarcasm=4, blame=3, escalation_delta=2)
        repair = features(repair=4, escalation_delta=-2)
        self.assertGreater(conflict_score(attack), 80)
        self.assertEqual(conflict_score(repair), 0)

    def test_one_strong_signal_is_enough_and_breadth_adds(self):
        self.assertEqual(conflict_score(features(hostility=4, sarcasm=4, blame=4, escalation_delta=2)), 100)
        # An insult or threat alone, or a personal attack without sarcasm, reaches the upper stages on its own.
        self.assertGreater(conflict_score(features(hostility=4)), 70)
        self.assertGreater(conflict_score(features(hostility=3, blame=3, escalation_delta=1)), 60)
        # The same strongest signal scores higher when other kinds of attack come with it.
        self.assertGreater(conflict_score(features(hostility=3, blame=3)), conflict_score(features(hostility=3)))
        # Sarcasm or blame as the strongest signal counts a little less than open hostility.
        self.assertLess(conflict_score(features(sarcasm=4)), conflict_score(features(hostility=4)))

    def test_threshold_boundaries_and_confidence_gate(self):
        self.assertEqual([rewrite_threshold(x) for x in (39, 40, 60, 80)], [70, 60, 45, 30])
        self.assertEqual(draft_decision(features(confidence=1), 100, 80), "low_confidence")
        self.assertEqual(draft_decision(features(), 50, 60), "rewrite_suggested")
        self.assertEqual(draft_decision(features(), 44, 60), "below_threshold")

    def test_invalid_scores_are_rejected(self):
        with self.assertRaises(ValueError):
            features(hostility=5)


if __name__ == "__main__":
    unittest.main()
