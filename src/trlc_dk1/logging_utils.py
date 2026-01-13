import logging


def configure_trlc_debug_logging(enabled: bool) -> None:
    """
    Enable extra debug logging for TRLC DK1 components.

    Intended to be called from device `connect()` based on a config flag such as
    `--robot.debug=true` / `--teleop.debug=true`.
    """
    if not enabled:
        return

    # Enable debug logs for this package and the DM motor control layer.
    logging.getLogger("trlc_dk1").setLevel(logging.DEBUG)
    logging.getLogger("trlc_dk1.motors.DM_Control_Python.DM_CAN").setLevel(logging.DEBUG)


