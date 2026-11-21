Sega System 32 for Analogue Pocket (mmnbass.System32): put your own ROM files here. No ROM files are supplied with this core.
Each game has its own files, named by its Game file in Assets/segas32/mmnbass.System32/:
  Golden Axe: The Revenge of Death Adder (US, MAME set ga2u), Game file ga2u.json:
    ga2u_main.bin        2,097,152 bytes   program ROM image
    ga2u_tiles.bin       4,194,304 bytes   tile ROM image
    ga2u_sprites.bin    16,777,216 bytes   sprite ROM image
    ga2u_sound.bin       4,194,304 bytes   sound ROM image
  Spider-Man: The Video Game (World, MAME set spidman), Game file spidman.json:
    spidman_main.bin     2,097,152 bytes   program ROM image
    spidman_tiles.bin    4,194,304 bytes   tile ROM image
    spidman_sprites.bin  8,388,608 bytes   sprite ROM image
    spidman_sound.bin    4,194,304 bytes   sound ROM image
  Optional: spidman_eeprom.bin (128 bytes), Spider-Man's factory-default EEPROM, used only when Spider-Man has no save yet.
Make the images from your own legally obtained MAME ROM sets with build_roms.py (https://github.com/mmnbass/openfpga-System32):
  python3 build_roms.py --out <SD card>/Assets/segas32/common ga2u.zip [ga2.zip] spidman.zip
It finds every ROM part by its CRC32 (merged, split or non-merged sets; a split ga2u set also needs its parent ga2.zip), stops with a
clear message naming any missing or wrong part, and checks each finished image against its known SHA-256.
Also in this folder (supplied with the core, no ROM data): ga2u_u1cfg.bin and spidman_u1cfg.bin, small per-game settings files the core needs. Don't delete them.
Saves: written by the Pocket on exit, one per game, named after the Game file (ga2u.sav, spidman.sav).
