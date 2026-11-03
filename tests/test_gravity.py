import math

import pytest

from market_microstructure import gravity, liquidation_imbalance, prepare_exposure


def test_hand_calculated_gravity_and_lgi():
    down = gravity([(200, 10)], 10)
    up = gravity([(300, 25)], 10)
    assert down == pytest.approx(73.57588823428847)
    assert up == pytest.approx(24.62549958716964)
    assert liquidation_imbalance(up, down) == pytest.approx(-0.49846941813201256)


def test_empty_symmetric_and_single_sided_lgi():
    assert gravity([], 10) == 0
    assert liquidation_imbalance(0, 0) == 0
    assert liquidation_imbalance(200, 200) == 0
    assert liquidation_imbalance(200, 0) == pytest.approx(1)
    assert liquidation_imbalance(0, 200) == pytest.approx(-1)
    assert liquidation_imbalance(2, 0, epsilon=1) == pytest.approx(2 / 3)


def test_distance_decay_no_cutoff_and_tau_sensitivity():
    assert gravity([(200, 0)], 10) == 200
    assert 0 < gravity([(200, 1000)], 100) < gravity([(200, 10)], 100)
    assert gravity([(200, 25)], 50) > gravity([(200, 25)], 10)
    assert gravity([(0, 5)], 10) == 0


def test_generator_order_and_scale_properties():
    rows = [(200, 10), (300, 25)]
    assert gravity(iter(reversed(rows)), 10) == gravity(rows, 10)
    assert gravity([(400, 10), (600, 25)], 10) == 2 * gravity(rows, 10)
    assert liquidation_imbalance(30, 10) == -liquidation_imbalance(10, 30)
    assert gravity([("200", "10")], "10") == gravity([(200, 10)], 10)


@pytest.mark.parametrize("bad", [0, -1, None, True, "bad", math.nan, math.inf, 10**400])
def test_invalid_tau(bad):
    with pytest.raises(ValueError):
        gravity([], bad)


@pytest.mark.parametrize("row", [(-1, 10), (1, -1), (None, 10), (True, 10),
                                      (1, math.nan), (math.inf, 1), (1, 10**400),
                                      (1,), (1, 2, 3), None, "12", {"1": 1, "2": 2}])
def test_invalid_exposure(row):
    with pytest.raises(ValueError):
        gravity([row], 10)


@pytest.mark.parametrize("kwargs", [{"up": -1}, {"down": -1}, {"up": None},
    {"up": True}, {"down": math.nan}, {"down": math.inf}, {"epsilon": 0},
    {"epsilon": -1}, {"epsilon": True}, {"epsilon": math.nan}])
def test_invalid_lgi_inputs(kwargs):
    with pytest.raises(ValueError):
        liquidation_imbalance(**{"up": 1, "down": 1, **kwargs})


def test_overflow_is_explicit_and_extreme_decay_can_underflow():
    with pytest.raises(ValueError):
        gravity([(1e308, 0), (1e308, 0)], 10)
    with pytest.raises(ValueError):
        liquidation_imbalance(1e308, 1e308)
    assert gravity([(1, 1e308)], 1e-308) == 0


@pytest.mark.parametrize("size,threshold,side,notional,distance", [
    ("2", "99.9", "lower", 200, 10), ("-3", "100.25", "upper", 300, 25),
])
def test_prepare_one_position(size, threshold, side, notional, distance):
    result = prepare_exposure(size, threshold, "100")
    assert result["status"] == result["reason"] == "included"
    assert result["side"] == side
    assert result["notional"] == notional
    assert result["distance_bps"] == pytest.approx(distance)


def test_missing_threshold_keeps_exposure_but_never_invents_distance():
    result = prepare_exposure(2, None, 100)
    assert result == {"status": "excluded", "reason": "missing_liquidation_price",
                      "side": "lower", "notional": 200.0,
                      "liquidation_price": None, "distance_bps": None}


@pytest.mark.parametrize("threshold", [0, -1, "bad", "NaN", "Infinity", True, 10**400])
def test_invalid_threshold_is_excluded_with_reason(threshold):
    result = prepare_exposure(2, threshold, 100)
    assert result["status"] == "excluded"
    assert result["reason"] == "invalid_liquidation_price"
    assert result["distance_bps"] is None


@pytest.mark.parametrize("size,threshold,reason", [
    (2, 100, "at_reference_price"), (2, 101, "wrong_side_of_reference"),
    (-2, 99, "wrong_side_of_reference"), (0, None, "zero_position"),
])
def test_exclusion_reasons(size, threshold, reason):
    result = prepare_exposure(size, threshold, 100)
    assert result["status"] == "excluded" and result["reason"] == reason
    assert result["distance_bps"] is None


@pytest.mark.parametrize("size,reference", [(None, 100), (math.nan, 100), (True, 100),
    (1, None), (1, 0), (1, -1), (1, math.inf), (1, True), (1e308, 100)])
def test_unknown_position_size_or_reference_is_an_error(size, reference):
    with pytest.raises(ValueError):
        prepare_exposure(size, 99, reference)


def test_distance_overflow_is_an_error():
    with pytest.raises(ValueError):
        prepare_exposure(-1, 1e308, 1e-308)
