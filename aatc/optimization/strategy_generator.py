"""Generate a repeatable grid of fixed-time green splits."""


def generate_strategies(green_values=(20, 30, 40, 50), yellow=3, all_red=1,
                        min_green=10, max_green=60):
    values = sorted(set(int(value) for value in green_values))
    if not values or any(value < min_green or value > max_green for value in values):
        raise ValueError("green_values must be within configured min/max green")
    if yellow <= 0 or all_red <= 0:
        raise ValueError("yellow and all-red durations must be positive")
    strategies = []
    for ns_green in values:
        for ew_green in values:
            strategies.append({
                "strategy_id": "NS{}_EW{}_Y{}_AR{}".format(ns_green, ew_green, yellow, all_red),
                "green_ns_s": ns_green,
                "green_ew_s": ew_green,
                "yellow_s": int(yellow),
                "all_red_s": int(all_red),
            })
    return strategies
