from analysis.scoring import adjust_deal_for_risk, supports_deal_upgrade


def test_zero_risk_upgrades_price_rating_one_bin():
	narrative = []

	assert adjust_deal_for_risk(
		"Good", 0, narrative, favorable_evidence=True
	) == "Great"
	assert "upgraded to Great" in narrative[-1]
	assert adjust_deal_for_risk("Poor", 0, favorable_evidence=True) == "Fair"


def test_zero_risk_cannot_upgrade_beyond_great():
	assert adjust_deal_for_risk("Great", 0, favorable_evidence=True) == "Great"


def test_neutral_zero_risk_does_not_upgrade_without_favorable_evidence():
	assert adjust_deal_for_risk("Good", 0) == "Good"


def test_low_nonzero_risk_remains_neutral():
	assert adjust_deal_for_risk("Good", 1) == "Good"
	assert adjust_deal_for_risk("Good", 2) == "Good"


def test_existing_risk_downgrades_are_preserved():
	assert adjust_deal_for_risk("Good", 3) == "Fair"
	assert adjust_deal_for_risk("Good", 5) == "Poor"
	assert adjust_deal_for_risk("Good", 7) == "Bad"


def test_suspicious_price_handling_is_not_upgraded():
	assert adjust_deal_for_risk("Suspicious", 0) == "Suspicious"


def test_deal_upgrade_requires_substantial_favorable_evidence():
	assert supports_deal_upgrade(-2.5) is True
	assert supports_deal_upgrade(-2.0) is True
	assert supports_deal_upgrade(-1.0) is False
	assert supports_deal_upgrade(0.0) is False
