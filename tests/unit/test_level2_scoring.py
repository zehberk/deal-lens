from analysis.scoring import (
	adjust_deal_for_evidence,
	adjust_deal_for_risk,
	deal_score_from_position,
	deal_strength_within_bin,
	get_structure_score,
)
from utils.models import StructuralStatus


SPORT_CUTOFFS = (26153, 26887, 27621, 28355)


def test_favorable_evidence_crosses_nearby_good_to_great_boundary():
	narrative = []

	result = adjust_deal_for_evidence(
		"Good", -2.5, 26194, 433, (26187, 27053, 27919, 28785), narrative
	)

	assert result == "Great"
	assert "from Good to Great" in narrative[-1]


def test_adverse_evidence_crosses_nearby_good_to_fair_boundary():
	result = adjust_deal_for_evidence(
		"Good", 1.019789174705252, 26816, 367, SPORT_CUTOFFS
	)

	assert result == "Fair"


def test_marginal_warranty_does_not_move_centered_fair_price():
	result = adjust_deal_for_evidence(
		"Fair", -1.0, 26142, 1100, (23130, 25330, 27530, 29730)
	)

	assert result == "Fair"


def test_strong_evidence_barely_crosses_poor_to_fair_boundary():
	result = adjust_deal_for_evidence(
		"Poor", -2.5, 28170, 367, SPORT_CUTOFFS
	)

	assert result == "Fair"


def test_favorable_evidence_does_not_rescue_price_far_into_bad_bin():
	result = adjust_deal_for_evidence(
		"Bad", -2.0, 30241, 367, SPORT_CUTOFFS
	)

	assert result == "Bad"


def test_existing_legacy_risk_downgrades_are_preserved():
	assert adjust_deal_for_risk("Good", 3) == "Fair"
	assert adjust_deal_for_risk("Good", 5) == "Poor"
	assert adjust_deal_for_risk("Good", 7) == "Bad"


def test_suspicious_price_handling_remains_ambiguous():
	assert adjust_deal_for_evidence(
		"Suspicious", -2.5, 20000, 500, SPORT_CUTOFFS
	) == "Suspicious"


def test_possible_structure_wording_does_not_claim_confirmed_damage():
	narrative = []

	get_structure_score(StructuralStatus.POSSIBLE, 1.0, narrative)

	assert "does not confirm structural damage" in narrative[-1]


def test_deal_strength_shows_position_within_assigned_bin():
	assert deal_strength_within_bin("Fair", 27620, SPORT_CUTOFFS) < 1
	assert deal_strength_within_bin("Fair", 27254, SPORT_CUTOFFS) == 50
	assert deal_strength_within_bin("Bad", 30241, SPORT_CUTOFFS) == 0
	assert deal_strength_within_bin("Suspicious", 20000, SPORT_CUTOFFS) is None


def test_deal_score_uses_the_full_price_scale():
	assert deal_score_from_position(9, "Great") == 91
	assert deal_score_from_position(42, "Fair") == 58
	assert deal_score_from_position(88, "Bad") == 12
	assert deal_score_from_position(50, "Suspicious") is None
