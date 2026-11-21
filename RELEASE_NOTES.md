# Sega System 32 for Analogue Pocket — v0.1.0

The initial public release of Sega System 32 for the Analogue Pocket! This core brings two Sega arcade games to openFPGA through a single System 32 hardware implementation.

## Supported Games

- Golden Axe: The Revenge of Death Adder (US)
- Spider-Man: The Video Game (World)

Select a game using `ga2u.json` or `spidman.json`. You can switch between games through the core menu without restarting the core.

## Features and Compatibility

- Both games verified on real Analogue Pocket hardware, including complete two-player playthroughs without observed errors.
- Music and sound effects supported.
- Pause functionality through the Pocket menu.
- Separate persistent arcade settings for each game.
- Analogue Dock support for up to four players; three- and four-player gameplay has not yet been hardware-tested.

**Known limitation:** High scores are not saved.

## Installation

1. Download `mmnbass.System32_0.1.0.zip` and extract it onto your Analogue Pocket SD card, merging the included folders rather than replacing existing folders.
2. Download `build_roms.py` from this release.
3. Follow the [README](https://github.com/mmnbass/openfpga-System32#readme) for the exact commands to prepare ROM images from your own MAME sets and copy them into `Assets/segas32/common/`.

ROMs are not included. The ROM builder requires Python 3.8 or newer, uses only the standard library, and validates the ROM inputs and generated images.

## Source Code and Credits

The complete corresponding source is available in this repository and in the attached `openfpga-System32-0.1.0-source.tar.gz` archive. For build instructions, see the README. The build process verifies its source inputs against the release reference.

This project is licensed under GPL-3.0-or-later, subject to the applicable third-party license notices.

The System 32 hardware implementation builds on Meathax's MiSTer System 32 project, with additional upstream components and contributors acknowledged in the repository's third-party notices.

Special thanks to Jade for helping test multiplayer!

Thanks also to the MAME project for reference behavior and ROM set definitions.

Built with AI assistance: Claude Code (Anthropic) wrote most of this project's code and documentation under the direction of mmnbass; OpenAI Codex served as an independent, read-only reviewer at development checkpoints. Project direction, design decisions, and hardware validation were led by mmnbass.

## Release Files and Checksums

- `mmnbass.System32_0.1.0.zip` — Installable Analogue Pocket core.
- `build_roms.py` — Standalone ROM image builder.
- `openfpga-System32-0.1.0-source.tar.gz` — Complete corresponding source, including the upstream snapshot.

| File | SHA-256 |
| --- | --- |
| `mmnbass.System32_0.1.0.zip` | `6df0860c85a18f9ade80f5ddfa02ac66d112edda9adb15a1f16e98ac4a077a6c` |
| `build_roms.py` | `96e0ef59c6dfd16ae59212dd19e3662584306a69dc4fb082e6784c4716ba65dd` |

The source archive is made from this tree, so its checksum is listed on the release page.

Fitted bitstream (not a separate download): the original `ga2_d2.rbf`, before bit reversal and packaging as `Cores/mmnbass.System32/bitstream.rbf_r` in the zip, has SHA-256 `0d222f75ab5401835af845450707c81023376d7820bd1a65b1c85569e0faaf46`. It was built by CI with Quartus Prime Lite, seed 1.
