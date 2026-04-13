"""Priority matrix and noise utilities for synthetic ticket data generation."""

import random

PRIORITY_MATRIX = {
    ("neutral", "less"): "low",
    ("neutral", "medium"): "medium",
    ("neutral", "high"): "medium",
    ("negative", "less"): "medium",
    ("negative", "medium"): "high",
    ("negative", "high"): "high",
    ("frustrated", "less"): "high",
    ("frustrated", "medium"): "high",
    ("frustrated", "high"): "urgent",
}

PRIORITY_LEVELS = ["low", "medium", "high", "urgent"]

SENTIMENTS = ["neutral", "negative", "frustrated"]

COMPLEXITIES = ["less", "medium", "high"]


def get_priority(sentiment, complexity):
    return PRIORITY_MATRIX[(sentiment, complexity)]


def perturb_priority(priority, rng=None):
    if rng is None:
        rng = random
    idx = PRIORITY_LEVELS.index(priority)
    shift = rng.choice([-1, 1])
    new_idx = max(0, min(len(PRIORITY_LEVELS) - 1, idx + shift))
    return PRIORITY_LEVELS[new_idx]


def apply_noise(priorities: list[str], noise_rate=0.07, seed=42):
    rng = random.Random(seed)
    result = list(priorities)
    n_noisy = max(1, round(len(result) * noise_rate))
    indices = rng.sample(range(len(result)), min(n_noisy, len(result)))
    for i in indices:
        result[i] = perturb_priority(result[i], rng=rng)
    return result
