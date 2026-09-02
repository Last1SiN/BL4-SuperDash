from __future__ import annotations

import math
import time
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

VERSION = "1.2.0"
LOG = MODS_DIR / "BL4_SuperDash.log"

IDLE = 0
NEUTRALIZE_MOVE = 1
WAIT_DASH_START = 2
HOLD_JUMP = 3
WAIT_RELEASE = 4
WAIT_LANDING = 5
VERIFY_SPRINT = 6
FINISH = 7

_phase = IDLE
_c = None
_start_ns = 0
_target_ns = 0
_jump_press_ns = 0
_initial_last_dash_time = None
_anim_hook = None
_resume_sprint = False
_saw_airborne = False
_landing_deadline_ns = 0
_neutral_frame_count = 0

# (mapping WrappedStruct, original bShouldBeIgnored)
_suppressed_mappings = []

# Captured direction for the active Super Dash.
_desired_x = 0.0
_desired_y = 0.0
_native_direction = 0
_dash_speed = 0.0

# Native SetWantsToDash enum, verified in Borderlands 4:
# 0 = Forward, 1 = Left, 2 = Back, 3 = Right.
DIR_FORWARD = 0
DIR_LEFT = 1
DIR_BACK = 2
DIR_RIGHT = 3


def log_error(msg: str) -> None:
    """Write exceptional/abort diagnostics to file without console output."""
    line = "[BL4 Super Dash v1.2.0] " + msg
    try:
        with LOG.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
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
    except Exception:
        return None

    try:
        text = repr(c)
        if "/FrontEnd/" in text or "Entry_P" in text:
            return None
    except Exception:
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
        "Frames for which the mod internally suppresses movement input before "
        "re-arming the directional dash. Default: 1."
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
    description="How long Jump remains held. Default: 25 ms.",
)

release_delay_ms = SliderOption(
    identifier="release_delay_ms",
    value=15,
    min_value=0,
    max_value=40,
    step=1,
    is_integer=True,
    display_name="Jump Release -> Dash Release (ms)",
    description="How long Dash remains requested after Jump release. Default: 15 ms.",
)

dash_timeout_ms = SliderOption(
    identifier="dash_timeout_ms",
    value=300,
    min_value=50,
    max_value=1000,
    step=10,
    is_integer=True,
    display_name="Dash Start Timeout (ms)",
    description="Abort if dash does not start after movement re-arm. Default: 300 ms.",
)


def _mapping_action(mapping) -> str:
    try:
        return repr(mapping.Action)
    except Exception:
        return ""


def _find_move_rearm_mappings():
    """Find Action_Move and Action_DashDirection mappings to neutralize briefly.

    We suppress the complete movement action pair for one animation frame. The
    desired direction is captured first, then the physical mappings are restored
    immediately after SetWantsToDash is re-armed. This works for remapped
    keyboard controls and for the 2D gamepad movement action.
    """
    p = get_pc_safe()
    if p is None:
        return []

    try:
        mappings = list(p.PlayerInput.EnhancedActionMappings)
    except Exception:
        return []

    result = []
    for mapping in mappings:
        action = _mapping_action(mapping)
        if (
            "Action_Move.Action_Move" in action
            or "Action_DashDirection.Action_DashDirection" in action
        ):
            result.append(mapping)
    return result


def _set_mapping_ignored(mapping, ignored: bool) -> bool:
    try:
        mapping.bShouldBeIgnored = bool(ignored)
        return bool(mapping.bShouldBeIgnored) == bool(ignored)
    except Exception:
        return False


def _suppress_move_mappings() -> bool:
    global _suppressed_mappings

    _suppressed_mappings = []
    mappings = _find_move_rearm_mappings()
    if not mappings:
        log_error("MOVEMENT SUPPRESS ERROR: no Action_Move/Action_DashDirection mappings found")
        return False

    ok = True
    for mapping in mappings:
        try:
            original = bool(mapping.bShouldBeIgnored)
        except Exception:
            original = False

        _suppressed_mappings.append((mapping, original))
        if not _set_mapping_ignored(mapping, True):
            ok = False

    if not ok:
        log_error("MOVEMENT SUPPRESS WARNING: one or more mappings could not be suppressed")
    return True


def _restore_move_mappings() -> None:
    global _suppressed_mappings

    for mapping, original in _suppressed_mappings:
        _set_mapping_ignored(mapping, original)
    _suppressed_mappings = []


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


def _capture_desired_direction(c) -> tuple[float, float, int]:
    """Capture the current world-space movement direction.

    Keyboard diagonals arrive as a combined movement vector. Gamepad movement
    arrives as a continuous 2D vector, so arbitrary stick angles are preserved.
    If the character is effectively stationary, legacy forward behavior is used.
    """
    p = get_pc_safe()
    yaw = 0.0
    if p is not None:
        try:
            yaw = float(p.GetControlRotation().Yaw)
        except Exception:
            pass

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

    imag = math.hypot(ix, iy)

    if speed > 5.0 and imag > 0.001:
        dx, dy = ix / imag, iy / imag
    elif speed > 5.0:
        smag = math.hypot(vx, vy)
        dx, dy = vx / smag, vy / smag
    else:
        dx, dy = cam_fx, cam_fy

    local_f = dx * cam_fx + dy * cam_fy
    local_r = dx * cam_rx + dy * cam_ry

    # The native call only exposes four directions. Use the nearest cardinal
    # native dash as the launch state, then rotate its horizontal velocity to
    # the exact captured movement angle.
    if abs(local_f) >= abs(local_r):
        enum = DIR_FORWARD if local_f >= 0.0 else DIR_BACK
    else:
        enum = DIR_RIGHT if local_r >= 0.0 else DIR_LEFT

    return dx, dy, enum


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


def _correct_horizontal_velocity(c, *, report_error: bool = False) -> bool:
    """Rotate native dash X/Y to the captured angle while preserving Z.

    Direct Velocity assignment is intentional. The earlier test implementation
    used repeated AddImpulse calls; BL4 queued those impulses and could release
    them later as a very large horizontal velocity spike. Direct assignment
    avoids that accumulation and also avoids the view-direction snap seen in
    the first directional prototype.
    """
    global _dash_speed

    current = _horizontal_velocity(c)
    if current is None:
        if report_error:
            log_error("DIRECTION ERROR: cannot read CharacterMovement.Velocity")
        return False

    cx, cy, cz = current
    current_speed = math.hypot(cx, cy)
    if _dash_speed <= 1.0:
        _dash_speed = current_speed
    if _dash_speed <= 1.0:
        if report_error:
            log_error(f"DIRECTION ERROR: invalid dash horizontal speed {_dash_speed:.3f}")
        return False

    tx = _desired_x * _dash_speed
    ty = _desired_y * _dash_speed

    try:
        velocity = unrealsdk.make_struct(
            "Vector",
            X=float(tx),
            Y=float(ty),
            Z=float(cz),
        )
        c.CharacterMovement.Velocity = velocity
    except Exception as exc:
        if report_error:
            log_error(f"DIRECTION ERROR: direct Velocity assignment failed: {exc!r}")
        return False

    if report_error:
        after = _horizontal_velocity(c)
        if after is None:
            log_error("DIRECTION ERROR: cannot verify direct Velocity assignment")
            return False
        ax, ay, _az = after
        if math.hypot(ax - tx, ay - ty) >= 2.0:
            log_error(
                "DIRECTION ERROR: direct Velocity assignment did not stick "
                f"target=({tx:.3f},{ty:.3f}) after=({ax:.3f},{ay:.3f})"
            )
            return False

    return True


def _restore_sprint_intent(c) -> bool:
    """Restore normal sprint intent only if Super Dash started from sprint."""
    try:
        m = c.CharacterMovement
    except Exception as exc:
        log_error(f"SPRINT RESTORE ERROR movement={exc!r}")
        return False

    ok = True
    for name in ("bWantsToSprint", "bWantsToStartSprinting"):
        try:
            setattr(m, name, True)
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
    global _phase, _c, _start_ns, _target_ns, _jump_press_ns
    global _initial_last_dash_time, _resume_sprint, _saw_airborne
    global _landing_deadline_ns, _neutral_frame_count, _dash_speed

    _release_sequence_inputs()

    _phase = IDLE
    _c = None
    _start_ns = 0
    _target_ns = 0
    _jump_press_ns = 0
    _initial_last_dash_time = None
    _resume_sprint = False
    _saw_airborne = False
    _landing_deadline_ns = 0
    _neutral_frame_count = 0
    _dash_speed = 0.0
    _disable_hook()


def _dash_has_started(c) -> bool:
    try:
        current = c.CharacterMovement.LastDashTime
        if _initial_last_dash_time is not None and current != _initial_last_dash_time:
            return True
    except Exception:
        pass

    try:
        return bool(c.IsCharacterDashing())
    except Exception:
        return False


def _update(obj: Any, args: Any, ret: Any, func: Any) -> None:
    global _phase, _target_ns, _jump_press_ns, _neutral_frame_count
    global _saw_airborne, _landing_deadline_ns, _dash_speed

    if _phase == IDLE:
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

    if _phase == NEUTRALIZE_MOVE:
        _neutral_frame_count += 1
        _clear_pending_move_input(c)

        if _neutral_frame_count < int(neutral_frames.value):
            return

        try:
            c.SetWantsToDash(True, int(_native_direction))
        except Exception as exc:
            log_error(f"SetWantsToDash(True,{_native_direction}) ERROR: {exc!r}")
            _reset()
            return

        _restore_move_mappings()
        _phase = WAIT_DASH_START
        _target_ns = now + int(dash_timeout_ms.value) * 1_000_000
        return

    if _phase == WAIT_DASH_START:
        if _dash_has_started(c):
            v = _horizontal_velocity(c)
            if v is not None:
                _dash_speed = math.hypot(v[0], v[1])

            if not _correct_horizontal_velocity(c, report_error=True):
                _reset()
                return

            try:
                c.Jump()
            except Exception as exc:
                log_error(f"Jump ERROR: {exc!r}")
                _reset()
                return

            _jump_press_ns = now
            _target_ns = now + int(jump_hold_ms.value) * 1_000_000
            _phase = HOLD_JUMP
            return

        if now >= _target_ns:
            log_error(
                f"ABORT dash timeout total={(now - _start_ns)/1_000_000:.3f}ms "
                f"direction={_native_direction}"
            )
            _reset()
        return

    if _phase == HOLD_JUMP:
        # Keep the horizontal dash vector on the captured angle during the brief
        # native dash window. Vertical velocity remains untouched.
        _correct_horizontal_velocity(c)

        if now < _target_ns:
            return

        try:
            c.StopJumping()
        except Exception as exc:
            log_error(f"StopJumping ERROR: {exc!r}")

        _target_ns = now + int(release_delay_ms.value) * 1_000_000
        _phase = WAIT_RELEASE
        return

    if _phase == WAIT_RELEASE:
        _correct_horizontal_velocity(c)

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
            _phase = WAIT_LANDING
        else:
            _phase = FINISH
        return

    if _phase == WAIT_LANDING:
        mode = _movement_mode_name(c)

        if "MOVE_Falling" in mode:
            _saw_airborne = True
            return

        if _saw_airborne and "MOVE_Walking" in mode:
            _restore_sprint_intent(c)
            _target_ns = now + 40_000_000
            _phase = VERIFY_SPRINT
            return

        if now >= _landing_deadline_ns:
            log_error("SPRINT RESTORE TIMEOUT; no landing detected")
            _phase = FINISH
        return

    if _phase == VERIFY_SPRINT:
        if now < _target_ns:
            return
        _phase = FINISH
        return

    if _phase == FINISH:
        _reset()


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


@keybind(
    "Super Dash",
    key=None,
    is_hidden=False,
    is_rebindable=True,
    event_filter=EInputEvent.IE_Pressed,
)
def super_dash() -> None:
    global _phase, _c, _start_ns, _initial_last_dash_time
    global _neutral_frame_count, _resume_sprint, _saw_airborne
    global _landing_deadline_ns, _desired_x, _desired_y, _native_direction
    global _dash_speed

    if _phase != IDLE:
        return

    c = get_char()
    if c is None:
        return

    try:
        m = c.CharacterMovement
        _initial_last_dash_time = m.LastDashTime
        _resume_sprint = bool(m.bIsSprinting)
    except Exception:
        _initial_last_dash_time = None
        _resume_sprint = False

    _desired_x, _desired_y, _native_direction = _capture_desired_direction(c)
    _saw_airborne = False
    _landing_deadline_ns = 0
    _dash_speed = 0.0

    if not _enable_hook():
        return

    _c = c
    _start_ns = time.perf_counter_ns()
    _neutral_frame_count = 0

    try:
        c.SetWantsToDash(False, int(_native_direction))
    except Exception:
        pass

    if not _suppress_move_mappings():
        _reset()
        return

    _clear_pending_move_input(c)
    _phase = NEUTRALIZE_MOVE


def on_disable() -> None:
    _reset()


try:
    LOG.write_text(
        "BL4 Super Dash v1.2.0\n"
        "Error log only; normal Super Dash activations are not logged.\n",
        encoding="utf-8",
    )
except Exception:
    pass

build_mod(on_disable=on_disable)
