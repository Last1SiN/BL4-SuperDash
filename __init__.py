from __future__ import annotations

import math
import time
from enum import IntEnum
from typing import Any

import unrealsdk
from mods_base import (
    EInputEvent,
    MODS_DIR,
    SliderOption,
    build_mod,
    get_pc,
    hook,
    keybind,
)
from unrealsdk.hooks import Type

VERSION = "1.2.2"
LOG = MODS_DIR / "BL4_SuperDash.log"

class Phase(IntEnum):
    IDLE = 0
    NEUTRALIZE_MOVE = 1
    WAIT_DASH_START = 2
    HOLD_JUMP = 3
    WAIT_RELEASE = 4
    WAIT_LANDING = 5
    SPRINT_SETTLE = 6
    FINISH = 7
    DASH_ACTIVE = 8
    DIAGONAL_DASH_ACTIVE = 10


class SequenceKind(IntEnum):
    NONE = 0
    SUPER_DASH = 1
    DASH = 2

_phase = Phase.IDLE
_sequence_kind = SequenceKind.NONE
_c = None
_start_ns = 0
_target_ns = 0
_initial_last_dash_time = None
_anim_hook = None
_resume_sprint = False
_saw_airborne = False
_landing_deadline_ns = 0
_neutral_frame_count = 0
_dash_min_end_ns = 0
_diagonal_emulation = False
_diagonal_start_ns = 0
_diagonal_duration_s = 0.33
_diagonal_base_speed = 2500.0
_diagonal_curve = ()
_pre_request_speed = 0.0
_forward_gate_neutral = False

# (mapping WrappedStruct, original bShouldBeIgnored)
_suppressed_mappings = []

_desired_x = 0.0
_desired_y = 0.0
_native_direction = 0
_dash_speed = 0.0

# Native SetWantsToDash enum, verified in Borderlands 4.
DIR_FORWARD = 0
DIR_LEFT = 1
DIR_BACK = 2
DIR_RIGHT = 3

GROUND_DASH_ACCEPT_NS = 45_000_000


def log_error(msg: str) -> None:
    """Write exceptional/abort diagnostics to file without console output."""
    try:
        with LOG.open("a", encoding="utf-8", errors="replace") as f:
            f.write(f"[BL4 Super Dash {VERSION}] {msg}\n")
    except Exception:
        pass


def get_pc_safe():
    try:
        return get_pc(possibly_loading=True)
    except Exception:
        return None


def get_char():
    p = get_pc_safe()
    if p is None:
        return None

    try:
        c = p.OakCharacter
        text = repr(c)
    except Exception:
        return None

    if "/FrontEnd/" in text or "Entry_P" in text:
        return None
    return c


neutral_frames = SliderOption(
    identifier="neutral_frames",
    value=1,
    min_value=1,
    max_value=4,
    step=1,
    is_integer=True,
    display_name="Movement Neutral Frames",
    description=(
        "Frames for which movement input is internally suppressed before "
        "re-arming Dash. Default: 1."
    ),
)

jump_hold_ms = SliderOption(
    identifier="jump_hold_ms",
    value=25,
    min_value=1,
    max_value=60,
    step=1,
    is_integer=True,
    display_name="Jump Hold (ms)",
    description="Super Dash only. How long Jump remains held. Default: 25 ms.",
)

release_delay_ms = SliderOption(
    identifier="release_delay_ms",
    value=15,
    min_value=0,
    max_value=40,
    step=1,
    is_integer=True,
    display_name="Jump Release -> Dash Release (ms)",
    description=(
        "Super Dash only. How long Dash remains requested after Jump release. "
        "Default: 15 ms."
    ),
)

dash_timeout_ms = SliderOption(
    identifier="dash_timeout_ms",
    value=300,
    min_value=50,
    max_value=1000,
    step=10,
    is_integer=True,
    display_name="Dash Start Timeout (ms)",
    description="Abort if native Dash does not start after re-arm. Default: 300 ms.",
)


def _mapping_action(mapping) -> str:
    try:
        return repr(mapping.Action)
    except Exception:
        return ""


def _find_move_mappings():
    p = get_pc_safe()
    if p is None:
        return []

    try:
        mappings = list(p.PlayerInput.EnhancedActionMappings)
    except Exception:
        return []

    return [
        mapping
        for mapping in mappings
        if (
            "Action_Move.Action_Move" in _mapping_action(mapping)
            or "Action_DashDirection.Action_DashDirection" in _mapping_action(mapping)
        )
    ]


def _set_mapping_ignored(mapping, ignored: bool) -> bool:
    try:
        mapping.bShouldBeIgnored = bool(ignored)
        return bool(mapping.bShouldBeIgnored) == bool(ignored)
    except Exception:
        return False


def _restore_move_mappings() -> None:
    global _suppressed_mappings

    for mapping, original in _suppressed_mappings:
        _set_mapping_ignored(mapping, original)
    _suppressed_mappings = []


def _suppress_move_mappings() -> bool:
    global _suppressed_mappings

    mappings = _find_move_mappings()
    if not mappings:
        log_error("MOVEMENT SUPPRESS ERROR: no movement mappings found")
        return False

    _suppressed_mappings = []
    for mapping in mappings:
        try:
            original = bool(mapping.bShouldBeIgnored)
        except Exception:
            original = False

        _suppressed_mappings.append((mapping, original))
        if not _set_mapping_ignored(mapping, True):
            log_error("MOVEMENT SUPPRESS ERROR: a mapping could not be suppressed")
            _restore_move_mappings()
            return False

    return True


def _clear_pending_move_input(c) -> None:
    try:
        c.ConsumeMovementInputVector()
    except Exception:
        pass

    try:
        c.CharacterMovement.ConsumeInputVector()
    except Exception:
        pass


def _movement_mode_name(c) -> str:
    try:
        return repr(c.CharacterMovement.MovementMode)
    except Exception:
        return ""


def _capture_direction(c, *, allow_standstill: bool):
    """Return world-space movement direction and nearest native Dash direction."""
    p = get_pc_safe()
    if p is None:
        return None

    try:
        yaw = float(p.GetControlRotation().Yaw)
    except Exception:
        yaw = 0.0

    yr = math.radians(yaw)
    cam_fx, cam_fy = math.cos(yr), math.sin(yr)
    cam_rx, cam_ry = -math.sin(yr), math.cos(yr)

    vx = vy = 0.0
    speed = 0.0
    try:
        vel = c.GetVelocity()
        vx, vy = float(vel.X), float(vel.Y)
        speed = math.hypot(vx, vy)
    except Exception:
        pass

    ix = iy = 0.0
    try:
        iv = c.GetLastMovementInputVector()
        ix, iy = float(iv.X), float(iv.Y)
    except Exception:
        pass

    input_mag = math.hypot(ix, iy)
    if input_mag > 0.001:
        dx, dy = ix / input_mag, iy / input_mag
    elif speed > 5.0:
        dx, dy = vx / speed, vy / speed
    elif allow_standstill:
        dx, dy = cam_fx, cam_fy
    else:
        return None

    local_forward = dx * cam_fx + dy * cam_fy
    local_right = dx * cam_rx + dy * cam_ry

    if abs(local_forward) >= abs(local_right):
        native = DIR_FORWARD if local_forward >= 0.0 else DIR_BACK
    else:
        native = DIR_RIGHT if local_right >= 0.0 else DIR_LEFT

    return dx, dy, native, local_forward, local_right


def _horizontal_velocity(c):
    try:
        v = c.CharacterMovement.Velocity
        return float(v.X), float(v.Y), float(v.Z)
    except Exception:
        try:
            v = c.GetVelocity()
            return float(v.X), float(v.Y), float(v.Z)
        except Exception:
            return None


def _set_horizontal_velocity(c, speed: float, *, report_error: bool = False) -> bool:
    current = _horizontal_velocity(c)
    if current is None:
        if report_error:
            log_error("DIRECTION ERROR: cannot read CharacterMovement.Velocity")
        return False

    _x, _y, z = current
    if speed <= 1.0:
        if report_error:
            log_error(f"DIRECTION ERROR: invalid horizontal speed {speed:.3f}")
        return False

    target_x = _desired_x * speed
    target_y = _desired_y * speed

    try:
        c.CharacterMovement.Velocity = unrealsdk.make_struct(
            "Vector",
            X=float(target_x),
            Y=float(target_y),
            Z=float(z),
        )
    except Exception as exc:
        if report_error:
            log_error(f"DIRECTION ERROR: Velocity assignment failed: {exc!r}")
        return False

    if report_error:
        after = _horizontal_velocity(c)
        if after is None:
            log_error("DIRECTION ERROR: cannot verify Velocity assignment")
            return False
        ax, ay, _az = after
        if math.hypot(ax - target_x, ay - target_y) >= 2.0:
            log_error(
                "DIRECTION ERROR: Velocity assignment did not stick "
                f"target=({target_x:.3f},{target_y:.3f}) "
                f"after=({ax:.3f},{ay:.3f})"
            )
            return False

    return True


def _correct_super_dash_velocity(c, *, report_error: bool = False) -> bool:
    """Rotate Super Dash X/Y while preserving its initial horizontal speed."""
    global _dash_speed
    global _diagonal_emulation, _diagonal_start_ns
    global _diagonal_duration_s, _diagonal_base_speed, _diagonal_curve

    current = _horizontal_velocity(c)
    if current is None:
        if report_error:
            log_error("DIRECTION ERROR: cannot read CharacterMovement.Velocity")
        return False

    if _dash_speed <= 1.0:
        _dash_speed = math.hypot(current[0], current[1])

    return _set_horizontal_velocity(c, _dash_speed, report_error=report_error)


def _correct_dash_velocity(c, *, report_error: bool = False) -> bool:
    """Rotate native Dash X/Y while preserving the game's current speed curve."""
    current = _horizontal_velocity(c)
    if current is None:
        if report_error:
            log_error("DASH DIRECTION ERROR: cannot read CharacterMovement.Velocity")
        return False

    return _set_horizontal_velocity(
        c,
        math.hypot(current[0], current[1]),
        report_error=report_error,
    )


DASH_ASSET_PATH = (
    "/Game/PlayerCharacters/_Shared/Tricks/ControlledMoves/"
    "Move_Dash.Move_Dash"
)


def _read_native_dash_shape():
    """Read the currently loaded Move_Dash duration, base speed and rich-curve keys."""
    try:
        asset = unrealsdk.find_object("OakControlledMove", DASH_ASSET_PATH)
        duration = float(asset.Duration.constant)
        speed = float(asset.speed.constant)
        keys = tuple(
            (
                float(key.time),
                float(key.Value),
                float(key.ArriveTangent),
                float(key.LeaveTangent),
            )
            for key in asset.SpeedScaleCurve.EditorCurveData.keys
        )
        if duration <= 0.0 or speed <= 1.0 or len(keys) < 2:
            raise ValueError("invalid Move_Dash shape")
        return duration, speed, keys
    except Exception as exc:
        log_error(f"DASH SHAPE ERROR: {exc!r}")
        return None


def _eval_rich_curve(keys, t: float) -> float:
    """Evaluate the scalar RichCurve with cubic Hermite interpolation."""
    if not keys:
        return 1.0

    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]

    for index in range(len(keys) - 1):
        t0, v0, _arrive0, leave0 = keys[index]
        t1, v1, arrive1, _leave1 = keys[index + 1]
        if t <= t1:
            dt = t1 - t0
            if dt <= 0.0:
                return v1

            u = (t - t0) / dt
            u2 = u * u
            u3 = u2 * u
            h00 = 2.0 * u3 - 3.0 * u2 + 1.0
            h10 = u3 - 2.0 * u2 + u
            h01 = -2.0 * u3 + 3.0 * u2
            h11 = u3 - u2
            return (
                h00 * v0
                + h10 * leave0 * dt
                + h01 * v1
                + h11 * arrive1 * dt
            )

    return keys[-1][1]


def _apply_emulated_diagonal_velocity(c, now_ns: int) -> bool:
    elapsed_s = max(0.0, (now_ns - _diagonal_start_ns) / 1_000_000_000.0)
    scale = _eval_rich_curve(_diagonal_curve, elapsed_s)
    speed = max(0.0, _diagonal_base_speed * scale)

    current = _horizontal_velocity(c)
    if current is None:
        return False

    _x, _y, z = current
    try:
        c.CharacterMovement.Velocity = unrealsdk.make_struct(
            "Vector",
            X=float(_desired_x * speed),
            Y=float(_desired_y * speed),
            Z=float(z),
        )
        return True
    except Exception:
        return False


def _is_character_dashing(c) -> bool:
    try:
        return bool(c.IsCharacterDashing())
    except Exception:
        return False


def _restore_sprint_intent(c) -> bool:
    try:
        movement = c.CharacterMovement
    except Exception as exc:
        log_error(f"SPRINT RESTORE ERROR movement={exc!r}")
        return False

    ok = True
    for name in ("bWantsToSprint", "bWantsToStartSprinting"):
        try:
            setattr(movement, name, True)
        except Exception as exc:
            ok = False
            log_error(f"SPRINT RESTORE {name} ERROR={exc!r}")
    return ok


def _release_sequence_inputs() -> None:
    c = _c
    if c is not None:
        try:
            c.StopJumping()
        except Exception:
            pass

        try:
            c.SetWantsToDash(False, int(_native_direction))
        except Exception:
            pass

    _restore_move_mappings()


def _disable_hook() -> None:
    global _anim_hook

    if _anim_hook is None:
        return

    try:
        _anim_hook.disable()
    except Exception:
        pass
    _anim_hook = None


def _reset() -> None:
    global _phase, _sequence_kind, _c, _start_ns, _target_ns
    global _initial_last_dash_time, _resume_sprint, _saw_airborne
    global _landing_deadline_ns, _neutral_frame_count, _dash_min_end_ns
    global _dash_speed, _hold_move_suppression
    global _diagonal_emulation, _diagonal_start_ns
    global _diagonal_duration_s, _diagonal_base_speed, _diagonal_curve
    global _pre_request_speed
    global _forward_gate_neutral

    _release_sequence_inputs()

    _phase = Phase.IDLE
    _sequence_kind = SequenceKind.NONE
    _c = None
    _start_ns = 0
    _target_ns = 0
    _initial_last_dash_time = None
    _resume_sprint = False
    _saw_airborne = False
    _landing_deadline_ns = 0
    _neutral_frame_count = 0
    _dash_min_end_ns = 0
    _dash_speed = 0.0
    _diagonal_emulation = False
    _diagonal_start_ns = 0
    _diagonal_duration_s = 0.33
    _diagonal_base_speed = 2500.0
    _diagonal_curve = ()
    _pre_request_speed = 0.0
    _forward_gate_neutral = False
    _disable_hook()


def _dash_has_started(c) -> bool:
    try:
        current = c.CharacterMovement.LastDashTime
        if _initial_last_dash_time is not None and current != _initial_last_dash_time:
            return True
    except Exception:
        pass

    return _is_character_dashing(c)


def _update_impl(obj: Any, args: Any, ret: Any, func: Any) -> None:
    global _phase, _target_ns, _neutral_frame_count
    global _saw_airborne, _landing_deadline_ns, _dash_speed, _dash_min_end_ns
    global _diagonal_start_ns, _diagonal_duration_s
    global _diagonal_base_speed, _diagonal_curve

    if _phase == Phase.IDLE:
        return

    c = _c
    if c is None or c != get_char():
        _reset()
        return

    try:
        if obj.TryGetPawnOwner() != c:
            return
    except Exception:
        return

    now = time.perf_counter_ns()

    # No sequence is allowed to hold movement suppression indefinitely.
    if _start_ns and now - _start_ns > 2_000_000_000:
        log_error("SEQUENCE FAILSAFE: exceeded 2000 ms; restoring input")
        _reset()
        return

    if _phase == Phase.NEUTRALIZE_MOVE:
        _neutral_frame_count += 1

        if _sequence_kind == SequenceKind.SUPER_DASH:
            _clear_pending_move_input(c)
            if _neutral_frame_count < int(neutral_frames.value):
                return
        elif _forward_gate_neutral:
            # Forward movement must be internally neutralized while the native
            # Dash request is accepted. Preserve the pre-request horizontal
            # speed so an unavailable Dash does not stop the player.
            if not _suppressed_mappings:
                if not _suppress_move_mappings():
                    _reset()
                    return
            _clear_pending_move_input(c)
            if _pre_request_speed > 1.0:
                _set_horizontal_velocity(c, _pre_request_speed)
        else:
            _clear_pending_move_input(c)

        try:
            c.SetWantsToDash(True, int(_native_direction))
        except Exception as exc:
            log_error(f"SetWantsToDash(True,{_native_direction}) ERROR: {exc!r}")
            _reset()
            return

        if _sequence_kind == SequenceKind.SUPER_DASH:
            _restore_move_mappings()
            _target_ns = now + int(dash_timeout_ms.value) * 1_000_000
        else:
            # Ground Dash gets a short native acceptance window. If the game
            # does not accept the request promptly, cancel it before recharge
            # can turn it into a delayed Dash.
            _target_ns = now + GROUND_DASH_ACCEPT_NS

        _phase = Phase.WAIT_DASH_START
        return

    if _phase == Phase.WAIT_DASH_START:
        if (
            _sequence_kind == SequenceKind.DASH
            and _forward_gate_neutral
            and _pre_request_speed > 1.0
        ):
            _set_horizontal_velocity(c, _pre_request_speed)

        if _dash_has_started(c):
            if _sequence_kind == SequenceKind.DASH:
                if not _suppressed_mappings:
                    if not _suppress_move_mappings():
                        _reset()
                        return
                _clear_pending_move_input(c)

                if _diagonal_emulation:
                    shape = _read_native_dash_shape()
                    if shape is None:
                        _reset()
                        return

                    _diagonal_duration_s, _diagonal_base_speed, _diagonal_curve = shape
                    _diagonal_start_ns = now

                    try:
                        c.SetWantsToDash(False, int(_native_direction))
                    except Exception as exc:
                        log_error(f"Diagonal native release ERROR: {exc!r}")
                        _reset()
                        return

                    if not _apply_emulated_diagonal_velocity(c, now):
                        log_error("DIAGONAL ERROR: cannot start emulated velocity")
                        _reset()
                        return

                    _phase = Phase.DIAGONAL_DASH_ACTIVE
                    return

                if not _correct_dash_velocity(c, report_error=True):
                    _reset()
                    return

                _dash_min_end_ns = now + 50_000_000
                _target_ns = now + 750_000_000
                _phase = Phase.DASH_ACTIVE
                return

            current = _horizontal_velocity(c)
            if current is not None:
                _dash_speed = math.hypot(current[0], current[1])

            if not _correct_super_dash_velocity(c, report_error=True):
                _reset()
                return

            try:
                c.Jump()
            except Exception as exc:
                log_error(f"Jump ERROR: {exc!r}")
                _reset()
                return

            _target_ns = now + int(jump_hold_ms.value) * 1_000_000
            _phase = Phase.HOLD_JUMP
            return

        if _sequence_kind == SequenceKind.DASH:
            if now < _target_ns:
                return

            try:
                c.SetWantsToDash(False, int(_native_direction))
            except Exception:
                pass
            _reset()
            return

        if now >= _target_ns:
            log_error(
                f"ABORT dash timeout total={(now - _start_ns)/1_000_000:.3f}ms "
                f"direction={_native_direction}"
            )
            _reset()
        return

    if _phase == Phase.DIAGONAL_DASH_ACTIVE:
        elapsed_s = (now - _diagonal_start_ns) / 1_000_000_000.0

        if elapsed_s < _diagonal_duration_s:
            if not _apply_emulated_diagonal_velocity(c, now):
                log_error("DIAGONAL ERROR: velocity update failed")
                _reset()
            return

        # Apply the native curve's final speed once, then give movement back.
        _apply_emulated_diagonal_velocity(c, now)
        _restore_move_mappings()


        if _resume_sprint:
            _restore_sprint_intent(c)
            _target_ns = now + 40_000_000
            _phase = Phase.SPRINT_SETTLE
        else:
            _phase = Phase.FINISH
        return

    if _phase == Phase.DASH_ACTIVE:
        if _is_character_dashing(c):
            _correct_dash_velocity(c)
            if now >= _target_ns:
                log_error("DASH TIMEOUT: native dash remained active too long")
                _reset()
            return

        if now < _dash_min_end_ns:
            _correct_dash_velocity(c)
            return

        try:
            c.SetWantsToDash(False, int(_native_direction))
        except Exception as exc:
            log_error(f"Dash release ERROR: {exc!r}")

        _restore_move_mappings()

        if _resume_sprint:
            _restore_sprint_intent(c)
            _target_ns = now + 40_000_000
            _phase = Phase.SPRINT_SETTLE
        else:
            _phase = Phase.FINISH
        return

    if _phase == Phase.HOLD_JUMP:
        _correct_super_dash_velocity(c)

        if now < _target_ns:
            return

        try:
            c.StopJumping()
        except Exception as exc:
            log_error(f"StopJumping ERROR: {exc!r}")

        _target_ns = now + int(release_delay_ms.value) * 1_000_000
        _phase = Phase.WAIT_RELEASE
        return

    if _phase == Phase.WAIT_RELEASE:
        _correct_super_dash_velocity(c)

        if now < _target_ns:
            return

        try:
            c.SetWantsToDash(False, int(_native_direction))
        except Exception as exc:
            log_error(f"Dash release ERROR: {exc!r}")

        if _resume_sprint:
            mode = _movement_mode_name(c)
            _saw_airborne = "MOVE_Falling" in mode
            _landing_deadline_ns = now + 3_000_000_000
            _phase = Phase.WAIT_LANDING
        else:
            _phase = Phase.FINISH
        return

    if _phase == Phase.WAIT_LANDING:
        mode = _movement_mode_name(c)

        if "MOVE_Falling" in mode:
            _saw_airborne = True
            return

        if _saw_airborne and "MOVE_Walking" in mode:
            _restore_sprint_intent(c)
            _target_ns = now + 40_000_000
            _phase = Phase.SPRINT_SETTLE
            return

        if now >= _landing_deadline_ns:
            log_error("SPRINT RESTORE TIMEOUT: no landing detected")
            _phase = Phase.FINISH
        return

    if _phase == Phase.SPRINT_SETTLE:
        if now >= _target_ns:
            _phase = Phase.FINISH
        return

    if _phase == Phase.FINISH:
        _reset()


def _update(obj: Any, args: Any, ret: Any, func: Any) -> None:
    try:
        _update_impl(obj, args, ret, func)
    except Exception as exc:
        log_error(f"SEQUENCE EXCEPTION: {type(exc).__name__}: {exc}")
        try:
            _reset()
        except Exception as reset_exc:
            log_error(
                f"SEQUENCE RESET ERROR: {type(reset_exc).__name__}: {reset_exc}"
            )
            # Last-resort cleanup must not leave movement input suppressed.
            try:
                _restore_move_mappings()
            except Exception:
                pass
            try:
                if _c is not None:
                    _c.SetWantsToDash(False, int(_native_direction))
            except Exception:
                pass


def _enable_hook() -> bool:
    global _anim_hook

    if _anim_hook is not None:
        return True

    try:
        h = hook(
            "/Script/Engine.AnimInstance:BlueprintUpdateAnimation",
            Type.POST,
            hook_identifier="BL4_SuperDash:sequence",
        )(_update)
        h.enable()
    except Exception as exc:
        log_error(f"sequence hook ERROR: {exc!r}")
        return False

    _anim_hook = h
    return True


def _begin_sequence(c, sequence_kind: SequenceKind, captured) -> bool:
    global _phase, _sequence_kind, _c, _start_ns, _initial_last_dash_time
    global _neutral_frame_count, _resume_sprint, _saw_airborne
    global _landing_deadline_ns, _desired_x, _desired_y, _native_direction
    global _dash_speed, _dash_min_end_ns
    global _diagonal_emulation, _diagonal_start_ns
    global _diagonal_duration_s, _diagonal_base_speed, _diagonal_curve
    global _local_forward, _local_right, _pre_request_speed
    global _forward_gate_neutral

    try:
        movement = c.CharacterMovement
        _initial_last_dash_time = movement.LastDashTime
        _resume_sprint = bool(movement.bIsSprinting)
    except Exception:
        _initial_last_dash_time = None
        _resume_sprint = False

    _desired_x, _desired_y, _native_direction = captured[:3]
    _sequence_kind = sequence_kind
    local_forward = float(captured[3]) if len(captured) >= 5 else 0.0
    local_right = float(captured[4]) if len(captured) >= 5 else 0.0

    current = _horizontal_velocity(c)
    _pre_request_speed = (
        math.hypot(current[0], current[1]) if current is not None else 0.0
    )

    _forward_gate_neutral = (
        sequence_kind == SequenceKind.DASH and local_forward > 0.0
    )
    _diagonal_emulation = (
        sequence_kind == SequenceKind.DASH
        and len(captured) >= 5
        and abs(local_forward) >= 0.20
        and abs(local_right) >= 0.20
    )
    _diagonal_start_ns = 0
    _diagonal_duration_s = 0.33
    _diagonal_base_speed = 2500.0
    _diagonal_curve = ()
    _saw_airborne = False
    _landing_deadline_ns = 0
    _dash_speed = 0.0
    _dash_min_end_ns = 0

    if not _enable_hook():
        _sequence_kind = SequenceKind.NONE
        return False

    _c = c
    _start_ns = time.perf_counter_ns()
    _neutral_frame_count = 0

    try:
        c.SetWantsToDash(False, int(_native_direction))
    except Exception:
        pass

    if sequence_kind == SequenceKind.SUPER_DASH:
        if not _suppress_move_mappings():
            _reset()
            return False
        _clear_pending_move_input(c)

    _phase = Phase.NEUTRALIZE_MOVE
    return True


@keybind(
    "Super Dash",
    key=None,
    description="Perform the one-key multi-directional Super Dash.",
    is_hidden=False,
    is_rebindable=True,
    event_filter=EInputEvent.IE_Pressed,
)
def super_dash() -> None:
    if _phase != Phase.IDLE:
        return

    c = get_char()
    if c is None:
        return

    captured = _capture_direction(c, allow_standstill=True)
    if captured is not None:
        _begin_sequence(c, SequenceKind.SUPER_DASH, captured)


@keybind(
    "Forward Dash",
    key=None,
    display_name="Dash",
    description=(
        "Perform Dash on the ground or in the air in the current movement direction, "
        "including forward and diagonal directions, with forward fallback when stationary."
    ),
    is_hidden=False,
    is_rebindable=True,
    event_filter=EInputEvent.IE_Pressed,
)
def dash() -> None:
    if _phase != Phase.IDLE:
        return

    c = get_char()
    if c is None:
        return

    mode = _movement_mode_name(c)
    if "MOVE_Walking" not in mode and "MOVE_Falling" not in mode:
        return

    captured = _capture_direction(c, allow_standstill=True)
    if captured is not None:
        dx, dy, native, local_forward, local_right = captured

        if local_forward > 0.0:
            if local_right > 0.20:
                native = DIR_RIGHT
            elif local_right < -0.20:
                native = DIR_LEFT
            else:
                native = DIR_FORWARD

        captured = (dx, dy, native, local_forward, local_right)
        _begin_sequence(c, SequenceKind.DASH, captured)


def on_disable() -> None:
    _reset()


try:
    LOG.write_text(
        f"BL4 Super Dash {VERSION}\n",
        encoding="utf-8",
    )
except Exception:
    pass


build_mod(
    keybinds=[super_dash, dash],
    options=[
        neutral_frames,
        jump_hold_ms,
        release_delay_ms,
        dash_timeout_ms,
    ],
    on_disable=on_disable,
)
