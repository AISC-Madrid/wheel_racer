"""Tests for lap timing and the honesty rules around it.

Most of these are about the ways a booth visitor might get a time they did not
drive — cutting the infield, starting the clock mid-circuit, or wobbling on the
start line — because those are what a leaderboard has to survive.
"""

import pytest

from wheel_racer.laptimer import LapTimer, gate_progresses

DT = 1.0 / 60.0
STEP = 0.002  # progress per frame; 500 frames to the lap, near the real rate
LAP_SECONDS = DT / STEP  # how long a clean lap takes in this simulation


class Sim:
    """Drives a car around the circuit and feeds the timer, frame by frame."""

    def __init__(self, timer: LapTimer, step: float = STEP) -> None:
        self.timer = timer
        self.step = step
        self.now = 0.0
        self.progress = 0.0
        self.laps: list[float] = []

    def green_light(self) -> None:
        """Start the clock with the car sitting on the line."""
        self.timer.start(self.now)
        self.timer.update(self.progress, self.now)

    def drive(self, distance: float) -> list[float]:
        """Drive some fraction of a lap, forwards or backwards, one frame at a
        time. Returns any lap times completed along the way."""
        completed = []
        direction = 1.0 if distance >= 0 else -1.0
        for _ in range(round(abs(distance) / self.step)):
            self.progress = (self.progress + direction * self.step) % 1.0
            self.now += DT
            lap = self.timer.update(self.progress, self.now)
            if lap is not None:
                completed.append(lap)
        self.laps.extend(completed)
        return completed

    def jump_to(self, progress: float) -> float | None:
        """Teleport straight to a point on the circuit.

        This is what cutting across the infield looks like to the timer: the
        nearest centreline point leaps to the far side of the loop.
        """
        self.progress = progress
        self.now += DT
        return self.timer.update(progress, self.now)


@pytest.fixture
def timer() -> LapTimer:
    return LapTimer(gates=3, max_progress_step=0.05)


@pytest.fixture
def sim(timer) -> Sim:
    run = Sim(timer)
    run.green_light()
    return run


class TestCleanLap:
    def test_a_full_lap_returns_a_time(self, sim):
        assert sim.drive(1.0) == [pytest.approx(LAP_SECONDS)]

    def test_no_time_before_the_line(self, sim):
        assert sim.drive(0.99) == []

    def test_every_gate_is_cleared_on_a_clean_lap(self, sim, timer):
        sim.drive(0.99)
        assert timer.gates_cleared == timer.gates

    def test_the_clock_stops_at_the_line(self, sim, timer):
        sim.drive(1.0)
        assert not timer.is_running

    def test_best_and_last_are_recorded(self, sim, timer):
        sim.drive(1.0)
        assert timer.last == pytest.approx(LAP_SECONDS)
        assert timer.best == pytest.approx(LAP_SECONDS)

    def test_best_keeps_the_quickest_of_several_laps(self, sim, timer):
        sim.drive(1.0)
        slow = timer.last

        sim.timer.start(sim.now)
        sim.step = STEP * 2  # same distance, half the frames, so half the time
        sim.drive(1.0)

        assert timer.best == pytest.approx(slow / 2)
        assert timer.best < slow


class TestInfieldShortcut:
    def test_cutting_across_the_middle_does_not_score(self, sim):
        """Drive a tenth of a lap, cut across to two thirds, finish from there."""
        sim.drive(0.1)
        sim.jump_to(0.6)
        assert sim.drive(0.4) == []

    def test_a_shortcut_credits_no_gate(self, sim, timer):
        sim.drive(0.1)
        sim.jump_to(0.6)
        assert timer.gates_cleared == 0

    def test_the_jump_frame_itself_scores_nothing(self, sim):
        sim.drive(0.1)
        assert sim.jump_to(0.6) is None

    def test_the_clock_restarts_after_an_invalid_crossing(self, sim, timer):
        """A cheated lap is not punished, just not counted — the player gets a
        fresh clock rather than a dead one."""
        sim.drive(0.1)
        sim.jump_to(0.6)
        sim.drive(0.4)
        assert timer.is_running
        assert timer.current_time(sim.now) == pytest.approx(0.0, abs=DT)

    def test_a_clean_lap_after_a_cheated_one_scores_normally(self, sim):
        sim.drive(0.1)
        sim.jump_to(0.6)
        sim.drive(0.4)
        assert sim.drive(1.0) == [pytest.approx(LAP_SECONDS)]


class TestStartLineAbuse:
    def test_wobbling_over_the_line_cannot_fake_a_lap(self, timer):
        """Landmark jitter puts the car either side of the line repeatedly."""
        timer.start(0.0)
        now = 0.0
        for i in range(50):
            now += DT
            assert timer.update(0.999 if i % 2 else 0.001, now) is None

    def test_reversing_over_the_line_does_not_finish_a_lap(self, sim):
        sim.drive(0.9)
        assert sim.drive(-0.9) == []

    def test_a_lap_started_mid_circuit_does_not_count(self, timer):
        """The clock only ever starts on the line, but guard it anyway."""
        run = Sim(timer)
        run.progress = 0.5
        run.green_light()
        assert run.drive(0.5) == []

    def test_driving_backwards_credits_no_gate(self, sim, timer):
        sim.drive(-0.9)
        assert timer.gates_cleared == 0


class TestGateOrder:
    def test_gates_are_credited_one_at_a_time(self, sim, timer):
        sim.drive(0.26)
        assert timer.gates_cleared == 1
        sim.drive(0.25)
        assert timer.gates_cleared == 2

    def test_a_gate_stays_credited_after_backtracking(self, sim, timer):
        """Running wide and rejoining behind a gate must not un-clear it."""
        sim.drive(0.3)
        sim.drive(-0.1)
        assert timer.gates_cleared == 1

    def test_an_excursion_and_recovery_still_scores(self, sim):
        sim.drive(0.3)
        sim.drive(-0.1)
        assert len(sim.drive(0.8)) == 1

    def test_a_single_checkpoint_lap_needs_no_gates(self):
        timer = LapTimer(gates=0)
        run = Sim(timer)
        run.green_light()
        assert run.drive(1.0) == [pytest.approx(LAP_SECONDS)]


class TestGatePositions:
    """`gate_progresses` is the one place gate positions are decided, so that
    the ticks drawn on the track and the gates a lap is judged against cannot
    disagree. A lap rejected for missing a gate that was never drawn would be
    impossible for a player to make sense of."""

    def test_the_count_is_what_was_asked_for(self):
        """The number of gates is the number of ticks on screen. The start line
        is not one of them — crossing it is what ends a lap."""
        assert len(gate_progresses(5)) == 5

    def test_gates_are_evenly_spaced_around_the_lap(self):
        assert gate_progresses(3) == [pytest.approx(0.25), pytest.approx(0.5),
                                      pytest.approx(0.75)]

    def test_no_gate_sits_on_the_start_line(self):
        """One at progress 0 would be cleared for free the instant a lap began,
        and one at 1.0 is the line again."""
        for gates in range(0, 9):
            assert all(0.0 < progress < 1.0 for progress in gate_progresses(gates))

    def test_gates_are_in_order(self):
        assert gate_progresses(7) == sorted(gate_progresses(7))

    def test_no_gates_is_a_valid_circuit(self):
        assert gate_progresses(0) == []

    def test_the_timer_uses_exactly_these_positions(self, timer):
        """The guard against the renderer and the validator drifting apart."""
        assert timer._thresholds == gate_progresses(timer.gates)


class TestClock:
    def test_current_time_is_none_before_the_start(self, timer):
        assert timer.current_time(10.0) is None

    def test_current_time_tracks_the_lap_in_progress(self, sim, timer):
        sim.drive(0.5)
        assert timer.current_time(sim.now) == pytest.approx(LAP_SECONDS / 2, abs=DT)

    def test_current_time_is_none_once_the_lap_is_done(self, sim, timer):
        sim.drive(1.0)
        assert timer.current_time(sim.now) is None

    def test_not_running_before_the_green_light(self, timer):
        assert not timer.is_running

    def test_updates_before_the_start_are_ignored(self, timer):
        assert timer.update(0.5, 1.0) is None
        assert timer.update(0.502, 1.1) is None


class TestReset:
    def test_reset_clears_the_best_time(self, sim, timer):
        sim.drive(1.0)
        timer.reset()
        assert timer.best is None
        assert timer.last is None

    def test_reset_stops_the_clock(self, sim, timer):
        sim.drive(0.5)
        timer.reset()
        assert not timer.is_running


class TestValidation:
    def test_rejects_a_negative_number_of_gates(self):
        with pytest.raises(ValueError):
            LapTimer(gates=-1)

    @pytest.mark.parametrize("step", [0.0, -0.1, 0.5, 1.0])
    def test_rejects_an_implausible_step_limit(self, step):
        with pytest.raises(ValueError):
            LapTimer(max_progress_step=step)

    def test_a_step_at_the_limit_is_still_driving(self, timer):
        """The limit is a cheat threshold, not a framerate budget — a bad hitch
        must not invalidate an honest lap."""
        timer.start(0.0)
        timer.update(0.10, 0.0)
        timer.update(0.15, DT)  # exactly max_progress_step
        assert timer.gates_cleared == 0  # no gate between 0.10 and 0.15
        timer.update(0.20, 2 * DT)
        timer.update(0.25, 3 * DT)
        assert timer.gates_cleared == 1
