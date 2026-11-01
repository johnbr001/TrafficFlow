"""Deterministic validation and clearance enforcement for signal requests."""


class SignalSafetyController:
    GREEN_PHASES = {"north_south": 0, "east_west": 3}
    YELLOW_PHASES = {0: 1, 3: 4}
    ALL_RED_PHASES = {2, 5}
    VALID_PHASES = set((0, 1, 2, 3, 4, 5))

    def __init__(self, traci, intersection_id="J1", min_green=10, max_green=60,
                 yellow_duration=3, all_red_duration=1):
        if min_green <= 0 or max_green < min_green:
            raise ValueError("Require 0 < min_green <= max_green")
        if yellow_duration <= 0 or all_red_duration < 0:
            raise ValueError("Yellow must be positive and all-red cannot be negative")
        self.traci = traci
        self.intersection_id = intersection_id
        self.min_green = float(min_green)
        self.max_green = float(max_green)
        self.yellow_duration = float(yellow_duration)
        self.all_red_duration = float(all_red_duration)
        self.last_phase = None
        self.last_decision = {"accepted": True, "reason": "initialized", "requested": None}
        self.green_switches = 0
        self._last_green = None

    def observe(self):
        """Apply timing constraints as phases change; SUMO owns clearance timing."""
        phase = self.traci.trafficlight.getPhase(self.intersection_id)
        elapsed = self.traci.trafficlight.getSpentDuration(self.intersection_id)
        if phase != self.last_phase and phase in self.GREEN_PHASES.values():
            if self._last_green is not None and self._last_green != phase:
                self.green_switches += 1
            self._last_green = phase
        self.last_decision = {"accepted": True, "reason": "no_transition", "requested": None}
        if phase in self.GREEN_PHASES.values() and elapsed >= self.max_green:
            self.traci.trafficlight.setPhase(self.intersection_id, self.YELLOW_PHASES[phase])
            phase = self.traci.trafficlight.getPhase(self.intersection_id)
            self.last_decision = {"accepted": True, "reason": "maximum_green_clearance_started",
                                  "requested": None}
        self.last_phase = phase
        return phase

    def request_green(self, requested):
        """Validate an abstract green request and, if safe, begin yellow clearance."""
        if requested not in self.GREEN_PHASES:
            return self._deny(requested, "illegal_phase")
        phase = self.traci.trafficlight.getPhase(self.intersection_id)
        elapsed = self.traci.trafficlight.getSpentDuration(self.intersection_id)
        target_phase = self.GREEN_PHASES[requested]
        if phase not in self.GREEN_PHASES.values():
            return self._deny(requested, "clearance_in_progress")
        if phase == target_phase:
            return self._accept(requested, "already_active")
        if elapsed < self.min_green:
            return self._deny(requested, "minimum_green_not_met")
        if elapsed >= self.max_green:
            self.traci.trafficlight.setPhase(self.intersection_id, self.YELLOW_PHASES[phase])
            return self._accept(requested, "maximum_green_clearance_started")
        self.traci.trafficlight.setPhase(self.intersection_id, self.YELLOW_PHASES[phase])
        return self._accept(requested, "yellow_clearance_started")

    def keep_current(self):
        phase = self.traci.trafficlight.getPhase(self.intersection_id)
        if phase not in self.VALID_PHASES:
            return self._deny(None, "illegal_phase")
        return self._accept(None, "keep_current")

    def extend_green(self, seconds):
        if seconds not in (5, 10):
            return self._deny(seconds, "illegal_extension")
        phase = self.traci.trafficlight.getPhase(self.intersection_id)
        if phase not in self.GREEN_PHASES.values():
            return self._deny(seconds, "clearance_in_progress")
        now = self.traci.simulation.getTime()
        elapsed = self.traci.trafficlight.getSpentDuration(self.intersection_id)
        remaining = max(0.0, self.traci.trafficlight.getNextSwitch(self.intersection_id) - now)
        if elapsed + remaining + seconds > self.max_green:
            return self._deny(seconds, "maximum_green_would_be_exceeded")
        self.traci.trafficlight.setPhaseDuration(self.intersection_id, remaining + seconds)
        return self._accept(seconds, "green_extended_by_{}s".format(seconds))

    def _deny(self, requested, reason):
        self.last_decision = {"accepted": False, "reason": reason, "requested": requested}
        return dict(self.last_decision)

    def _accept(self, requested, reason):
        self.last_decision = {"accepted": True, "reason": reason, "requested": requested}
        return dict(self.last_decision)
