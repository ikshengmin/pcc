import asyncio
import time


def main() -> None:
    loop = asyncio.new_event_loop()
    print("LOOP_CLOCK_ENTER")
    assert loop.time() >= 0.0
    loop.close()
    for clock in (time.time, time.monotonic, time.perf_counter):
        first = clock()
        second = clock()
        assert isinstance(first, float)
        assert second >= first
    formatter = time.strftime
    assert formatter("owned-clock") == "owned-clock"
    sleeper = time.sleep
    assert sleeper(0) is None
    try:
        sleeper(-1)
    except ValueError as error:
        assert str(error) == "sleep length must be non-negative"
    else:
        raise AssertionError("negative delay was accepted")
    print("OWNED_TIME_OK")


main()
