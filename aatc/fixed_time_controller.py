"""Fixed-time baseline; SUMO's static program owns its phase schedule."""


class FixedTimeController:
    name = "fixed_time"

    def choose(self, state):
        return None
