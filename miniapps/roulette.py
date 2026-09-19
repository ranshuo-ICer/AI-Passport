"""SpinWheel - hold a key to spin, let go to land on the answer.

Idea from the community archive: shinku-chen's "What to Eat Today" turns the
Passport into a food roulette (hold to cycle, release to stop). The screen design
here is ours; the interaction is the part worth copying, and it fits our three
keys unusually well.

Two wheels instead of one, so the toy settles both "what to eat" and "what to
do". Both keep their last landing across a power cycle.

Keys:  UP (hold)    spin the EAT wheel; let go to land
       DOWN (hold)  spin the DO wheel; let go to land
       OK           clear both results for a fresh round
"""

TITLE = "SpinWheel"

BG = 0x0000
BAR = 0x18E3
INK = 0xFFFF
DIM = 0x8410
ACCENT = 0x07FF
CARD = 0x0841
CARD_ON = 0x0410
EDGE = 0x39E7
EAT_C = 0xFD20
DO_C = 0x07E0
IDLE_C = 0x39E7
GOLD_C = 0xFFE0
UNSEEN = 0x5ACB

EAT = ("Noodles", "Rice", "Hotpot", "BBQ", "Sushi", "Dumplings",
       "Burger", "Pizza", "Salad", "Curry")

DO = ("Walk", "Movie", "Game", "Nap", "Read", "Gym",
      "Coffee", "Music", "Code", "Clean")

SPIN_DIV = 2                 # 50 Hz loop -> 25 spins per second

CARD_A_Y = 28
CARD_B_Y = 158
CARD_H = 118


def card_xy(which):
    return (16, CARD_A_Y if which == 0 else CARD_B_Y)


def draw_card(ctx, which):
    """Only the card that changed: the wheel spins at 25 fps."""
    lcd = ctx.lcd
    x, y = card_xy(which)
    items = EAT if which == 0 else DO
    idx = ctx.a if which == 0 else ctx.b
    landed = ctx.done[which]
    accent = EAT_C if which == 0 else DO_C

    lcd.fill_rect(x, y, 208, CARD_H, CARD_ON if landed else CARD)
    lcd.rect(x, y, 208, CARD_H, accent if landed else EDGE)

    lcd.text("EAT" if which == 0 else "DO", x + 8, y + 8, accent, 
             CARD_ON if landed else CARD)

    bgc = CARD_ON if landed else CARD
    hint = "hold %s" % ("UP" if which == 0 else "DN")
    if ctx.spin[which]:
        lcd.text("> > >", x + 158, y + 8, GOLD_C, bgc)
    else:
        # keep the hint visible: the cleared state must still say how to play
        lcd.text(hint, x + 128, y + 8, IDLE_C if landed else UNSEEN, bgc)

    name = items[idx]
    lcd.text_center(name, y + 42, INK if landed else IDLE_C,
                    CARD_ON if landed else CARD, 2)
    lcd.text_center("%d/%d" % (idx + 1, len(items)), y + 74,
                    IDLE_C, CARD_ON if landed else CARD, 1)
    if landed:
        lcd.text_center("LOCKED IN", y + 90, accent, CARD_ON, 1)


def draw(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, 18, BAR)
    lcd.text("SPIN WHEEL", 6, 5, ACCENT, BAR)
    lcd.text("SPINS %d" % ctx.spins, 150, 5, IDLE_C, BAR)
    draw_card(ctx, 0)
    draw_card(ctx, 1)
    lcd.text("BAT %s" % ctx.battery.label(), 16, 284, DIM, BG)
    lcd.text("hold UP/DN to spin", 16, 298, EDGE, BG)
    lcd.text("OK clear  long OK back", 16, 310, BAR, BG)


def land(ctx, which):
    ctx.spin[which] = False
    ctx.done[which] = True
    ctx.spins += 1
    ctx.dirty = True
    ctx.kv_set("a" if which == 0 else "b",
               ctx.a if which == 0 else ctx.b)
    ctx.kv_set("s", ctx.spins)
    ctx.kv_flush()
    draw_card(ctx, which)
    if ctx.audio and ctx.audio.ok:
        # two notes: "here it is"
        try:
            ctx.audio.tone(880, 90)
            ctx.audio.tone(1319, 130)
        except Exception as exc:                          # noqa: BLE001
            ctx.log("tone: %s" % exc)


def setup(ctx):
    ctx.a = int(ctx.kv_get("a", 0)) % len(EAT)
    ctx.b = int(ctx.kv_get("b", 0)) % len(DO)
    ctx.spins = int(ctx.kv_get("s", 0))
    ctx.spin = [False, False]
    # the stored values are landings, so boot straight into LOCKED IN
    ctx.done = [True, True]
    ctx.dirty = False
    if ctx.audio and ctx.audio.ok:
        ctx.audio.set_volume(72)
    ctx.log("spinwheel a=%s b=%s spins=%d" % (EAT[ctx.a], DO[ctx.b], ctx.spins))
    draw(ctx)


def loop(ctx):
    if ctx.frame % SPIN_DIV:
        return
    held = ctx.buttons.current()
    if held == "up":
        ctx.a = (ctx.a + 1) % len(EAT)
        ctx.spin[0] = True
        ctx.done[0] = False
        ctx.spin[1] = False
        draw_card(ctx, 0)
    elif held == "down":
        ctx.b = (ctx.b + 1) % len(DO)
        ctx.spin[1] = True
        ctx.done[1] = False
        ctx.spin[0] = False
        draw_card(ctx, 1)
    else:
        # release: land whatever is under the pointer
        for which in (0, 1):
            if ctx.spin[which]:
                land(ctx, which)


def on_key(ctx, key):
    if key == "ok":
        ctx.a = 0
        ctx.b = 0
        ctx.spins = 0
        ctx.done = [False, False]
        ctx.spin = [False, False]
        ctx.dirty = True
        draw(ctx)


def teardown(ctx):
    if ctx.dirty:
        ctx.kv_set("a", ctx.a)
        ctx.kv_set("b", ctx.b)
        ctx.kv_set("s", ctx.spins)
        ctx.kv_flush()
