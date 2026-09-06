# BL4 Super Dash

Perform Borderlands 4's Super Dash movement technique with a single rebindable key or controller button, now in the direction you are currently moving.

## Features

- One-key Super Dash.
- Directional Super Dash based on current movement.
- Keyboard supports all 8 movement directions, including diagonals.
- Gamepad preserves the continuous analog angle of the left stick rather than reducing it to 4/8 sectors.
- From a standstill, Super Dash keeps the original forward behavior.
- Works while walking or sprinting.
- Preserves sprint after landing when Super Dash was started from a sprint.
- Movement input can remain physically held throughout the sequence.
- Supports keyboard/mouse and gamepad activation.
- Uses Borderlands 4's native Dash and Jump movement calls.
- No Windows SendInput or external macro software.
- Adjustable timing options.
- No normal per-activation console spam; the log is reserved for errors/abort diagnostics.

## Requirements

- Borderlands 4.
- [BL4 PythonSDK / Oak2 Mod Manager v0.3+ — latest stable release](https://github.com/bl-sdk/oak2-mod-manager/releases/latest).
- [Official BL4 SDK installation guide](https://bl-sdk.github.io/oak2-mod-db/).

Oak2 Mod Manager v0.3 already bundles the required **Mods Base 1.12**, **Console Mod Menu 1.6**, and **Keybinds 1.1** components. They do not need to be downloaded separately when using that release or a newer compatible Oak2 release.

## Installation

1. **Fully close Borderlands 4.**
2. If BL4 PythonSDK / Oak2 is not installed, or you want to update it, download the [latest stable Oak2 Mod Manager release](https://github.com/bl-sdk/oak2-mod-manager/releases/latest). Extract the SDK release directly into the **Borderlands 4 game folder** (the folder containing `OakGame`) and allow folders/files to merge. For the complete SDK procedure, including Proton/Linux notes, use the [official BL4 SDK installation guide](https://bl-sdk.github.io/oak2-mod-db/).
3. Start Borderlands 4 once after installing/updating the SDK. Press `~` twice to open the SDK console, type `mods`, and verify that the Mod Menu opens.
4. Download the latest **BL4 Super Dash** release from [GitHub Releases](https://github.com/Last1SiN/BL4-SuperDash/releases/latest) or [Nexus Mods](https://www.nexusmods.com/borderlands4/mods/289).
5. Fully close the game again and copy `BL4_SuperDash.sdkmod` **without extracting it** to:

   `Borderlands 4\sdk_mods\`

6. Start/restart Borderlands 4. Press `~` twice, type `mods`, open **BL4 Super Dash**, and enable the mod.
7. Bind **Super Dash** to the desired keyboard key, mouse button, or gamepad button in the mod settings.

To update BL4 Super Dash, replace the existing `BL4_SuperDash.sdkmod` with the newer file and restart the game.

Remove or disable older SuperDash test/probe builds before installing this release.

## Directional behavior

Version 1.2.0 captures the current movement vector at activation.

- Keyboard cardinal movement stays cardinal.
- Keyboard combinations such as W+D, S+D, S+A, and W+A produce diagonal Super Dash movement.
- Gamepad movement uses the current analog stick angle continuously, including angles between the usual 8 directions.
- Stick magnitude does not scale Super Dash power; only the movement angle is used.
- If the character is effectively stationary, Super Dash is performed forward relative to the current view, preserving the original standstill behavior.

Internally, BL4 exposes four native Dash directions. The mod starts the nearest native direction, then rotates only the horizontal dash velocity to the exact captured movement angle while preserving vertical velocity and the native dash speed.

## Configuration

Default values are the tested release settings and normally do not need to be changed.

- **Movement Neutral Frames:** 1
- **Jump Hold (ms):** 25
- **Jump Release -> Dash Release (ms):** 15
- **Dash Start Timeout (ms):** 300

## Sprint preservation

If Super Dash is activated while the character is already sprinting, the mod remembers the actual sprint state, performs the Super Dash sequence, waits for landing, and restores the game's normal sprint intent.

If Super Dash is started from normal movement, sprint is not forced on.

## Version 1.2.0

- Added directional Super Dash using the current movement direction.
- Added keyboard diagonal support.
- Added continuous analog gamepad direction support.
- Preserved forward fallback from a standstill.
- Replaced the experimental queued-impulse direction correction with direct horizontal velocity rotation, preventing accumulated velocity spikes and the camera-direction snap seen in the first directional prototype.
- Removed verbose per-activation debug/trace output from the SDK console; normal successful activations are not written to the mod log.
- Kept the existing timing behavior and sprint preservation.

## Compatibility and license

- Co-op support: **ClientSide** — tested with BL4 Super Dash installed only on the local player while the other co-op players did not have the mod installed.
- License: **GPL-3.0**

## Credits

- **Mod creator / code:** Sol (ChatGPT, GPT-5.6 Sol)
- **QA / maintainer:** [Last1SiN](https://github.com/Last1SiN)
- **BL4 PythonSDK / Oak2 Mod Manager:** created by [apple1417](https://github.com/apple1417), with contributions from the [BL-SDK](https://github.com/bl-sdk) project and contributors.
