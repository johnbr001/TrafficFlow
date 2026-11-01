"""Queue-responsive controller that proposes only a compatible green group."""


class RuleBasedController:
    def __init__(self, queue_threshold=4, min_green=10):
        if queue_threshold < 0:
            raise ValueError("queue_threshold cannot be negative")
        if min_green <= 0:
            raise ValueError("min_green must be positive")
        self.queue_threshold = int(queue_threshold)
        self.min_green = float(min_green)

    def choose(self, state):
        phase = state["signal"]["current_phase"]
        if phase not in (0, 3):
            return None
        if state["signal"]["phase_elapsed_s"] < self.min_green:
            return None
        approaches = state["approaches"]
        ns_queue = approaches["north"]["queue_length"] + approaches["south"]["queue_length"]
        ew_queue = approaches["east"]["queue_length"] + approaches["west"]["queue_length"]
        if phase == 0 and ew_queue > ns_queue + self.queue_threshold:
            return "east_west"
        if phase == 3 and ns_queue > ew_queue + self.queue_threshold:
            return "north_south"
        return None
