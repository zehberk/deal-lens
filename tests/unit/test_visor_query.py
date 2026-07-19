from visor_api import LISTING_EXPANSIONS, LISTING_FIELDS, VisorListingQuery


def test_legacy_url_options_map_to_public_api_parameters():
	query = VisorListingQuery.from_url(
		"https://visor.vin/search/listings?make=Toyota&model=Camry"
		"&trim=XSE,LE&year=2025,2024&car_type=Used,CPO"
		"&price_min=20000&price_max=40000&miles_min=1000&miles_max=50000"
		"&location=80202&radius=100&sort=cheapest"
	)

	assert query.filters == {
		"make": ("Toyota",),
		"model": ("Camry",),
		"trim": ("LE", "XSE"),
		"year": ("2024", "2025"),
		"inventory_type": ("certified", "used"),
		"min_price": "20000",
		"max_price": "40000",
		"min_mileage": "1000",
		"max_mileage": "50000",
		"postal_code": "80202",
		"radius": "100",
		"sort": "price",
	}
	assert query.unsupported == {}


def test_named_location_and_unknown_browser_options_are_reported():
	query = VisorListingQuery.from_url(
		"https://visor.vin/search/listings?location=Denver%2C+CO&agnostic=false"
	)

	assert query.filters == {}
	assert query.unsupported == {"location": "Denver, CO", "agnostic": "false"}


def test_projection_and_fingerprint_are_normalized():
	first = VisorListingQuery.from_url(
		"https://visor.vin/search/listings?trim=XSE,LE&year=2025,2024"
	)
	second = VisorListingQuery.from_url(
		"https://visor.vin/search/listings?year=2024,2025&trim=LE,XSE"
	)

	assert first.api_params()["fields"] == LISTING_FIELDS
	assert first.api_params()["include"] == LISTING_EXPANSIONS
	assert first.fingerprint(50) == second.fingerprint(50)


def test_current_browser_geo_parameters_map_to_postal_radius_query():
	query = VisorListingQuery.from_url(
		"https://visor.vin/search/listings?make=Subaru&model=Forester"
		"&year=%222024%22&geo_origin_kind=postal_code"
		"&geo_origin_value=%2280013%22&geo_mode=radius"
		"&geo_distance_value=%22100%22&geo_distance_unit=mi"
	)

	assert query.filters == {
		"make": ("Subaru",),
		"model": ("Forester",),
		"year": ("2024",),
		"postal_code": "80013",
		"radius": "100",
	}
	assert query.unsupported == {}


def test_unsupported_geo_shape_is_reported_instead_of_silently_invented():
	query = VisorListingQuery.from_url(
		"https://visor.vin/search/listings?geo_origin_kind=city"
		"&geo_origin_value=Denver&geo_mode=radius"
		"&geo_distance_value=100&geo_distance_unit=km"
	)

	assert query.filters == {}
	assert query.unsupported == {
		"geo_origin_kind": "city",
		"geo_origin_value": "Denver",
		"geo_mode": "radius",
		"geo_distance_value": "100",
		"geo_distance_unit": "km",
	}
