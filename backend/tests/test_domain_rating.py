import pytest

from app.domain import rating as R
from app.domain.enums import GoalkeeperRatingMode, RatingKind

F = R.FIELD_ATTRIBUTES


def attrs(*values):
    return dict(zip(F, values, strict=True))


def test_average_is_mean_of_five_attributes():
    assert R.compute_average(attrs(5, 4, 3, 2, 1), F) == 3.0


@pytest.mark.parametrize(
    "avg,tier",
    [(5.0, "S"), (4.5, "S"), (4.49, "A"), (3.75, "A"), (3.74, "B"), (3.0, "B"), (2.99, "C"), (2.0, "C"),
     (1.99, "D"), (0.0, "D")],
)
def test_tier_thresholds(avg, tier):
    assert R.compute_tier(avg) == tier


def test_tier_is_robust_to_floating_point_error():
    # 4.5 calculado por média ponderada pode virar 4.4999999999 em float
    assert R.compute_tier(4.499999999999) == "S"
    assert R.compute_tier(4.4999) == "A"


def test_display_average_one_decimal():
    assert R.display_average(3.75) == 3.8
    assert R.display_average(3.749) == 3.7


def test_validate_scores():
    with pytest.raises(ValueError):
        R.validate_scores({"finishing": 3}, F)
    with pytest.raises(ValueError):
        R.validate_scores(attrs(6, 1, 1, 1, 1), F)
    assert R.validate_scores(attrs(5, 0, 1, 2, 3), F)["finishing"] == 5.0


def test_goalkeeper_attribute_modes():
    assert R.attributes_for(RatingKind.GOALKEEPER, GoalkeeperRatingMode.OWN) == R.GOALKEEPER_ATTRIBUTES
    assert R.attributes_for(RatingKind.GOALKEEPER, GoalkeeperRatingMode.GENERAL) == F
    assert R.attributes_for(RatingKind.FIELD) == F


def test_update_follows_70_30_with_enough_evaluators():
    params = R.RatingParams(match_weight=0.3, min_evaluators_full_weight=3)
    new, w = R.update_attributes(attrs(3, 3, 3, 3, 3), attrs(5, 5, 5, 5, 5), 3, False, F, params)
    assert w == pytest.approx(0.3)
    assert new["finishing"] == pytest.approx(3 * 0.7 + 5 * 0.3)


def test_update_weight_reduced_with_few_evaluators():
    params = R.RatingParams(match_weight=0.3, min_evaluators_full_weight=3)
    _, w = R.update_attributes(attrs(3, 3, 3, 3, 3), attrs(5, 5, 5, 5, 5), 1, False, F, params)
    assert w == pytest.approx(0.1)


def test_no_evaluations_means_no_change():
    params = R.RatingParams()
    new, w = R.update_attributes(attrs(3, 3, 3, 3, 3), {}, 0, True, F, params)
    assert w == 0 and new == attrs(3, 3, 3, 3, 3)


def test_provisional_profiles_use_higher_weight():
    params = R.RatingParams(match_weight=0.3, provisional_match_weight=0.5, min_evaluators_full_weight=1)
    _, w = R.update_attributes(attrs(3, 3, 3, 3, 3), attrs(5, 5, 5, 5, 5), 2, True, F, params)
    assert w == pytest.approx(0.5)


def test_outlier_detection_flags_discrepant_vote():
    normal = attrs(3, 3, 3, 3, 3)
    evals = [normal, attrs(3, 4, 3, 3, 3), attrs(4, 3, 3, 3, 3), attrs(0, 0, 0, 0, 0)]
    flags = R.detect_outliers(evals, F, threshold=2.0)
    assert flags == [False, False, False, True]


def test_outlier_detection_needs_three_votes():
    assert R.detect_outliers([attrs(5, 5, 5, 5, 5), attrs(0, 0, 0, 0, 0)], F, 2.0) == [False, False]
