# BL4 Super Dash

**Borderlands 4 PythonSDK / Oak2 mod**

Current release: **v1.1.1**

> Installation-ready `.sdkmod` files are published under **Releases**.  
> The files in this repository are the mod source.

---

Perform Borderlands 4's Super Dash movement technique with a single rebindable key or controller button.

## Features

- One-key Super Dash.
- Works from a standstill.
- Works while already moving forward.
- Works while already sprinting.
- Preserves sprint after landing when Super Dash was started from a sprint.
- Forward can remain physically held throughout the sequence.
- Supports keyboard/mouse and gamepad activation.
- Automatically detects keyboard and gamepad movement mappings.
- Uses Borderlands 4's native Dash and Jump movement calls.
- No Windows SendInput or external macro software.
- Adjustable timing options.

## Requirements

- Borderlands 4
- BL-SDK / Oak2 Mod Manager
- Mods Base 1.12+
- Keybinds 1.1+
- Keyboard or gamepad movement controls

## Installation

1. Copy `BL4_SuperDash.sdkmod` to:

   `Borderlands 4\sdk_mods\`

2. Start Borderlands 4.
3. Open the PythonSDK / Mods menu.
4. Enable **BL4 Super Dash**.
5. Bind **Super Dash** to the desired keyboard key, mouse button, or gamepad button.

Do not extract `BL4_SuperDash.sdkmod`.

Remove or disable older test builds before installing this release.

## Configuration

Default values are the tested release settings and normally do not need to be changed.

- **Forward Neutral Frames:** 1
- **Jump Hold (ms):** 25
- **Jump Release -> Dash Release (ms):** 15
- **Dash Start Timeout (ms):** 300

## Sprint preservation

Version 1.1.1 preserves sprint state across Super Dash.

If Super Dash is activated while the character is already sprinting, the mod remembers the actual sprint state, performs the normal Super Dash sequence, waits for landing, and restores the game's normal sprint intent.

If Super Dash is started from normal movement, sprint is not forced on.


## Compatibility and license

- Co-op support: **ClientSide** — tested with BL4 Super Dash installed only on the local player while the other co-op players did not have the mod installed.
- License: **GPL-3.0**

## Credits

Creator: Sol (ChatGPT, GPT-5.6 Sol)
QA: Last1SiN
