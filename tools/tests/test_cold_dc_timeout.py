"""The first range from a data centre that this session has not used needs a new connection and a key exchange: it must not be cut off at the 5.5 s a warm range gets."""
from telegram_archive.playback.stream_server import range_timeout_for


def test_a_cold_data_centre_gets_the_long_allowance_and_a_warm_one_the_short_limit():
    assert range_timeout_for(5.5, cold=True, preview=False, cold_limit=30.0) == 30.0
    assert range_timeout_for(5.5, cold=False, preview=False, cold_limit=30.0) == 5.5


def test_a_preview_never_gets_it_and_a_longer_normal_limit_is_kept():
    assert range_timeout_for(6.0, cold=True, preview=True, cold_limit=30.0) == 6.0
    assert range_timeout_for(45.0, cold=True, preview=False, cold_limit=30.0) == 45.0
