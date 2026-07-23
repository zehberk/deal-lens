import ast

from pathlib import Path

from analysis.level1_models import MarketCohort as AnalysisMarketCohort
from visor_api import MarketCohort


def test_analysis_reexports_source_market_cohort_for_compatibility():
	assert AnalysisMarketCohort is MarketCohort


def test_visor_api_does_not_import_analysis_modules():
	violations = []
	for path in Path("visor_api").glob("*.py"):
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				modules = tuple(alias.name for alias in node.names)
			elif isinstance(node, ast.ImportFrom):
				modules = (node.module or "",)
			else:
				continue
			if any(module == "analysis" or module.startswith("analysis.") for module in modules):
				violations.append(f"{path}:{node.lineno}")

	assert violations == []
