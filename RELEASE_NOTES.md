# Sega System 32 — v0.2.0

This release introduces SegaSonic the Hedgehog, expanded regional ROM support, persistent high scores, and configurable arcade
cabinet settings.

## Supported games

Golden Axe: The Revenge of Death Adder

* World (Rev B) — New
* US (Rev A)
* Japan — New

Spider-Man: The Videogame

* World
* US (Rev A) — New
* Japan — New

SegaSonic the Hedgehog

* Japan (Rev C) — New
* Japan (Prototype) — New

## New features

* Persistent high scores in Spider-Man and SegaSonic the Hedgehog (Rev C).
* Operator menu access, including configurable cabinet sizes and common or individual coin chutes.
* Expanded multiplayer support for native cabinet configurations.
* Persistent operator settings, with original regional factory defaults preserved.
* D-pad support for SegaSonic's original trackball controls.
* Full game titles and regional information in the game selection menu.
* Updated ROM builder and plugin supporting all eight ROM sets.

## Core improvements

* Expanded V60 CPU instruction support.
* SegaSonic protection hardware implementation.
* Improved sprite priority handling.
* Improved video handling for different native display widths.
* Cabinet-aware coin and controller routing.
* Improved work RAM initialization.
* High-score preservation across ordinary operator menu visits.

## Important: Upgrading from v0.1.0

Game instance filenames have changed to use full game titles and regional information.

If upgrading from v0.1.0, remove the following obsolete game instance files if they are still present after updating:

* `ga2u.json`
* `spidman.json`

This prevents duplicate entries from appearing in the game selection menu. Do not delete your ROM images or save files.


## Additional notes

* Three- and four-player configurations have been validated in simulation but have not yet been tested on physical hardware.
  Community testing is welcome.
* Spider-Man may reassign existing credits when changing cabinet size in individual coin mode. This is consistent with the
  original arcade software.

Enjoy!
