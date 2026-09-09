import numpy as np
import pytest

from scripts.evaluate_behavior_models import accepted_label


def test_probability_gate_requires_confidence_and_margin() -> None:
    names = {0: "empty", 1: "seated", 2: "standing"}
    assert accepted_label(np.array([0.05, 0.85, 0.10]), names)[0] == "seated"
    assert accepted_label(np.array([0.10, 0.70, 0.20]), names)[0] == "unknown"
    assert accepted_label(np.array([0.02, 0.55, 0.43]), names)[0] == "unknown"


def test_probability_gate_rejects_nonfinite_or_wrong_width() -> None:
    with pytest.raises(ValueError, match="Invalid"):
        accepted_label(np.array([float("nan"), 0.5]), {0: "a", 1: "b"})
    with pytest.raises(ValueError, match="Invalid"):
        accepted_label(np.array([1.0]), {0: "a", 1: "b"})


@pytest.mark.parametrize(
    "probabilities",
    [
        np.array([-0.01, 1.01]),
        np.array([0.2, 0.2]),
        np.array([0.8, 0.3]),
    ],
)
def test_probability_gate_rejects_values_that_are_not_probabilities(probabilities) -> None:
    with pytest.raises(ValueError, match="Invalid"):
        accepted_label(probabilities, {0: "a", 1: "b"})
