"""Stable discrete action mapping used by the PPO traffic controller."""

ACTION_NAMES = {
    0: "keep_current",
    1: "request_north_south_green",
    2: "request_east_west_green",
    3: "extend_current_green_5s",
    4: "extend_current_green_10s",
}


def apply_action(action, safety_controller):
    action = int(action)
    if action == 0:
        return safety_controller.keep_current()
    if action == 1:
        return safety_controller.request_green("north_south")
    if action == 2:
        return safety_controller.request_green("east_west")
    if action == 3:
        return safety_controller.extend_green(5)
    if action == 4:
        return safety_controller.extend_green(10)
    return {"accepted": False, "reason": "illegal_action", "requested": action}
