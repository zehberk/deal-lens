from analysis import workflow


async def test_level2_collects_kbb_before_dealer_and_document_data(monkeypatch):
	events = []
	listing = {"id": "listing-1", "vin": "VIN1", "title": "Test vehicle"}
	ctx = workflow.build_analysis_context(
		{"vehicle": {"make": "Test", "model": "Vehicle"}}
	)

	monkeypatch.setattr(workflow, "build_analysis_context", lambda metadata: ctx)
	monkeypatch.setattr(workflow, "normalize_listing", lambda value: value)
	monkeypatch.setattr(workflow, "populate_cache", lambda value: events.append("cache"))

	async def populate_variants(_ctx, _listings):
		events.append("variants")

	async def populate_pricing(_ctx, _listings):
		events.append("kbb")

	async def download(_listings, _filename):
		events.append("dealer_and_documents")

	monkeypatch.setattr(workflow, "populate_variants", populate_variants)
	monkeypatch.setattr(workflow, "populate_pricing_data", populate_pricing)
	monkeypatch.setattr(workflow, "get_vehicle_dir", lambda value: None)
	monkeypatch.setattr(workflow, "download_files", download)
	monkeypatch.setattr(
		workflow,
		"populate_filtered_listings",
		lambda *_args, **_kwargs: events.append("filter"),
	)
	monkeypatch.setattr(
		workflow, "check_missing_docs", lambda _listings: events.append("reports")
	)

	result = await workflow.prepare_level2_analysis(
		{"vehicle": {"make": "Test", "model": "Vehicle"}},
		[listing],
		"listings.json",
	)

	assert result is ctx
	assert events == [
		"cache",
		"variants",
		"kbb",
		"dealer_and_documents",
		"filter",
		"reports",
	]
