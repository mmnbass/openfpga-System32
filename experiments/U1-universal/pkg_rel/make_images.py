#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the System32 (release/s32-v1) icon and platform image from simple original shapes (a 5x7 pixel font of our own and a
four-cell 'status band' motif). No third-party art.
Format = the working packages' (pocket_jaguar pkg/pocket, HW-001): 16 bits per pixel, the level in the low byte, the high
byte 0, COLUMN-major (all y of x = 0, then x = 1, ...), y = 0 at the top; 255 = white, 0 = black.
icon 36x36 = 2,592 bytes; platform image 521x165 = 171,930 bytes.
Usage: make_images.py <icon.bin out> <platform.bin out>"""
import sys
from pathlib import Path

G = {
 "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."],
 "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
 "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
 "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
 "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
 "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
 "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
 "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
 "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],          # SM1: added glyph (our own 5x7 shape)
 "3": ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],          # U1: added glyphs (our own 5x7 shapes)
 "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
 "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
 "V": ["#...#", "#...#", "#...#", "#...#", ".#.#.", ".#.#.", "..#.."],
 "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],          # rel: added glyphs (our own 5x7 shapes)
 "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
 "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
}

class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [[255] * h for _ in range(w)]          # px[x][y]
    def rect(self, x0, y0, x1, y1, v=0):                 # x1, y1 exclusive
        for x in range(max(x0, 0), min(x1, self.w)):
            for y in range(max(y0, 0), min(y1, self.h)):
                self.px[x][y] = v
    def frame(self, t):
        self.rect(0, 0, self.w, t); self.rect(0, self.h - t, self.w, self.h)
        self.rect(0, 0, t, self.h); self.rect(self.w - t, 0, self.w, self.h)
    def text(self, s, x, y, scale, gap):
        for ch in s:
            if ch != " ":
                for r, row in enumerate(G[ch]):
                    for c, k in enumerate(row):
                        if k == "#":
                            self.rect(x + c * scale, y + r * scale, x + (c + 1) * scale, y + (r + 1) * scale)
            x += 5 * scale + gap
        return x
    def band(self, x, y, cell, n, step):                  # n small squares, the band motif (alternating fill)
        for i in range(n):
            if i % 2 == 0: self.rect(x + i * step, y, x + i * step + cell, y + cell)
            else:          # outline only
                self.rect(x + i * step, y, x + i * step + cell, y + cell)
                self.rect(x + i * step + 1, y + 1, x + i * step + cell - 1, y + cell - 1, 255)
    def bytes(self):
        out = bytearray()
        for x in reversed(range(self.w)):          # APF images are stored rotated 90 degrees: columns right to left (as the openFPGA
            for y in range(self.h):                # library's updater and Analogue-convention images such as openfpga-Jaguar's; v0.1 fix)
                out += bytes([self.px[x][y], 0])
        return bytes(out)

def icon():
    c = Canvas(36, 36); c.frame(2)
    c.text("S32", 3, 8, 2, 1)                  # 3 glyphs: 3*10 + 2*1 = 32 wide
    c.band(4, 26, 4, 7, 4 + 0)                 # 7 small cells, 4 px pitch (touching squares/outlines)
    return c.bytes()

def platform():
    c = Canvas(521, 165); c.frame(6)
    c.text("SYSTEM 32", 40, 30, 8, 8)            # GA2Play1: 8 glyphs incl. space = 8*48 = 384 + 60 < 521; was 7 glyphs + a space at 40 px + 8 gap: ends well inside 521
    c.band(60, 120, 20, 16, 28)                # a row of 16 cells, like the status band
    return c.bytes()

if __name__ == "__main__":
    if len(sys.argv) != 3: sys.exit(__doc__)
    i, p = icon(), platform()
    assert len(i) == 2592 and len(p) == 171930
    Path(sys.argv[1]).write_bytes(i); Path(sys.argv[2]).write_bytes(p)
    print("icon", len(i), "platform", len(p))
