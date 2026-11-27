Sega System 32 for Analogue Pocket (mmnbass.System32), v0.2.0: put your own ROM files here. No ROM files are supplied with this core.
Each set has its own files, named by its Game file in Assets/segas32/mmnbass.System32/. Every set below is verified on hardware
unless marked.
  Golden Axe: The Revenge of Death Adder (World, Rev B) (MAME set ga2), Game file "Golden Axe - The Revenge of Death Adder (World, Rev B).json":
    ga2_main.bin           2,097,152 bytes   program ROM image
    ga2_tiles.bin          4,194,304 bytes   tile ROM image
    ga2_sprites.bin       16,777,216 bytes   sprite ROM image
    ga2_sound.bin          4,194,304 bytes   sound ROM image
  Golden Axe: The Revenge of Death Adder (US, Rev A) (MAME set ga2u), Game file "Golden Axe - The Revenge of Death Adder (US, Rev A).json":
    ga2u_main.bin          2,097,152 bytes   program ROM image
    ga2u_tiles.bin         4,194,304 bytes   tile ROM image
    ga2u_sprites.bin      16,777,216 bytes   sprite ROM image
    ga2u_sound.bin         4,194,304 bytes   sound ROM image
  Golden Axe: The Revenge of Death Adder (Japan) (MAME set ga2j), Game file "Golden Axe - The Revenge of Death Adder (Japan).json":
    ga2j_main.bin          2,097,152 bytes   program ROM image
    ga2j_tiles.bin         4,194,304 bytes   tile ROM image
    ga2j_sprites.bin      16,777,216 bytes   sprite ROM image
    ga2j_sound.bin         4,194,304 bytes   sound ROM image
  Spider-Man: The Videogame (World) (MAME set spidman), Game file "Spider-Man - The Videogame (World).json":
    spidman_main.bin       2,097,152 bytes   program ROM image
    spidman_tiles.bin      4,194,304 bytes   tile ROM image
    spidman_sprites.bin    8,388,608 bytes   sprite ROM image
    spidman_sound.bin      4,194,304 bytes   sound ROM image
  Spider-Man: The Videogame (US, Rev A) (MAME set spidmanu), Game file "Spider-Man - The Videogame (US, Rev A).json":
    spidmanu_main.bin      2,097,152 bytes   program ROM image
    spidmanu_tiles.bin     4,194,304 bytes   tile ROM image
    spidmanu_sprites.bin   8,388,608 bytes   sprite ROM image
    spidmanu_sound.bin     4,194,304 bytes   sound ROM image
  Spider-Man: The Videogame (Japan) (MAME set spidmanj), Game file "Spider-Man - The Videogame (Japan).json":
    spidmanj_main.bin      2,097,152 bytes   program ROM image
    spidmanj_tiles.bin     4,194,304 bytes   tile ROM image
    spidmanj_sprites.bin   8,388,608 bytes   sprite ROM image
    spidmanj_sound.bin     4,194,304 bytes   sound ROM image
  SegaSonic The Hedgehog (Japan, rev. C) (MAME set sonic), Game file "SegaSonic The Hedgehog (Japan, rev. C).json":
    sonic_main.bin         2,097,152 bytes   program ROM image
    sonic_tiles.bin        4,194,304 bytes   tile ROM image
    sonic_sprites.bin     16,777,216 bytes   sprite ROM image
    sonic_sound.bin        4,194,304 bytes   sound ROM image
  SegaSonic The Hedgehog (Japan, prototype) (MAME set sonicp), Game file "SegaSonic The Hedgehog (Japan, prototype).json":
    sonicp_main.bin        2,097,152 bytes   program ROM image
    sonicp_tiles.bin       4,194,304 bytes   tile ROM image
    sonicp_sprites.bin     8,388,608 bytes   sprite ROM image
    sonicp_sound.bin       4,194,304 bytes   sound ROM image
  The regional sets of a game share its tile, sprite and sound images (same ROM parts); each set still has its own four files,
  its own configuration file and its own save.
  Optional: spidman_eeprom.bin / spidmanu_eeprom.bin / spidmanj_eeprom.bin (128 bytes), Spider-Man's factory-default EEPROM for
  that set, used only when it has no save yet. Without it the game writes its own regional defaults on first start.
Make the images from your own legally obtained MAME ROM sets with the System 32 ROM plugin (pupdate / Pocket Sync) or build_roms.py
(https://github.com/mmnbass/openfpga-System32):
  python3 build_roms.py --out <SD card>/Assets/segas32/common <your set zips>
It finds every ROM part by its CRC32 (merged, split or non-merged sets; a split regional set also needs its parent zip), stops with a
clear message naming any missing or wrong part, and checks every finished image against the SHA-256 of the image tested on hardware.
Included (no ROM data): <set>_u1cfg.bin for every set above, the 64-byte per-game configuration files.

Players and controls: with the Analogue Dock, controllers 1-4 = players 1-4; handheld = player 1 only. The number of players and
the coin slots are the game's own operator settings (see "Players and coin slots" below).
Select = coin, Start = start / join, D-pad = move, A = attack, B = jump, X or Y = magic (Golden Axe). SegaSonic: the D-pad rolls the
trackball, A is the button.

Saves: written by the Pocket on exit, one per Game file, named after it ("Golden Axe - The Revenge of Death Adder (US, Rev A).sav"),
1,164 bytes: the game's settings (EEPROM) and the high-score table (Spider-Man; SegaSonic rev. C: TOP 15 ranking; Golden Axe and
the SegaSonic prototype keep none). Older 128-byte saves still load. A save belongs to ONE set: do not copy or rename a save
from one regional set to another (the settings differ between regions; with the World settings the US Spider-Man accepts no
coins). A set with no save starts from its own defaults.

Players and coin slots: every set starts with its own factory settings, exactly as the arcade board did; the core changes
nothing. To play with more players, or with one coin slot per player, change the game's own settings in its operator (test) menu.
They are kept in the game's save, so they stay set for that set only.
  Opening the menu: core menu > Operator buttons (on). Then on controller 1: R = TEST, L = SERVICE. Turn Operator buttons off
  again for normal play.
  Controllers: player N uses controller N (Dock with game controllers). Select = insert coin, Start = start.
  The coin slots follow the game's COIN CHUTE TYPE setting: COMMON = shared slots, INDIVIDUAL = one slot (and one credit counter)
  per player.
  Golden Axe - The Revenge of Death Adder (World, Japan: factory 2 players, COMMON; US: factory 4 players, INDIVIDUAL)
    R opens the test menu; L moves the cursor, R selects. GAME ASSIGNMENTS > CABINET TYPE: 2P / 3P / 4P.
    COIN ASSIGNMENTS > COIN CHUTE TYPE: COMMON / INDIVIDUAL. Then EXIT, and EXIT again to return to the game.
  Spider-Man - The Videogame (all sets: factory 4 players; World, Japan COMMON, US INDIVIDUAL)
    R opens the test menu; L moves the cursor, R selects. GAME ASSIGNMENTS > CABINET TYPE: 2P / 3P / 4P.
    COIN ASSIGNMENTS > COIN CHUTE TYPE: COMMON / INDIVIDUAL. Credits that exist when you change the cabinet size follow the
    game's coin slots, so they can show under another player or disappear (as on the original board).
  SegaSonic The Hedgehog (Japan, rev. C) (factory 2 players, COMMON)
    For 3 players: R opens the test menu; L moves the cursor, R selects. GAME ASSIGNMENT > CABINET TYPE: 3P.
    COIN ASSIGNMENTS > COIN CHUTE TYPE: COMMON / INDIVIDUAL (with COMMON, player 3's coins go into the shared slot).
  SegaSonic The Hedgehog (Japan, prototype)
    The prototype's menu has no cabinet or coin settings (GAME / COIN ASSIGNMENT are unfinished in it and open BACKUP RAM CLEAR:
    choose NO). Its menu is reversed: TEST moves the cursor, SERVICE selects. As shipped it plays with three players.
  High scores are kept through a visit to the operator menu; the game's own clear (SegaSonic: BACKUP DATA CLEAR) still clears them.
  Three and four players are validated in simulation and not yet on hardware; community testing is welcome.
  Known limitation: after leaving the operator menu, wait a moment before opening the Pocket menu. Opening it during the
  game's restart (about the first second) can make the game lose the saved high scores and keep its factory table.

Updating from v0.1.0 (Game files are now named by the full title):
  - delete the old Game files ga2u.json and spidman.json from Assets/segas32/mmnbass.System32/, or those games are listed twice;
  - to keep your settings, rename your old saves (Saves/segas32/mmnbass.System32/ on the SD card):
      ga2u.sav     ->  Golden Axe - The Revenge of Death Adder (US, Rev A).sav
      spidman.sav  ->  Spider-Man - The Videogame (World).sav
  ROM image names (ga2u_*.bin, spidman_*.bin) and configuration files are unchanged: your existing images keep working.
