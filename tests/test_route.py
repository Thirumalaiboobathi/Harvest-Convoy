from harvest_convoy.scheduling.route import RoutePoint, haversine_km, order_route


def test_haversine_zero_distance_for_same_point() -> None:
    assert haversine_km(10.0, 77.5, 10.0, 77.5) == 0.0


def test_haversine_known_distance_theni_to_madurai() -> None:
    # Theni (~10.0104N, 77.4768E) to Madurai (~9.9252N, 78.1198E), straight-line
    # (not road) distance -- sanity bound, not a precise citation.
    d = haversine_km(10.0104, 77.4768, 9.9252, 78.1198)
    assert 65.0 < d < 80.0


def test_order_route_visits_nearest_point_first() -> None:
    points = [
        RoutePoint("far", 10.10, 77.60),
        RoutePoint("near", 9.870, 77.455),
        RoutePoint("mid", 9.950, 77.500),
    ]
    order = order_route(points, start_lat=9.865, start_lon=77.45389)
    assert order == ["near", "mid", "far"]


def test_order_route_is_deterministic_tie_break_by_plot_id() -> None:
    # Two points equidistant from the start (mirrored across it).
    points = [
        RoutePoint("z_plot", 9.865, 77.46389),  # +0.01 lon
        RoutePoint("a_plot", 9.865, 77.44389),  # -0.01 lon
    ]
    order = order_route(points, start_lat=9.865, start_lon=77.45389)
    assert order == ["a_plot", "z_plot"]


def test_order_route_empty_input() -> None:
    assert order_route([], start_lat=9.865, start_lon=77.45389) == []
