"""StarDex - a pocket constellation dex.

Structure borrowed from the community archive: sunny0826's "Offline Pokedex"
browses an embedded dataset with a sprite, stats and a cry, and remembers what
you have already seen. The dataset here is the twelve zodiac constellations; the
charts are drawn as star dots plus connecting lines, and the "cry" is a short
tone motif derived from the entry number.

Honest about the data: the star patterns are **stylized**, not to scale or to
real coordinates. The names, abbreviations, brightest stars, areas and best
months are the real ones.

Keys:  UP / DOWN  previous / next entry
       OK         play the entry's tone motif
"""

TITLE = "StarDex"

BG = 0x0000
BAR = 0x18E3
DEX_BG = 0x0208
DEX_EDGE = 0x05E0
INK = 0xFFFF
DIM = 0x8410
ACCENT = 0x07FF
GOLD = 0xFFE0
SEEN = 0x07E0
UNSEEN = 0x39E7

# name, abbrev, brightest star, area (sq deg), best month, stars, links
DEX = (
    ("Aries", "Ari", "Hamal", 441, "Dec",
     ((15, 40), (40, 60), (62, 45), (85, 72)), ((0, 1), (1, 2), (2, 3))),
    ("Taurus", "Tau", "Aldebaran", 797, "Jan",
     ((20, 25), (45, 45), (70, 40), (50, 70), (80, 60), (55, 85)),
     ((0, 1), (1, 2), (1, 3), (3, 4), (3, 5))),
    ("Gemini", "Gem", "Pollux", 514, "Feb",
     ((25, 15), (30, 45), (35, 80), (70, 20), (75, 55), (80, 85)),
     ((0, 1), (1, 2), (3, 4), (4, 5), (1, 4))),
    ("Cancer", "Cnc", "Al Tarf", 506, "Mar",
     ((30, 35), (55, 50), (75, 35), (60, 75)), ((0, 1), (1, 2), (1, 3))),
    ("Leo", "Leo", "Regulus", 947, "Apr",
     ((15, 60), (35, 40), (55, 50), (75, 30), (88, 55), (60, 80)),
     ((0, 1), (1, 2), (2, 3), (2, 4), (2, 5))),
    ("Virgo", "Vir", "Spica", 1294, "May",
     ((15, 35), (40, 45), (60, 60), (80, 40), (70, 80), (45, 80)),
     ((0, 1), (1, 2), (2, 3), (2, 4), (4, 5))),
    ("Libra", "Lib", "Zubeneschamali", 538, "Jun",
     ((25, 30), (50, 45), (75, 30), (60, 75)), ((0, 1), (1, 2), (1, 3))),
    ("Scorpius", "Sco", "Antares", 497, "Jul",
     ((10, 25), (30, 35), (45, 50), (55, 70), (70, 80), (85, 65)),
     ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5))),
    ("Sagittarius", "Sgr", "Kaus Australis", 867, "Aug",
     ((20, 70), (40, 50), (60, 60), (75, 35), (85, 60), (55, 85)),
     ((0, 1), (1, 2), (2, 3), (2, 4), (0, 5))),
    ("Capricornus", "Cap", "Deneb Algedi", 398, "Sep",
     ((15, 40), (40, 55), (65, 40), (85, 60), (55, 80)),
     ((0, 1), (1, 2), (2, 3), (1, 4))),
    ("Aquarius", "Aqr", "Sadalsuud", 980, "Oct",
     ((15, 50), (35, 35), (55, 50), (75, 35), (88, 55), (50, 80)),
     ((0, 1), (1, 2), (2, 3), (3, 4), (2, 5))),
    ("Pisces", "Psc", "Eta Piscium", 889, "Nov",
     ((20, 30), (45, 50), (70, 30), (85, 55), (50, 80)),
     ((0, 1), (1, 2), (2, 3), (1, 4))),
)

CH_X = 10
CH_Y = 40
CH_W = 220
CH_H = 150
PAD = 6

# a short motif per entry: (semitone offset, ms)
MOTIF = ((0, 120), (4, 120), (7, 180), (12, 220))
BASE_HZ = 262


def star_xy(nx, ny):
    x = CH_X + PAD + nx * (CH_W - 2 * PAD) // 100
    y = CH_Y + PAD + ny * (CH_H - 2 * PAD) // 100
    return x, y


def draw_line(lcd, x0, y0, x1, y1, color):
    """Dotted line: a few 2x2 dots along the segment. Cheap and looks right."""
    dx = x1 - x0
    dy = y1 - y0
    steps = max(abs(dx), abs(dy))
    if steps == 0:
        return
    n = steps // 7 + 1
    for i in range(n + 1):
        t = i * 1000 // n if n else 0
        x = x0 + dx * t // 1000
        y = y0 + dy * t // 1000
        lcd.fill_rect(x, y, 2, 2, color)


def draw_chart(ctx, idx):
    lcd = ctx.lcd
    name, abbr, bright, area, month, stars, links = DEX[idx]
    lcd.fill_rect(CH_X, CH_Y, CH_W, CH_H, DEX_BG)
    lcd.rect(CH_X - 1, CH_Y - 1, CH_W + 2, CH_H + 2, DEX_EDGE)

    pts = []
    for nx, ny in stars:
        pts.append(star_xy(nx, ny))
    for a, b in links:
        draw_line(lcd, pts[a][0], pts[a][1], pts[b][0], pts[b][1], DEX_EDGE)
    for i, (x, y) in enumerate(pts):
        s = 4 if i == 0 else 3          # the brightest star is the first point
        lcd.fill_rect(x - s // 2, y - s // 2, s, s, GOLD if i == 0 else INK)


def draw(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, 18, BAR)
    lcd.text("STAR DEX", 6, 5, ACCENT, BAR)
    lcd.text("%d/%d" % (ctx.idx + 1, len(DEX)), 186, 5, UNSEEN, BAR)

    entry = DEX[ctx.idx]
    lcd.text("%02d %s" % (ctx.idx + 1, entry[0]), 10, 22, INK, BG)
    lcd.text(entry[1], 200, 22, DIM, BG)

    draw_chart(ctx, ctx.idx)

    lcd.text("brightest  %s" % entry[2][:20], 10, 198, GOLD, BG)
    lcd.text("area       %d sq deg" % entry[3], 10, 212, DIM, BG)
    lcd.text("best month %s" % entry[4], 10, 226, DIM, BG)

    lcd.progress(10, 244, 220, 10, ctx.seen * 100 // len(DEX), SEEN, 0x2104)
    lcd.text("seen %d/%d" % (ctx.seen, len(DEX)), 10, 258, SEEN, BG)
    lcd.text("spins %d" % ctx.plays, 150, 258, DIM, BG)

    lcd.text("BAT %s" % ctx.battery.label(), 10, 284, DIM, BG)
    lcd.text("UP/DN browse   OK sound", 10, 298, UNSEEN, BG)
    lcd.text("long OK = back", 10, 310, BAR, BG)


def mark_seen(ctx):
    """seen is a 12-char "0"/"1" string, the same trick beats.py uses."""
    if ctx.seen_flags[ctx.idx] == "0":
        f = list(ctx.seen_flags)
        f[ctx.idx] = "1"
        ctx.seen_flags = "".join(f)
        ctx.seen = ctx.seen_flags.count("1")
        ctx.dirty = True


def play_motif(ctx):
    if not (ctx.audio and ctx.audio.ok):
        return
    try:
        for semis, ms in MOTIF:
            hz = BASE_HZ * (2 ** (semis / 12.0))
            ctx.audio.tone(int(hz), ms)
    except Exception as exc:                              # noqa: BLE001
        ctx.log("tone: %s" % exc)


def setup(ctx):
    ctx.idx = int(ctx.kv_get("i", 0)) % len(DEX)
    flags = str(ctx.kv_get("seen", "0" * len(DEX)))
    if len(flags) != len(DEX) or any(c not in "01" for c in flags):
        flags = "0" * len(DEX)
    ctx.seen_flags = flags
    ctx.seen = flags.count("1")
    ctx.plays = int(ctx.kv_get("p", 0))
    ctx.dirty = False
    if ctx.audio and ctx.audio.ok:
        ctx.audio.set_volume(70)
    mark_seen(ctx)
    ctx.log("stardex %s seen=%d" % (DEX[ctx.idx][0], ctx.seen))
    draw(ctx)


def on_key(ctx, key):
    if key == "up":
        ctx.idx = (ctx.idx - 1) % len(DEX)
        mark_seen(ctx)
    elif key == "down":
        ctx.idx = (ctx.idx + 1) % len(DEX)
        mark_seen(ctx)
    elif key == "ok":
        ctx.plays += 1
        ctx.dirty = True
        play_motif(ctx)
    draw(ctx)


def teardown(ctx):
    if ctx.dirty:
        ctx.kv_set("i", ctx.idx)
        ctx.kv_set("seen", ctx.seen_flags)
        ctx.kv_set("p", ctx.plays)
        ctx.kv_flush()
