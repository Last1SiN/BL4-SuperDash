from __future__ import annotations

import time
from typing import Any

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

LOG = MODS_DIR / "BL4_SuperDash.log"

IDLE = 0
NEUTRALIZE_FORWARD = 1
WAIT_DASH_START = 2
HOLD_JUMP = 3
WAIT_RELEASE = 4
FINAL_SNAPSHOT = 5
WAIT_LANDING = 6
VERIFY_SPRINT = 7

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

# (mapping WrappedStruct, original bShouldBeIgnored)
_suppressed_mappings = []

FORWARD_DIRECTION = 0


def log(msg: str) -> None:
    line = "[BL4 Super Dash v1.1.1] " + msg
    print(line)
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
    display_name="Forward Neutral Frames",
    description=(
        "Frames for which the mod internally suppresses the current Forward mapping "
        "before re-arming it. Default: 1."
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
    description="Abort if dash does not start after Forward re-arm. Default: 300 ms.",
)


def _snapshot(c) -> str:
    parts = []

    try:
        parts.append(f"velocity={c.GetVelocity()!r}")
    except Exception:
        pass

    try:
        parts.append(f"dashing={c.IsCharacterDashing()!r}")
    except Exception:
        pass

    try:
        m = c.CharacterMovement
        parts.append(f"bWantsToDash={m.bWantsToDash!r}")
        parts.append(f"LastDashTime={m.LastDashTime!r}")
        parts.append(f"MovementMode={m.MovementMode!r}")
        parts.append(f"bIsSprinting={m.bIsSprinting!r}")
        parts.append(f"bWantsToSprint={m.bWantsToSprint!r}")
        parts.append(f"bWantsToStartSprinting={m.bWantsToStartSprinting!r}")
    except Exception:
        pass

    try:
        parts.append(f"bPressedJump={c.bPressedJump!r}")
    except Exception:
        pass

    return " ".join(parts)


def _mapping_action(mapping) -> str:
    try:
        return repr(mapping.Action)
    except Exception:
        return ""


def _mapping_key(mapping) -> str:
    try:
        return str(mapping.Key.KeyName)
    except Exception:
        return ""


def _mapping_modifiers(mapping) -> str:
    try:
        return repr(list(mapping.Modifiers))
    except Exception:
        try:
            return repr(mapping.Modifiers)
        except Exception:
            return ""


def _find_forward_rearm_mappings():
    """
    Find every Enhanced Input mapping that must be neutralized for one frame.

    Keyboard path:
      - discover the current Forward key from Action_Move by its modifier chain;
      - include both Action_Move and Action_DashDirection for that key.

    Gamepad path:
      - discover 2D gamepad movement mappings (normally Gamepad_Left2D);
      - include both Action_Move and Action_DashDirection for the same stick key.

    Both paths are suppressed together. This avoids needing an input-device mode
    switch and lets the same Super Dash key work with keyboard or controller.
    """
    p = get_pc_safe()
    if p is None:
        return [], [], []

    try:
        mappings = list(p.PlayerInput.EnhancedActionMappings)
    except Exception:
        return [], [], []

    keyboard_forward_keys = []
    gamepad_move_keys = []

    for mapping in mappings:
        action = _mapping_action(mapping)
        if "Action_Move.Action_Move" not in action:
            continue

        key = _mapping_key(mapping)
        if not key or key == "None":
            continue

        mods = _mapping_modifiers(mapping)

        if key.startswith("Gamepad_"):
            # BL4 currently exposes movement as Gamepad_Left2D. Keep this generic
            # enough to survive a future/right-stick or southpaw mapping variant.
            if "2D" in key and key not in gamepad_move_keys:
                gamepad_move_keys.append(key)
            continue

        # Keyboard Forward is the positive Y Action_Move mapping: SwizzleAxis,
        # without Negate. This survives normal user remapping of W.
        if "InputModifierSwizzleAxis" in mods and "InputModifierNegate" not in mods:
            if key not in keyboard_forward_keys:
                keyboard_forward_keys.append(key)

    target_keys = set(keyboard_forward_keys + gamepad_move_keys)
    result = []

    for mapping in mappings:
        key = _mapping_key(mapping)
        if key not in target_keys:
            continue

        action = _mapping_action(mapping)
        if (
            "Action_Move.Action_Move" in action
            or "Action_DashDirection.Action_DashDirection" in action
        ):
            result.append(mapping)

    return keyboard_forward_keys, gamepad_move_keys, result

def _set_mapping_ignored(mapping, ignored: bool) -> bool:
    try:
        mapping.bShouldBeIgnored = bool(ignored)
        return bool(mapping.bShouldBeIgnored) == bool(ignored)
    except Exception:
        return False


def _suppress_forward_mappings() -> bool:
    global _suppressed_mappings

    _suppressed_mappings = []

    keyboard_keys, gamepad_keys, mappings = _find_forward_rearm_mappings()
    if not mappings:
        log(
            "FORWARD SUPPRESS ERROR: no keyboard/gamepad Forward mappings found "
            f"keyboard={keyboard_keys!r} gamepad={gamepad_keys!r}"
        )
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

    states = []
    for mapping, original in _suppressed_mappings:
        try:
            current = bool(mapping.bShouldBeIgnored)
        except Exception:
            current = None

        states.append(
            f"{_mapping_action(mapping)} key={_mapping_key(mapping)} "
            f"original={original} now={current}"
        )

    log(
        "FORWARD SUPPRESSED "
        f"keyboard={keyboard_keys!r} gamepad={gamepad_keys!r} "
        f"count={len(_suppressed_mappings)} ok={ok} | "
        + " || ".join(states)
    )

    return ok

def _restore_forward_mappings() -> None:
    global _suppressed_mappings

    if not _suppressed_mappings:
        return

    states = []

    for mapping, original in _suppressed_mappings:
        success = _set_mapping_ignored(mapping, original)

        try:
            current = bool(mapping.bShouldBeIgnored)
        except Exception:
            current = None

        states.append(
            f"{_mapping_action(mapping)} key={_mapping_key(mapping)} "
            f"restore={original} now={current} ok={success}"
        )

    log("FORWARD RESTORED | " + " || ".join(states))
    _suppressed_mappings = []


def _clear_pending_move_input(c) -> None:
    # Best effort: clear already accumulated movement input so the one-frame
    # logical Forward release reaches the movement layer immediately.
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


def _restore_sprint_intent(c) -> bool:
    """
    Re-assert sprint only when Super Dash started from an actual sprint.

    We deliberately do not force bIsSprinting.  Instead we restore the movement
    component's normal sprint intent and let BL4 transition back into sprint.
    """
    try:
        m = c.CharacterMovement
    except Exception as exc:
        log(f"SPRINT RESTORE ERROR movement={exc!r}")
        return False

    ok = True

    for name in ("bWantsToSprint", "bWantsToStartSprinting"):
        try:
            setattr(m, name, True)
        except Exception as exc:
            ok = False
            log(f"SPRINT RESTORE {name} ERROR={exc!r}")

    log(f"SPRINT INTENT RESTORED ok={ok} | {_snapshot(c)}")
    return ok

def _release_sequence_inputs() -> None:
    c = _c

    if c is not None:
        try:
            c.StopJumping()
        except Exception:
            pass

        try:
            c.SetWantsToDash(False, FORWARD_DIRECTION)
        except Exception:
            pass

    _restore_forward_mappings()


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
    global _landing_deadline_ns

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


_neutral_frame_count = 0


def _update(obj: Any, args: Any, ret: Any, func: Any) -> None:
    global _phase, _target_ns, _jump_press_ns, _neutral_frame_count
    global _saw_airborne, _landing_deadline_ns

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

    if _phase == NEUTRALIZE_FORWARD:
        _neutral_frame_count += 1
        _clear_pending_move_input(c)

        log(
            f"NEUTRAL FRAME {_neutral_frame_count}/{int(neutral_frames.value)} "
            f"| {_snapshot(c)}"
        )

        if _neutral_frame_count < int(neutral_frames.value):
            return

        # Set the dash request while Forward is still logically released,
        # then restore Forward. On the next Enhanced Input processing pass,
        # the physically-held key or analog stick is presented to the game again.
        try:
            c.SetWantsToDash(True, FORWARD_DIRECTION)
        except Exception as exc:
            log(f"SetWantsToDash(True, 0) ERROR: {exc!r}")
            _reset()
            return

        _restore_forward_mappings()

        _phase = WAIT_DASH_START
        _target_ns = now + int(dash_timeout_ms.value) * 1_000_000

        log(f"FORWARD RE-ARMED; waiting for dash | {_snapshot(c)}")
        return

    if _phase == WAIT_DASH_START:
        if _dash_has_started(c):
            log(
                f"DASH START after={(now - _start_ns)/1_000_000:.3f}ms "
                f"| {_snapshot(c)}"
            )

            try:
                c.Jump()
            except Exception as exc:
                log(f"Jump ERROR: {exc!r}")
                _reset()
                return

            _jump_press_ns = now
            _target_ns = now + int(jump_hold_ms.value) * 1_000_000
            _phase = HOLD_JUMP

            log(f"JUMP DOWN | {_snapshot(c)}")
            return

        if now >= _target_ns:
            log(
                f"ABORT dash timeout total={(now - _start_ns)/1_000_000:.3f}ms "
                f"| {_snapshot(c)}"
            )
            _reset()

        return

    if _phase == HOLD_JUMP:
        if now < _target_ns:
            return

        try:
            c.StopJumping()
        except Exception as exc:
            log(f"StopJumping ERROR: {exc!r}")

        log(
            f"JUMP UP held={(now - _jump_press_ns)/1_000_000:.3f}ms "
            f"| {_snapshot(c)}"
        )

        _target_ns = now + int(release_delay_ms.value) * 1_000_000
        _phase = WAIT_RELEASE
        return

    if _phase == WAIT_RELEASE:
        if now < _target_ns:
            return

        try:
            c.SetWantsToDash(False, FORWARD_DIRECTION)
        except Exception as exc:
            log(f"Dash release ERROR: {exc!r}")

        log(
            f"DASH REQUEST UP total={(now - _start_ns)/1_000_000:.3f}ms "
            f"| {_snapshot(c)}"
        )

        if _resume_sprint:
            mode = _movement_mode_name(c)
            _saw_airborne = "MOVE_Falling" in mode
            _landing_deadline_ns = now + 3_000_000_000
            _phase = WAIT_LANDING
            log(
                "SPRINT PRESERVE armed; waiting for landing "
                f"saw_airborne={_saw_airborne} | {_snapshot(c)}"
            )
        else:
            _phase = FINAL_SNAPSHOT
        return

    if _phase == WAIT_LANDING:
        mode = _movement_mode_name(c)

        if "MOVE_Falling" in mode:
            _saw_airborne = True
            return

        if _saw_airborne and "MOVE_Walking" in mode:
            log(f"LANDING DETECTED; restoring pre-dash sprint | {_snapshot(c)}")
            _restore_sprint_intent(c)
            _target_ns = now + 40_000_000
            _phase = VERIFY_SPRINT
            return

        if now >= _landing_deadline_ns:
            log(f"SPRINT RESTORE TIMEOUT; no landing detected | {_snapshot(c)}")
            _phase = FINAL_SNAPSHOT
        return

    if _phase == VERIFY_SPRINT:
        if now < _target_ns:
            return

        log(f"SPRINT VERIFY | {_snapshot(c)}")
        _phase = FINAL_SNAPSHOT
        return

    if _phase == FINAL_SNAPSHOT:
        log(
            f"DONE total={(now - _start_ns)/1_000_000:.3f}ms "
            f"| {_snapshot(c)}"
        )
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
        log(f"sequence hook ERROR: {exc!r}")
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
    global _landing_deadline_ns

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

    _saw_airborne = False
    _landing_deadline_ns = 0

    if not _enable_hook():
        return

    _c = c
    _start_ns = time.perf_counter_ns()
    _neutral_frame_count = 0

    # Always perform the re-arm. It also works from standstill and avoids
    # maintaining two subtly different input paths.
    try:
        c.SetWantsToDash(False, FORWARD_DIRECTION)
    except Exception:
        pass

    if not _suppress_forward_mappings():
        _reset()
        return

    _clear_pending_move_input(c)
    _phase = NEUTRALIZE_FORWARD

    log(
        f"PRESS; internal Forward release/re-arm sequence "
        f"| initial_LastDashTime={_initial_last_dash_time!r} "
        f"resume_sprint={_resume_sprint} {_snapshot(c)}"
    )


def on_disable() -> None:
    _reset()


try:
    LOG.write_text(
        "BL4 Super Dash v1.1.1\n"
        "One-key Super Dash with keyboard/gamepad support and sprint preservation.\n",
        encoding="utf-8",
    )
except Exception:
    pass

log("Loaded.")
build_mod(on_disable=on_disable)
