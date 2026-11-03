"""Small human-readable evaluation and repeatability check for the live model."""

import argparse
import json
from pathlib import Path

from fastapi import HTTPException

from app.analysis import get_analyzer
from app.schemas import ConversationInput


def main() -> int:
    parser = argparse.ArgumentParser(description="한국어 갈등 신호 예시 평가")
    parser.add_argument("--repeats", type=int, default=1, help="같은 예시 반복 횟수 (기본 1)")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("--repeats는 1~10 사이여야 합니다.")

    cases = json.loads((Path(__file__).resolve().parents[1] / "examples" / "eval_cases.json").read_text(encoding="utf-8"))
    try:
        provider = get_analyzer()
        # Repeated measurements must invoke the model rather than replay the cache.
        provider.cache_enabled = False
    except HTTPException as error:
        print(error.detail)
        return 2
    failures = 0
    for case in cases:
        request = ConversationInput.model_validate(case["input"])
        samples = [provider.analyze(request) for _ in range(args.repeats)]
        checks = case["checks"]
        passed = all(
            all(("min" not in bounds or getattr(sample, field) >= bounds["min"])
                and ("max" not in bounds or getattr(sample, field) <= bounds["max"])
                for field, bounds in checks.items())
            for sample in samples
        )
        failures += not passed
        scores = [{field: getattr(sample, field) for field in checks} for sample in samples]
        spread = {field: max(getattr(x, field) for x in samples) - min(getattr(x, field) for x in samples)
                  for field in checks}
        print(f"{'PASS' if passed else 'FAIL'} | {case['name']} | scores={scores} | spread={spread}")
    print(f"{len(cases) - failures}/{len(cases)} cases passed ({args.repeats} runs each)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
