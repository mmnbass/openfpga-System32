# Sega System 32 for Analogue Pocket

An openFPGA core for the Analogue Pocket that runs Sega System 32 arcade games from one core:

| Game | Versions (MAME set) |
| --- | --- |
| **Golden Axe: The Revenge of Death Adder** | World, Rev B (`ga2`); US, Rev A (`ga2u`); Japan (`ga2j`) |
| **Spider-Man: The Videogame** | World (`spidman`); US, Rev A (`spidmanu`); Japan (`spidmanj`) |
| **SegaSonic the Hedgehog** | Japan, Rev C (`sonic`); Japan, prototype (`sonicp`) |

You choose the game at launch with its Game file, named by its full title and version, for example
`Golden Axe - The Revenge of Death Adder (US, Rev A).json`.

**Current release: v0.2.0.**

## Status

- **Every set, one and two players:** played on hardware (Analogue Pocket with the Dock).
- **Three or four players:** validated in simulation, not yet on hardware. Community testing is welcome.
- **Operator settings:** each game's own operator (test) menu is reachable from the core menu, so cabinet size and coin chutes
  (common or individual) can be set as on the original board; the core follows them. Every set starts with its own factory settings.
- **High scores:** kept between sessions in Spider-Man and SegaSonic the Hedgehog (Rev C). Golden Axe and the SegaSonic prototype
  keep no high-score table of their own.
- **Other System 32 games:** not currently supported.

## Installing

1. **Copy the core.** Unzip `mmnbass.System32_0.2.0.zip`, then copy the folders inside it (`Cores`, `Platforms`, `Assets`)
   onto the root of the SD card, **merging** them with the folders already there. Never replace the existing `Cores`,
   `Platforms` or `Assets` folders: that deletes your other cores and their files. On macOS, hold Option while you drag the
   folders onto the card and choose **Merge** (never **Replace**). On Windows and Linux, the file manager merges folders when
   you copy them; if it asks about individual files, replacing those is fine. The zip adds:

   - `Cores/mmnbass.System32/`
   - `Platforms/segas32.json` and `Platforms/_images/segas32.bin`
   - `Assets/segas32/common/`: the README and every set's configuration file (`<set>_u1cfg.bin`)
   - `Assets/segas32/mmnbass.System32/`: one Game file per set

2. **Make the ROM images** from your own MAME ROM sets. They are not included and never will be. You supply the set zips you
   have (`ga2.zip`, `ga2u.zip`, `ga2j.zip`, `spidman.zip`, `spidmanu.zip`, `spidmanj.zip`, `sonic.zip`, `sonicp.zip`); the core
   zip from step 1 already provides the configuration files; the plugin below (or `build_roms.py`) makes four ROM images per set.

   **Recommended: the [Sega System 32 ROMs plugin](https://github.com/mmnbass/pocket-plugin-System32)** for pupdate and
   Pocket Sync. It builds the images directly on the SD card, with the same checks as `build_roms.py` and byte for byte the
   same result, and needs no Python or terminal.

   1. Copy your set zips into `Assets/segas32/common/` on the SD card. A split regional set also needs its parent's zip
      (`ga2.zip` for Golden Axe, `spidman.zip` for Spider-Man).
   2. Install the plugin:
      - **pupdate:** open **Plugins**, choose **Install from GitHub...** and enter `mmnbass/pocket-plugin-System32`.
      - **Pocket Sync:** open **Plugins**, choose **Add Plugin** and enter `https://github.com/mmnbass/pocket-plugin-System32`.
   3. Run **Sega System 32 ROMs**. It writes the ROM images into `Assets/segas32/common/` and reports each game as written
      and verified, already installed and verified, or not built (with the missing parts listed). It writes only the ROM
      images: the configuration files come from the core zip, so leave them in place.

   **Manual alternative: `build_roms.py`.**

   1. Install Python 3.8 or newer from [python.org/downloads](https://www.python.org/downloads/) if you don't have it
      (on Windows, keep the option that installs the `py` launcher).
   2. Download `build_roms.py` from the release page. Put it in one folder together with your set zips (and, for a split
      regional set, its parent's zip).
   3. Open a terminal in that folder. On macOS, open Terminal, type `cd ` (with a space), drag the folder onto the window
      and press Return. On Windows, click the File Explorer address bar, type `cmd` and press Enter. On Linux, open a
      terminal in the folder. Then run, listing the zips you have:

      macOS / Linux:

      ```
      python3 build_roms.py --out roms ga2u.zip spidman.zip sonic.zip
      ```

      Windows:

      ```
      py build_roms.py --out roms ga2u.zip spidman.zip sonic.zip
      ```

   4. On the SD card, open **Assets**, then **segas32**, then **common**. Copy the `.bin` files from your `roms` folder (four
      per set) into that **common** folder, alongside `README.txt` and the `_u1cfg.bin` files. Not into `segas32` itself: the
      core only looks in `common`.

   - Merged, split and non-merged sets all work: every ROM part is found by its CRC32.
   - The tool checks every part and compares each finished image with the image tested on hardware.
   - It writes nothing for a game unless all four of that game's images are correct.
   - Missing or wrong parts are listed by name, size and CRC32.
   - It needs only Python's standard library: no other installs, and no copy of this repository.

3. **Play.** On the Pocket, open **Sega System 32** and pick a Game file. To change game, load another Game file from the core menu.

### Upgrading from v0.1.0

Merge the new zip's folders onto the card the same way (replacing individual files is fine). Then:

- **Remove the old Game files** `ga2u.json` and `spidman.json` from `Assets/segas32/mmnbass.System32/` if they are still there;
  otherwise those games are listed twice.
- **Keep your ROM images and saves.** The ROM image names are unchanged, so your images keep working. Saves are now named after
  the new Game files: to keep your v0.1.0 operator settings, rename them in `Saves/segas32/mmnbass.System32/`: `ga2u.sav` to
  `Golden Axe - The Revenge of Death Adder (US, Rev A).sav` and `spidman.sav` to `Spider-Man - The Videogame (World).sav`.
  (v0.1.0 saved settings only, no high scores.)

## Controls

| Pocket | Game |
| --- | --- |
| Select | Insert coin |
| Start | Start / join |
| A | Button 1 (Golden Axe: attack; SegaSonic: the button) |
| B | Button 2 (Golden Axe: jump) |
| X or Y | Button 3 (Golden Axe: magic) |
| D-pad | Move (SegaSonic: rolls the trackball) |
| L / R | Service / Test, while **Operator buttons** is on in the core menu |

- With the Dock, controllers 1-4 are players 1-4. Handheld play is player 1 only.
- The game pauses while the Pocket menu is open.
- **Operator menu:** core menu > Operator buttons on, then R (Test) on controller 1 opens the game's own test menu, where cabinet
  type and coin chutes are set. Turn Operator buttons off again for normal play.
- **Saves:** one per Game file: the game's operator settings and, for Spider-Man and SegaSonic (Rev C), its high-score table.
- Spider-Man may reassign existing credits when the cabinet size is changed with individual coin chutes, as the original arcade
  software does.

## Building from source

The release bitstream was built by the project's CI with Intel Quartus Prime Lite in the `raetro/quartus` Docker image (see
`experiments/V2-datapath-floor/toolchain.json`), seed 1.

The CPU microcode is pinned:

- `experiments/D2-pocket-top/reference/microcode_pin_rel_public.json` records the builder arguments and the SHA-256 of the three
  microcode images. The project generator refuses to run unless they rebuild exactly.
- It has the same builder, arguments and image hashes as the pin of the released build (`microcode_pin_rel.json`, kept for reference).
  Only its record of the source tree differs.

```
git clone --recursive https://github.com/mmnbass/openfpga-System32
cd openfpga-System32
git clone https://github.com/open-fpga/core-template build/D2REL/template && git -C build/D2REL/template checkout da3a021b1eaf742604d86d8dc9b33a6666263e6a
T=$PWD/build/D2REL/template/src/fpga
python3 experiments/D2-pocket-top/gen_d2.py build/D2REL/gen --template "$T" --config rel
python3 experiments/D2-pocket-top/pocket/prepare_quartus_d2.py build/D2REL/gen build/D2REL/seed1 --template "$T" --seed 1 \
        --microcode-pin experiments/D2-pocket-top/reference/microcode_pin_rel_public.json
python3 tools/verify_release_inputs.py      # prints RELEASE INPUTS IDENTICAL
# then compile build/D2REL/seed1/ga2_d2.qpf with Quartus Prime Lite, seed 1 (experiments/V2-datapath-floor/ci_quartus.sh)
```

`tools/verify_release_inputs.py` compares the prepared project with the released build's own configuration reference
(`experiments/D2-pocket-top/reference/d2_rel_seed1_config_reference.json`):

- **Identical:** all 185 source files, the 22 project files and the three microcode images.
- **Accepted differences:** only the documented ones, each checked by the script:
  - the public pin's name and hash (two checks);
  - one research log under `experiments/M1-functional/results/` with a local path removed;
  - `references/s32` is the history-free snapshot of upstream `accd644` (commit `2b9c265d`), without one diagnostic screenshot
    (`verif/modelsim_romboot/dump80.png`) that nothing builds or reads. The script checks its exact tree, so every other upstream
    file is byte-identical to `accd644`'s. Only the commit hash (in the gitlink, `UPSTREAM.lock` and the generated project's records)
    and the URL differ.
- **Paths:** the path `/home/runner/work/ga2-pocket-research/ga2-pocket-research` is the CI checkout root recorded in that reference.
  The script maps your checkout onto it before comparing.

To package a fitted bitstream: `experiments/U1-universal/pkg_rel/package_rel.sh <ga2_d2.rbf> <outdir>`. Then validate it with
`experiments/U1-universal/pkg_rel/validate_pkg.py <outdir>/stage --rbf <ga2_d2.rbf>`.

The tree keeps the research layout (`experiments/...`) of the project it comes from. Only the files the release build, its checks and
its packaging use are included.

## Credits and licence

- **Licence:** GPL-3.0-or-later (`LICENSE`).
- **Upstream:** the System 32 hardware model is based on the Sega System 32 MiSTer core by Meathax (`references/s32`, pinned and
  unmodified, GPL-3.0). It includes s80x86 (GPL-3.0), JT12 and T80.
- **Third-party notices:** `THIRD_PARTY_NOTICES.md`.
- **Thanks:** Special thanks to Jade for helping test multiplayer!
  Thanks also to the MAME project for reference behavior and ROM set definitions.
- **AI assistance:** Claude Code (Anthropic) wrote most of this project's code and documentation under the direction of mmnbass;
  OpenAI Codex served as an independent, read-only reviewer at development checkpoints. Project direction, design decisions, and
  hardware validation were led by mmnbass. The underlying System 32 hardware model is based on Meathax's work (see Upstream).

## Disclaimer

This is an independent, community-developed project. It is not affiliated with, endorsed by or sponsored by Sega, Marvel, Analogue or
their respective rights holders.

- **Trademarks:** Sega, System 32, Golden Axe and SegaSonic are trademarks of Sega Corporation. Spider-Man is a trademark of Marvel. Analogue and
  Analogue Pocket are trademarks of Analogue Enterprises Limited. All trademarks belong to their respective owners and are used here
  only to identify the hardware and games.
- **No ROMs:** This repository and its releases do not include copyrighted game ROMs, BIOS files or game data. Users are responsible for obtaining and using ROM images in accordance with applicable law.
