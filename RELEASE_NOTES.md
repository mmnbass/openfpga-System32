# Sega System 32 for Analogue Pocket — v0.1.0

Initial public release.

- **One core, two games:** Golden Axe: The Revenge of Death Adder (US) and Spider-Man: The Video Game (World). Pick a game at launch with its Game file (`ga2u.json` / `spidman.json`); switch from the core menu.
- **Verified on hardware:** two-player play of both games, with complete playthroughs and no observed errors.
- **Not yet validated:** three- and four-player play.
- **Sound and pause:** music and sound effects. The game pauses while the Pocket menu is open.
- **Saves:** each game's arcade settings are kept between sessions, one save per game. High scores are not saved.
- **ROMs:** not included. Make them from your own MAME sets with `build_roms.py` (attached to this release; see the README), which checks every part and every finished image.

Files:

- `mmnbass.System32_0.1.0.zip`: the core. Unzip it and merge its folders onto the SD card root; never replace existing folders.
- `build_roms.py`: the ROM image builder (Python 3.8+, standard library only).

Bitstream built by CI with Quartus Prime Lite, seed 1.

| File | SHA-256 |
| --- | --- |
| `ga2_d2.rbf` | `0d222f75ab5401835af845450707c81023376d7820bd1a65b1c85569e0faaf46` |
| zip | `08eb3e2080273b84d8f32896f5c837a6c6345fa2f72fe7401246bc53ab3004b6` |
| `build_roms.py` | `96e0ef59c6dfd16ae59212dd19e3662584306a69dc4fb082e6784c4716ba65dd` |

Special thanks to Jade for helping test multiplayer!
