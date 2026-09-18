"""Snake - the classic, with three buttons.

UP    turn left
DOWN  turn right
OK    pause / unpause   (from GAME OVER: play again)

Steering is RELATIVE because there are only two direction buttons: the snake
always moves, UP and DOWN rotate it. The screen is redrawn incrementally (move
the head, erase the tail) so a step costs two fill_rect calls, not 270.

ASCII-only and small on purpose: see docs/KNOWN_ISSUES.md #1 - a BLE upload
over 10s gets the phone heartbeat injected into app.py and loses its tail, and
a cut inside a UTF-8 char turns into UnicodeError / "LOAD FAILED".
"""

import random
import time

TITLE = "Snake"

CELL = 16
COLS = 15
ROWS = 18
TOP = 24
BOARD_H = ROWS * CELL

BG = 0x0000
BAR = 0x18E3
SEP = 0x39E7
BODY = 0x07E0
HEAD = 0xBFF7
FOOD = 0xF800
WALL = 0x39E7
DIM = 0x8410
WHT = 0xFFFF
ACC = 0x07FF

DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))     # right, down, left, up

P_START = 280
P_MIN = 90
P_STEP = 8


def _frame(ctx):
    l = ctx.lcd
    l.fill(BG)
    l.fill_rect(0, 0, ctx.w, TOP - 1, BAR)
    l.text2x("SNAKE", 6, 3, ACC, BAR)
    l.fill_rect(0, TOP - 1, ctx.w, 1, SEP)
    _head(ctx)


def _head(ctx):
    l = ctx.lcd
    l.fill_rect(120, 3, 116, 18, BAR)
    s = "%d/%d" % (ctx.score, ctx.best)
    l.text(s, 234 - 8 * len(s), 8, DIM, BAR)


def _cell(ctx, c, r, col):
    # ctx is passed in explicitly: a mini-program has no module-level ctx.
    ctx.lcd.fill_rect(c * CELL, TOP + r * CELL, CELL - 1, CELL - 1, col)


def _board(ctx):
    l = ctx.lcd
    l.fill_rect(0, TOP, ctx.w, BOARD_H, BG)
    l.rect(0, TOP, ctx.w, BOARD_H, WALL)
    _cell(ctx, ctx.food[0], ctx.food[1], FOOD)
    n = len(ctx.body)
    for i, (c, r) in enumerate(ctx.body):
        _cell(ctx, c, r, HEAD if i == 0 else BODY)


def _place_food(ctx):
    free = []
    for c in range(COLS):
        for r in range(ROWS):
            if (c, r) not in ctx.body:
                free.append((c, r))
    if not free:
        ctx.food = (-1, -1)
        return False
    ctx.food = free[random.randint(0, len(free) - 1)]
    return True


def _over(ctx):
    ctx.state = "over"
    if ctx.score > ctx.best:
        ctx.best = ctx.score
        ctx.kv_set("best", ctx.best)
        ctx.kv_flush()
    l = ctx.lcd
    l.fill_rect(24, 118, ctx.w - 48, 84, BAR)
    l.rect(24, 118, ctx.w - 48, 84, SEP)
    l.text_center("GAME OVER", 132, WHT, BAR, 1)
    l.text_center("score %d" % ctx.score, 150, ACC, BAR, 1)
    l.text_center("best %d" % ctx.best, 166, DIM, BAR, 1)
    l.text_center("OK = again", 184, 0x07E0, BAR, 1)
    _head(ctx)



def _new(ctx):
    c = COLS // 3
    r = ROWS // 2
    ctx.body = [(c + 2, r), (c + 1, r), (c, r)]
    ctx.dir = 0
    ctx.score = 0
    ctx.period = P_START
    ctx.state = "run"
    ctx.paused = False
    ctx.t_next = time.ticks_add(time.ticks_ms(), ctx.period)
    _place_food(ctx)
    _board(ctx)


def _turn(ctx, delta):
    if ctx.state != "run" or ctx.paused:
        return
    d = (ctx.dir + delta) % 4
    # ignore a reversal straight into your own neck
    hc, hr = ctx.body[0]
    tc, tr = ctx.body[1]
    nc = hc + DIRS[d][0]
    nr = hr + DIRS[d][1]
    if (nc, nr) != (tc, tr):
        ctx.dir = d


def _step(ctx):
    d = DIRS[ctx.dir]
    hc, hr = ctx.body[0]
    nc, nr = hc + d[0], hr + d[1]
    if nc < 0 or nc >= COLS or nr < 0 or nr >= ROWS:
        _over(ctx)
        return
    grow = (nc, nr) == ctx.food
    seg = ctx.body if grow else ctx.body[:-1]
    if (nc, nr) in seg:
        _over(ctx)
        return

    ctx.body.insert(0, (nc, nr))
    _cell(ctx, nc, nr, HEAD)
    if len(ctx.body) > 2:
        pc, pr = ctx.body[1]
        _cell(ctx, pc, pr, BODY)

    if grow:
        ctx.score += 1
        ctx.period = max(P_MIN, ctx.period - P_STEP)
        if not _place_food(ctx):
            _over(ctx)
            return
        _cell(ctx, ctx.food[0], ctx.food[1], FOOD)
        _head(ctx)
    else:
        tc, tr = ctx.body.pop()
        _cell(ctx, tc, tr, BG)

    ctx.t_next = time.ticks_add(time.ticks_ms(), ctx.period)


def _pause(ctx, on):
    l = ctx.lcd
    ctx.paused = on
    if on:
        l.fill_rect(60, 140, 120, 32, BAR)
        l.rect(60, 140, 120, 32, SEP)
        l.text_center("PAUSE", 152, ACC, BAR, 1)
    else:
        _board(ctx)
        ctx.t_next = time.ticks_add(time.ticks_ms(), ctx.period)


def setup(ctx):
    ctx.best = int(ctx.kv_get("best", 0))
    ctx.score = 0                     # _frame() draws the score, so set it first
    ctx.state = "run"
    ctx.paused = False
    ctx.food = (0, 0)
    ctx.body = []
    _frame(ctx)
    _new(ctx)


def on_key(ctx, key):
    if key == "ok":
        if ctx.state == "over":
            _new(ctx)
        else:
            _pause(ctx, not ctx.paused)
    elif key == "up":
        _turn(ctx, -1)
    elif key == "down":
        _turn(ctx, 1)


def loop(ctx):
    if ctx.state != "run" or ctx.paused:
        return
    if time.ticks_diff(time.ticks_ms(), ctx.t_next) >= 0:
        _step(ctx)


def teardown(ctx):
    ctx.kv_set("best", ctx.best)
    ctx.kv_flush()
