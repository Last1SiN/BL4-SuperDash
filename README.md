# BL4 Super Dash

[English](README.md) | [Русский](README_RU.md)

BL4 Super Dash performs Borderlands 4's Super Dash movement technique with a single rebindable keyboard key, mouse button or gamepad button, in the direction you are currently moving.

The mod uses Borderlands 4's native Dash and Jump movement calls instead of external macro software.

## Features

- Super Dash with a single rebindable button.
- Multi-directional Super Dash based on current movement.
- Keyboard supports all 8 movement directions, including diagonals.
- Gamepad preserves the continuous analog angle of the left stick instead of reducing movement to fixed sectors.
- From a standstill, Super Dash falls back to forward relative to the current view.
- Works while walking or sprinting.
- Preserves sprint after landing when Super Dash was started from sprint.
- Movement input can remain physically held throughout the sequence.
- Uses Borderlands 4's native Dash and Jump calls.
- No Windows SendInput or external macro software.
- Normal successful activations do not spam the SDK console; the mod log is reserved for errors/abort diagnostics.

## Directional behavior

The mod captures the current movement direction when Super Dash is activated.

Keyboard supports forward, backward, left, right and all four diagonals. Gamepad movement uses the current analog stick angle continuously, including angles between the usual eight directions. Stick magnitude does not scale Super Dash power; only the movement angle is used.

If the character is effectively stationary, Super Dash is performed forward relative to the current view.

Internally, Borderlands 4 exposes four native Dash directions. BL4 Super Dash starts the nearest native direction and then rotates only the horizontal dash velocity to the captured movement angle, preserving vertical velocity and the native horizontal dash speed.

## Configuration

Default values are the tested release settings:

- **Movement Neutral Frames:** `1`
- **Jump Hold (ms):** `25`
- **Jump Release -> Dash Release (ms):** `15`
- **Dash Start Timeout (ms):** `300`

The defaults normally do not need to be changed.

## Requirements

- Borderlands 4
- [BL4 PythonSDK / Oak2 Mod Manager](https://github.com/bl-sdk/oak2-mod-manager/releases/latest)

Use the [official BL4 SDK / Oak2 installation guide](https://bl-sdk.github.io/oak2-mod-db/) for SDK installation and updates.

## Installing the mod

1. Install or update BL4 PythonSDK / Oak2 using the official guide above.
2. Download `BL4_SuperDash.sdkmod` from [GitHub Releases](https://github.com/Last1SiN/BL4-SuperDash/releases/latest) or [Nexus Mods](https://www.nexusmods.com/borderlands4/mods/289).
3. With Borderlands 4 closed, copy the `.sdkmod` file intact to `Borderlands 4\sdk_mods\`. Do not extract the `.sdkmod` itself.
4. Remove old SuperDash test/probe builds if present.
5. Start the game, open the Mods menu, enable **BL4 Super Dash** and bind **Super Dash** to the desired key or button.

To update BL4 Super Dash, replace the existing `.sdkmod` with the newer file and restart the game.

## Compatibility and license

- Co-op support: **Unknown** — behavior with the mod installed only on a client while the host does not have it has not yet been validated.
- The directional correction changes only horizontal dash direction while preserving native dash speed and vertical velocity.
- License: **GPL-3.0**

## Credits

**Development:** Sol / GPT-5.6 Sol  
**Design, testing & QA:** Last1SiN

**BL4 PythonSDK / Oak2 Mod Manager:** created by [apple1417](https://github.com/apple1417), with contributions from the [BL-SDK](https://github.com/bl-sdk) project and contributors.
