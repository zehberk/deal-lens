import pytest

from analysis.scoring import (
	deal_score_from_position,
	calculate_deal_score,
	deal_rating_from_score,
	risk_penalty,
	get_structure_score,
	format_deal_score_narrative,
	score_warranty_status,
)
from utils.models import CarfaxData, StructuralStatus


def test_favorable_evidence_crosses_nearby_good_to_great_boundary():
	score = calculate_deal_score(80, 0, 2.5)

	assert deal_rating_from_score(score) == "Great"


def test_adverse_evidence_crosses_nearby_good_to_fair_boundary():
	score = calculate_deal_score(62, 1.019789174705252)

	assert deal_rating_from_score(score) == "Fair"


def test_marginal_warranty_does_not_move_centered_fair_price():
	score = calculate_deal_score(53, 0, 1.0)

	assert deal_rating_from_score(score) == "Fair"


def test_strong_evidence_barely_crosses_poor_to_fair_boundary():
	score = calculate_deal_score(25, 0, 2.5)

	assert deal_rating_from_score(score) == "Fair"


def test_favorable_evidence_does_not_rescue_price_far_into_bad_bin():
	score = calculate_deal_score(0, 0, 2.0)

	assert deal_rating_from_score(score) == "Bad"


def test_possible_structure_wording_does_not_claim_confirmed_damage():
	narrative = []

	get_structure_score(StructuralStatus.POSSIBLE, 1.0, narrative)

	assert "does not confirm structural damage" in narrative[-1]


def test_deal_score_uses_the_full_price_scale():
	assert deal_score_from_position(9) == 91
	assert deal_score_from_position(42) == 58
	assert deal_score_from_position(88) == 12
	assert deal_score_from_position(-10) == 100


def test_risk_penalty_accelerates_as_risk_increases():
	low_step = risk_penalty(2) - risk_penalty(1)
	moderate_step = risk_penalty(5) - risk_penalty(4)
	high_step = risk_penalty(9) - risk_penalty(8)

	assert 0 < low_step < moderate_step < high_step
	assert risk_penalty(10) == 100


def test_adverse_mileage_risk_subtracts_from_deal_score():
	assert calculate_deal_score(60, 4) < calculate_deal_score(60, 0)


def test_meaningful_risk_negates_favorable_evidence():
	assert calculate_deal_score(60, 4, 3) == calculate_deal_score(60, 4, 0)
	assert calculate_deal_score(60, 3, 3) > calculate_deal_score(60, 3, 0)


def test_zero_score_requires_bottom_price_and_maximum_risk():
	assert calculate_deal_score(0, 0) == 10
	assert calculate_deal_score(0, 4) > 0
	assert calculate_deal_score(0, 10) == 0
	assert calculate_deal_score(100, 10) > 0


def test_rating_is_derived_from_continuous_score():
	assert deal_rating_from_score(80) == "Great"
	assert deal_rating_from_score(60) == "Good"
	assert deal_rating_from_score(40) == "Fair"
	assert deal_rating_from_score(20) == "Poor"
	assert deal_rating_from_score(19.9) == "Bad"


def test_zero_score_components_use_plain_language():
	narrative = format_deal_score_narrative(50, 50, 0, 0)

	assert "no risk was identified" in narrative
	assert "there is no favorable evidence" in narrative
	assert "0.0/10" not in narrative
	assert "0 points" not in narrative
	assert "less than 1 point" in format_deal_score_narrative(50, 50, 0, 0.4)
	assert "adds 1 point." in format_deal_score_narrative(50, 50, 0, 1.2)


def test_warranty_bonus_uses_limiting_mileage_at_typical_use():
	carfax = CarfaxData(
		summary={},
		accident_damage={},
		reliability_section={},
		additional_history={
			"Structural Damage": "No structural damage reported to CARFAX.",
			"Basic Warranty": "Original warranty estimated to have 5 months or 687 miles remaining.",
		},
		ownership_history={},
		detailed_history=[],
	)
	narrative = []

	result = score_warranty_status(carfax, {"coverages": []}, narrative)

	assert result == pytest.approx(-0.0916, abs=0.001)
	assert "about 0.5 months" in narrative[-1]
