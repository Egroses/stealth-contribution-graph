import argparse
import datetime as dt
import hashlib
import heapq
import json
import math
import os
import random
import urllib.request
from pathlib import Path

import pixelfont as stil

QUERY = (
    "query($login:String!){user(login:$login){contributionsCollection{contributionCalendar{"
    "totalContributions weeks{contributionDays{contributionCount contributionLevel date weekday}}}}}}"
)
LEVEL_INDEX = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

WIDTH = 880
HEIGHT = 236
PITCH = 15.0
CELL = 11.5
GRID_TOP = 86.0
VIEW_RANGE = 4.2
HALF_ANGLE = 32.0
RAYS = 15
STEP = 0.18
HOLD = 12
MAX_STEPS = 260
CANDIDATES = 50
FLOOR_TARGETS = (0.4, 0.5, 0.6)
GUARD_COUNTS = (5, 3)
H_LENGTHS = (12, 6, 4)
V_LENGTHS = (6, 4)
SAMPLES = ((0, 0), (0.32, 0), (-0.32, 0), (0, 0.32), (0, -0.32), (0.24, 0.24), (-0.24, 0.24), (0.24, -0.24), (-0.24, -0.24))

THEMES = {
    "dark": {
        "bg": "#0d1117",
        "frame": "#30363d",
        "levels": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
        "crate_edge": "rgba(255,255,255,0.10)",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "faint": "#21262d",
        "guard": "#ff7b72",
        "guard_edge": "#0d1117",
        "cone": "#ffe7a1",
        "cone_stops": (0.78, 0.38, 0.0),
        "cone_blend": ' style="mix-blend-mode:screen"',
        "you": "#79c0ff",
        "you_band": "#0d1117",
        "you_eyes": "#ffffff",
        "green": "#3fb950",
        "door": "#010409",
        "overlay": "#0d1117",
    },
    "light": {
        "bg": "#ffffff",
        "frame": "#d1d9e0",
        "levels": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "crate_edge": "rgba(0,0,0,0.07)",
        "text": "#1f2328",
        "muted": "#59636e",
        "faint": "#eaeef2",
        "guard": "#cf222e",
        "guard_edge": "#ffffff",
        "cone": "#d4a72c",
        "cone_stops": (0.6, 0.3, 0.02),
        "cone_blend": " style=\"mix-blend-mode:multiply\"",
        "you": "#0969da",
        "you_band": "#ffffff",
        "you_eyes": "#0969da",
        "green": "#1a7f37",
        "door": "#59636e",
        "overlay": "#ffffff",
    },
}


def fetch_graph(login):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set. Any token that can read public profiles works, e.g. GITHUB_TOKEN=$(gh auth token)")
    request = urllib.request.Request(
        os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql"),
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "stealth-contribution-graph",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def load_calendar(json_path, login):
    if json_path:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    else:
        data = fetch_graph(login)
    user = (data.get("data") or {}).get("user")
    if not user:
        raise SystemExit(f"could not read the contribution graph of {login}: {data.get('errors')}")
    return user["contributionsCollection"]["contributionCalendar"]


class Board:
    def __init__(self, calendar, floor_target, seed):
        self.weeks = calendar["weeks"]
        self.total = calendar["totalContributions"]
        self.cols = len(self.weeks)
        self.rows = 7
        self.level = {}
        self.count = {}
        self.dates = {}
        for c, week in enumerate(self.weeks):
            for day in week["contributionDays"]:
                cell = (c, day["weekday"])
                level = LEVEL_INDEX.get(day["contributionLevel"], 0)
                if day["contributionCount"] > 0 and level == 0:
                    level = 1
                self.level[cell] = level
                self.count[cell] = day["contributionCount"]
                self.dates[cell] = day["date"]
        self.carved = set()
        self.original = {}
        self.sight = {}
        self.open_quiet_days(floor_target, random.Random(seed))
        self.ensure_connected()

    def carve(self, cell):
        if self.floor(cell):
            return
        self.carved.add(cell)
        self.original[cell] = self.level[cell]
        self.level[cell] = 0

    def open_quiet_days(self, target, rng):
        floor = sum(1 for level in self.level.values() if level == 0)
        need = math.ceil(target * len(self.level)) - floor
        if need <= 0:
            return
        busy = sorted(cell for cell, level in self.level.items() if level > 0)
        noise = {cell: rng.random() for cell in busy}
        busy.sort(key=lambda cell: (self.count[cell], noise[cell]))
        for cell in busy[:need]:
            self.carve(cell)

    def floor(self, cell):
        return self.level.get(cell, -1) == 0

    def blocks(self, cell):
        return self.level.get(cell, -1) != 0

    def neighbors(self, cell):
        c, r = cell
        for nc, nr in ((c + 1, r), (c, r - 1), (c, r + 1), (c - 1, r)):
            if self.floor((nc, nr)):
                yield (nc, nr)

    @property
    def entrances(self):
        return [cell for cell in self.level if self.floor(cell) and (cell[0] - 1, cell[1]) not in self.level]

    @property
    def exits(self):
        return [cell for cell in self.level if self.floor(cell) and (cell[0] + 1, cell[1]) not in self.level]

    def ensure_connected(self):
        starts = [cell for cell in self.level if (cell[0] - 1, cell[1]) not in self.level]
        goals = {cell for cell in self.level if (cell[0] + 1, cell[1]) not in self.level}
        cost = {}
        prev = {}
        heap = []
        for cell in starts:
            weight = self.level[cell] ** 2
            cost[cell] = weight
            prev[cell] = None
            heapq.heappush(heap, (weight, cell))
        while heap:
            d, cell = heapq.heappop(heap)
            if d > cost[cell]:
                continue
            c, r = cell
            for nxt in ((c + 1, r), (c, r - 1), (c, r + 1), (c - 1, r)):
                if nxt not in self.level:
                    continue
                nd = d + self.level[nxt] ** 2
                if nxt not in cost or nd < cost[nxt]:
                    cost[nxt] = nd
                    prev[nxt] = cell
                    heapq.heappush(heap, (nd, nxt))
        best = min(goals, key=lambda cell: (cost.get(cell, 10**9), cell[1]))
        if cost[best] == 0:
            return
        cell = best
        while cell is not None:
            self.carve(cell)
            cell = prev[cell]


def angle_gap(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def line_blocked(board, ox, oy, tx, ty):
    dx = tx - ox
    dy = ty - oy
    dist = math.hypot(dx, dy)
    steps = max(1, int(dist / 0.08))
    target = (math.floor(tx), math.floor(ty))
    for i in range(1, steps):
        f = i / steps
        cell = (math.floor(ox + dx * f), math.floor(oy + dy * f))
        if cell != target and board.blocks(cell):
            return True
    return False


def point_seen(board, gx, gy, angle, px, py):
    dx = px - gx
    dy = py - gy
    dist = math.hypot(dx, dy)
    if dist < 0.62:
        return True
    if dist > VIEW_RANGE:
        return False
    if angle_gap(math.degrees(math.atan2(dy, dx)), angle) > HALF_ANGLE:
        return False
    return not line_blocked(board, gx, gy, px, py)


def visible_cells(board, x, y, angle):
    key = (round(x, 3), round(y, 3), round(angle % 360.0, 3))
    if key in board.sight:
        return board.sight[key]
    seen = set()
    reach = int(VIEW_RANGE) + 2
    base_c = math.floor(x)
    for c in range(base_c - reach, base_c + reach + 1):
        for r in range(board.rows):
            cell = (c, r)
            if not board.floor(cell):
                continue
            cx = c + 0.5
            cy = r + 0.5
            if math.hypot(cx - x, cy - y) > VIEW_RANGE + 0.7:
                continue
            for ox, oy in SAMPLES:
                if point_seen(board, x, y, angle, cx + ox, cy + oy):
                    seen.add(cell)
                    break
    result = frozenset(seen)
    board.sight[key] = result
    return result


def cone_polygon(board, x, y, angle):
    points = [(x, y)]
    for i in range(RAYS):
        a = math.radians(angle - HALF_ANGLE + 2 * HALF_ANGLE * i / (RAYS - 1))
        ux = math.cos(a)
        uy = math.sin(a)
        dist = 0.0
        hit = VIEW_RANGE
        while dist < VIEW_RANGE:
            dist += 0.04
            cell = (math.floor(x + ux * dist), math.floor(y + uy * dist))
            if board.blocks(cell):
                hit = max(0.0, dist - 0.04)
                break
        points.append((x + ux * hit, y + uy * hit))
    return points


class Guard:
    def __init__(self, cells, base_angle, phase):
        self.cells = list(cells)
        self.length = len(self.cells)
        self.period = 2 * self.length
        self.base = base_angle
        self.phase = phase % self.period
        self.track = self.build_track()
        self.intervals = self.build_intervals()

    def local(self, u):
        n = self.length
        if u < n:
            return self.cells[u], 0.0
        if u == n:
            return self.cells[n - 1], 180.0
        return self.cells[2 * n - 1 - u], 180.0

    def turning(self, u):
        return u == self.length - 1 or u == 2 * self.length - 1

    def build_track(self):
        frames = []
        u0 = self.phase
        cell, rel = self.local(u0)
        angle = self.base + rel
        for t in range(self.period):
            u = (t + self.phase) % self.period
            cell, _ = self.local(u)
            frames.append((float(t), cell[0] + 0.5, cell[1] + 0.5, angle))
            if self.turning(u):
                for k in (1, 2, 3):
                    frames.append((t + k / 4.0, cell[0] + 0.5, cell[1] + 0.5, angle + 45.0 * k))
                angle += 180.0
        cell, _ = self.local(self.phase)
        frames.append((float(self.period), cell[0] + 0.5, cell[1] + 0.5, angle))
        return frames

    def build_intervals(self):
        result = []
        for t in range(self.period):
            inside = [f for f in self.track if t <= f[0] <= t + 1]
            samples = [(f[1], f[2], f[3]) for f in inside]
            first, last = inside[0], inside[-1]
            if (first[1], first[2]) != (last[1], last[2]):
                samples.append(((first[1] + last[1]) / 2, (first[2] + last[2]) / 2, first[3]))
            result.append(samples)
        return result

    def pose(self, time):
        t = time % self.period
        for a, b in zip(self.track, self.track[1:]):
            if a[0] <= t <= b[0]:
                f = 0.0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
                return (a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f)
        last = self.track[-1]
        return last[1], last[2], last[3]

    def cells_at(self, t):
        cells = set()
        for x, y, _ in self.intervals[t % self.period]:
            cells.add((math.floor(x), math.floor(y)))
        return cells


def patrol_segments(board):
    segments = []
    lo = 3
    hi = board.cols - 4
    for r in range(board.rows):
        run = []
        for c in range(board.cols + 1):
            cell = (c, r)
            if lo <= c <= hi and board.floor(cell):
                run.append(cell)
                continue
            for n in H_LENGTHS:
                for s in range(0, len(run) - n + 1):
                    segments.append((tuple(run[s:s + n]), 0.0))
            run = []
    for c in range(lo, hi + 1):
        run = []
        for r in range(board.rows + 1):
            cell = (c, r)
            if r < board.rows and board.floor(cell):
                run.append(cell)
                continue
            for n in V_LENGTHS:
                for s in range(0, len(run) - n + 1):
                    segments.append((tuple(run[s:s + n]), 90.0))
            run = []
    return segments


def passable_alone(board):
    cache = {}

    def check(cells, angle):
        key = (cells, angle)
        if key not in cache:
            guard = Guard(cells, angle, 0)
            danger = danger_table(board, [guard], guard.period)
            cache[key] = plan_route(board, danger, guard.period, 2) is not None
        return cache[key]

    return check


def pick_guards(segments, rng, count, passable):
    order = segments[:]
    rng.shuffle(order)
    taken = set()
    mids = []
    guards = []
    for cells, angle in order:
        mid = sum(c for c, _ in cells) / len(cells)
        if any(abs(mid - m) < 6 for m in mids):
            continue
        if any((c + dc, r + dr) in taken for c, r in cells for dc in (-1, 0, 1) for dr in (-1, 0, 1)):
            continue
        if not passable(cells, angle):
            continue
        if rng.random() < 0.5:
            cells = tuple(reversed(cells))
            angle += 180.0
        taken.update(cells)
        mids.append(mid)
        guards.append(Guard(cells, angle, rng.randrange(2 * len(cells))))
        if len(guards) == count:
            break
    return guards if len(guards) >= count - 1 else None


def lcm_all(values):
    out = 1
    for v in values:
        out = out * v // math.gcd(out, v)
    return out


def danger_table(board, guards, period):
    table = []
    for t in range(period):
        cells = set()
        for g in guards:
            for x, y, a in g.intervals[t % g.period]:
                cells |= visible_cells(board, x, y, a)
            cells |= g.cells_at(t)
        table.append(frozenset(cells))
    return table


def next_nodes(board, exits, entrances, danger, period, node, t):
    lit = danger[t % period]
    if node == "IN":
        return [e for e in entrances if e not in lit] + ["IN"]
    if node == "OUT" or node in lit:
        return []
    options = [n for n in board.neighbors(node) if n not in lit]
    if node in exits:
        options.append("OUT")
    options.append(node)
    return options


def preference(node, nxt):
    if nxt == "OUT":
        return (0, 0)
    if node == "IN":
        return (2, nxt[1]) if nxt != "IN" else (1, 0)
    if nxt == node:
        return (2, 0)
    dc = nxt[0] - node[0]
    if dc > 0:
        return (1, 0)
    if dc == 0:
        return (3, 0)
    return (4, 0)


def plan_route(board, danger, period, start):
    entrances = sorted(board.entrances, key=lambda e: e[1])
    exits = set(board.exits)
    layers = [{"IN"}]
    t = start
    while "OUT" not in layers[-1]:
        if len(layers) > MAX_STEPS:
            return None
        grown = set()
        for node in layers[-1]:
            grown.update(next_nodes(board, exits, entrances, danger, period, node, t))
        if not grown:
            return None
        layers.append(grown)
        t += 1
    arrival = start + len(layers) - 1
    good = [set() for _ in layers]
    good[-1] = {"OUT"}
    for i in range(len(layers) - 2, -1, -1):
        for node in layers[i]:
            if any(n in good[i + 1] for n in next_nodes(board, exits, entrances, danger, period, node, start + i)):
                good[i].add(node)
    route = [("IN", start)]
    node = "IN"
    for i in range(len(layers) - 1):
        options = [n for n in next_nodes(board, exits, entrances, danger, period, node, start + i) if n in good[i + 1]]
        node = min(options, key=lambda n: preference(node, n))
        route.append((node, start + i + 1))
    assert route[-1] == ("OUT", arrival)
    return route


def tension(board, guards, danger, period, route):
    waits = 0
    close = 0
    near_guards = set()
    for (a, t), (b, _) in zip(route, route[1:]):
        if a in ("IN", "OUT"):
            continue
        if a == b:
            waits += 1
        lit = danger[t % period]
        if any((a[0] + dc, a[1] + dr) in lit for dc in (-1, 0, 1) for dr in (-1, 0, 1)):
            close += 1
        for i, g in enumerate(guards):
            gx, gy, _ = g.pose(t)
            if math.hypot(gx - a[0] - 0.5, gy - a[1] - 0.5) <= 2.6:
                near_guards.add(i)
    return waits, close, len(near_guards)


def route_point(route, time):
    idx = int(math.floor(time))
    f = time - idx
    if idx >= len(route) - 1:
        return None
    a = route[idx][0]
    b = route[idx + 1][0]
    if a in ("IN", "OUT") and b in ("IN", "OUT"):
        return None
    if a in ("IN", "OUT"):
        return (b[0] + 0.5, b[1] + 0.5)
    if b in ("IN", "OUT"):
        return (a[0] + 0.5, a[1] + 0.5)
    return (a[0] + 0.5 + (b[0] - a[0]) * f, a[1] + 0.5 + (b[1] - a[1]) * f)


def verify(board, guards, route, substeps=10):
    offset = route[0][1]
    for i in range((len(route) - 1) * substeps + 1):
        local = i / substeps
        point = route_point(route, local)
        if point is None:
            continue
        absolute = offset + local
        for g in guards:
            gx, gy, ga = g.pose(absolute)
            for ox, oy in SAMPLES:
                if point_seen(board, gx, gy, ga % 360.0, point[0] + ox, point[1] + oy):
                    return False
    return True


def best_start(board, danger, period):
    best = None
    for start in range(2, 2 + period):
        route = plan_route(board, danger, period, start)
        if route is None:
            continue
        arrival = route[-1][1]
        loop = math.ceil((arrival + HOLD) / period) * period
        key = (loop - arrival - HOLD, loop)
        if best is None or key < best[0]:
            best = (key, start, route, loop)
        if key[0] == 0:
            break
    return best


def choose_plan(board, seed, count):
    rng = random.Random(seed)
    segments = patrol_segments(board)
    passable = passable_alone(board)
    ranked = []
    for _ in range(CANDIDATES):
        guards = pick_guards(segments, rng, count, passable)
        if not guards:
            continue
        period = lcm_all(g.period for g in guards)
        danger = danger_table(board, guards, period)
        found = best_start(board, danger, period)
        if found is None:
            continue
        (slack, loop), start, route, _ = found
        travel = route[-1][1] - start
        if travel > 140:
            continue
        waits, close, met = tension(board, guards, danger, period, route)
        score = met * 3.0 + close * 0.6 + min(waits, 14) * 0.8 - slack * 0.6 - max(0, travel - 100) * 0.3
        ranked.append((score, len(ranked), guards, period, loop, start, route))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for _, _, guards, period, loop, start, route in ranked:
        if verify(board, guards, route):
            return guards, period, loop, start, route
    return None


def make_plan(calendar, seed):
    boards = [Board(calendar, target, seed) for target in FLOOR_TARGETS]
    for count in GUARD_COUNTS:
        for board in boards:
            plan = choose_plan(board, seed, count)
            if plan:
                return board, plan
    raise SystemExit("no safe route found for this contribution graph")


def assert_never_seen(board, guards, loop, start, route):
    danger_hits = 0
    for (a, t), (b, _) in zip(route, route[1:]):
        for cell in (a, b):
            if cell in ("IN", "OUT"):
                continue
            for g in guards:
                for x, y, ang in g.intervals[t % g.period]:
                    if cell in visible_cells(board, x, y, ang):
                        danger_hits += 1
    assert danger_hits == 0, f"seen {danger_hits} times"
    assert verify(board, guards, route, substeps=20), "continuous check failed"
    assert route[-1][1] + HOLD <= loop
    assert all(loop % g.period == 0 for g in guards)


def fmt(v):
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def compress(series):
    keep = []
    n = len(series)
    for i, (k, v) in enumerate(series):
        if 0 < i < n - 1 and series[i - 1][1] == v and series[i + 1][1] == v:
            continue
        keep.append((k, v))
    return keep


def key_time(k, total):
    if k == 0:
        return "0"
    if k == total:
        return "1"
    return f"{k / total:.4f}".rstrip("0").rstrip(".")


def anim(attr, series, dur, kind="animate", extra=""):
    series = compress(series)
    total = series[-1][0]
    values = ";".join(v for _, v in series)
    times = ";".join(key_time(k, total) for k, _ in series)
    tag = "animateTransform" if kind == "transform" else "animate"
    type_attr = f' type="{extra}"' if kind == "transform" else ""
    return f'<{tag} attributeName="{attr}"{type_attr} values="{values}" keyTimes="{times}" dur="{fmt(dur)}s" repeatCount="indefinite"/>'


def month_labels(board):
    labels = []
    last = None
    for c, week in enumerate(board.weeks):
        first = week["contributionDays"][0]["date"]
        month = int(first[5:7])
        if month != last:
            labels.append((c, MONTHS[month - 1]))
            last = month
    cleaned = []
    for c, name in labels:
        if cleaned and c - cleaned[-1][0] < 3:
            cleaned[-1] = (c, name)
            continue
        cleaned.append((c, name))
    return cleaned


def pretty_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def render(board, guards, loop, start, route, theme_name):
    th = THEMES[theme_name]
    out = []
    gx0 = (WIDTH - PITCH * board.cols) / 2

    def px(cx, cy):
        return gx0 + cx * PITCH, GRID_TOP + cy * PITCH

    grid_w = PITCH * board.cols
    grid_h = PITCH * 7
    dur = loop * STEP

    out.append(f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="10" fill="{th["bg"]}" stroke="{th["frame"]}"/>')

    out.append(stil.pixel_text("STEALTH RUN", 24, 20, 3, th["text"]))
    cursor_x = 24 + stil.text_width("STEALTH RUN", 3) + 6
    out.append(
        f'<rect x="{fmt(cursor_x)}" y="20" width="9" height="21" fill="{th["green"]}">'
        f'<animate attributeName="opacity" values="1;1;0;0" keyTimes="0;0.5;0.5;1" dur="1s" repeatCount="indefinite"/></rect>'
    )
    out.append(
        f'<text x="24" y="62" font-family="{stil.MONO}" font-size="11" fill="{th["muted"]}">'
        f"a year of commits, used as cover. the guards patrol the empty days.</text>"
    )
    right = WIDTH - 24
    zero_w = stil.text_width("0", 3)
    out.append(stil.pixel_text("0", right, 20, 3, th["green"], anchor="end"))
    out.append(stil.pixel_text("DETECTED", right - zero_w - 10, 27, 2, th["muted"], anchor="end"))
    out.append(
        f'<text x="{right}" y="62" text-anchor="end" font-family="{stil.MONO}" font-size="11" fill="{th["muted"]}">'
        f"regenerated daily from the contribution graph</text>"
    )

    for c, name in month_labels(board):
        x, _ = px(c, 0)
        out.append(f'<text x="{fmt(x + 1)}" y="{fmt(GRID_TOP - 7)}" font-family="{stil.MONO}" font-size="10" fill="{th["muted"]}">{name}</text>')

    inset = (PITCH - CELL) / 2
    for (c, r), level in sorted(board.level.items()):
        x, y = px(c, r)
        fill = th["levels"][level]
        out.append(f'<rect x="{fmt(x + inset)}" y="{fmt(y + inset)}" width="{CELL}" height="{CELL}" rx="2" fill="{fill}"/>')
        if level > 0:
            out.append(
                f'<path d="M{fmt(x + inset + 2)} {fmt(y + inset + 1.6)}h{fmt(CELL - 4)}" stroke="{th["crate_edge"]}" stroke-width="1.4" stroke-linecap="round"/>'
            )
    for c, r in sorted(board.carved):
        x, y = px(c, r)
        fill = th["levels"][board.original[(c, r)]]
        out.append(f'<rect x="{fmt(x + inset)}" y="{fmt(y + inset)}" width="{CELL}" height="{CELL}" rx="2" fill="{fill}"/>')
        out.append(f'<rect x="{fmt(x + inset + 2.5)}" y="{fmt(y + inset + 2.5)}" width="{fmt(CELL - 5)}" height="{fmt(CELL - 5)}" rx="1.5" fill="{th["levels"][0]}"/>')

    first_cell = next(cell for cell, _ in route if cell not in ("IN", "OUT"))
    last_cell = next(cell for cell, _ in reversed(route) if cell not in ("IN", "OUT"))
    door_in = (first_cell[0] - 0.75, first_cell[1] + 0.5)
    door_out = (last_cell[0] + 1.75, last_cell[1] + 0.5)

    ix, iy = px(*door_in)
    out.append(f'<rect x="{fmt(ix - 3)}" y="{fmt(iy - 7)}" width="6" height="14" rx="1.5" fill="{th["door"]}" stroke="{th["frame"]}"/>')
    out.append(f'<text x="{fmt(ix)}" y="{fmt(iy - 11)}" text-anchor="middle" font-family="{stil.MONO}" font-size="8" fill="{th["muted"]}">IN</text>')

    ox, oy = px(*door_out)
    exit_time = route[-1][1]
    flash = [(0, "0.25")]
    flash.append((max(0, exit_time - 1), "0.25"))
    flash.append((exit_time, "1"))
    flash.append((min(loop, exit_time + 4), "0.25"))
    flash.append((loop, "0.25"))
    out.append(
        f'<rect x="{fmt(ox - 8)}" y="{fmt(oy - 10)}" width="16" height="20" rx="4" fill="{th["green"]}" opacity="0.25">'
        + anim("opacity", flash, dur)
        + "</rect>"
    )
    out.append(f'<rect x="{fmt(ox - 3)}" y="{fmt(oy - 7)}" width="6" height="14" rx="1.5" fill="{th["door"]}" stroke="{th["green"]}"/>')
    label_y = oy - 12 if last_cell[1] > 0 else oy + 19
    out.append(f'<text x="{fmt(ox)}" y="{fmt(label_y)}" text-anchor="middle" font-family="{stil.MONO}" font-size="8" font-weight="700" fill="{th["green"]}">EXIT</text>')

    positions = []
    for (cell, t) in route:
        if cell == "IN":
            positions.append((t, door_in))
        elif cell == "OUT":
            positions.append((t, door_out))
        else:
            positions.append((t, (cell[0] + 0.5, cell[1] + 0.5)))
    timeline = {}
    for t in range(loop + 1):
        timeline[t] = door_in if t < start else door_out
    for t, p in positions:
        timeline[t] = p
    pos_series = []
    for t in range(loop + 1):
        x, y = px(*timeline[t])
        pos_series.append((t, f"{fmt(x)},{fmt(y)}"))
    pos_series[-1] = (loop, pos_series[0][1])
    appear = max(0, start - 2)

    def shifted(series_map, k):
        return [(t, series_map[(t - k) % loop]) for t in range(loop + 1)]

    pos_map = {t: v for t, v in pos_series[:-1]}
    op_map = {}
    for t in range(loop):
        if t < appear:
            op_map[t] = 0.0
        elif t < start:
            op_map[t] = (t - appear) / max(1, start - appear)
        elif t <= exit_time:
            op_map[t] = 1.0
        else:
            op_map[t] = 0.0

    stops = th["cone_stops"]
    defs = (
        f'<radialGradient id="cone" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="{fmt(VIEW_RANGE * PITCH)}">'
        f'<stop offset="0" stop-color="{th["cone"]}" stop-opacity="{stops[0]}"/>'
        f'<stop offset="0.65" stop-color="{th["cone"]}" stop-opacity="{stops[1]}"/>'
        f'<stop offset="1" stop-color="{th["cone"]}" stop-opacity="{stops[2]}"/>'
        f"</radialGradient>"
        f'<radialGradient id="glow" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="11">'
        f'<stop offset="0" stop-color="{th["you"]}" stop-opacity="0.45"/>'
        f'<stop offset="1" stop-color="{th["you"]}" stop-opacity="0"/>'
        f"</radialGradient>"
    )

    for g in guards:
        gdur = g.period * STEP
        move = []
        shape = []
        turn = []
        for t, cx, cy, ang in g.track:
            x, y = px(cx, cy)
            move.append((t, f"{fmt(x)},{fmt(y)}"))
            turn.append((t, fmt(ang)))
            poly = cone_polygon(board, cx, cy, ang % 360.0)
            rel = [((qx - cx) * PITCH, (qy - cy) * PITCH) for qx, qy in poly]
            d = "M" + " L".join(f"{fmt(a)} {fmt(b)}" for a, b in rel) + "Z"
            shape.append((t, d))
        x0, y0 = px(g.track[0][1], g.track[0][2])
        out.append(f'<g transform="translate({fmt(x0)},{fmt(y0)})">')
        out.append(anim("transform", move, gdur, kind="transform", extra="translate"))
        out.append(f'<path d="{shape[0][1]}" fill="url(#cone)"{th["cone_blend"]}>' + anim("d", shape, gdur) + "</path>")
        out.append("</g>")

    for k, alpha in ((2, 0.18), (1, 0.32)):
        ghost_pos = shifted(pos_map, k)
        ghost_op = [(t, fmt(op_map[(t - k) % loop] * alpha)) for t in range(loop + 1)]
        out.append(f'<g transform="translate({ghost_pos[0][1]})" opacity="0">')
        out.append(anim("transform", ghost_pos, dur, kind="transform", extra="translate"))
        out.append(anim("opacity", ghost_op, dur))
        out.append(f'<circle r="4.2" fill="{th["you"]}"/>')
        out.append("</g>")

    you_op = [(t, fmt(op_map[t % loop])) for t in range(loop + 1)]
    out.append(f'<g transform="translate({pos_series[0][1]})" opacity="0">')
    out.append(anim("transform", pos_series, dur, kind="transform", extra="translate"))
    out.append(anim("opacity", you_op, dur))
    out.append('<circle r="11" fill="url(#glow)"/>')
    out.append(f'<circle r="5.3" fill="{th["you"]}" stroke="{th["guard_edge"]}" stroke-width="1.4"/>')
    out.append(f'<rect x="-4.3" y="-1.9" width="8.6" height="3" rx="1.2" fill="{th["you_band"]}"/>')
    out.append(f'<rect x="-2.6" y="-1" width="1.5" height="1.3" fill="{th["you_eyes"]}"/>')
    out.append(f'<rect x="1.1" y="-1" width="1.5" height="1.3" fill="{th["you_eyes"]}"/>')
    out.append("</g>")

    route_cells = {}
    for cell, t in route:
        if cell not in ("IN", "OUT"):
            route_cells[t] = cell

    for g in guards:
        gdur = g.period * STEP
        move = []
        turn = []
        for t, cx, cy, ang in g.track:
            x, y = px(cx, cy)
            move.append((t, f"{fmt(x)},{fmt(y)}"))
            turn.append((t, fmt(ang)))
        x0, y0 = px(g.track[0][1], g.track[0][2])
        out.append(f'<g transform="translate({fmt(x0)},{fmt(y0)})">')
        out.append(anim("transform", move, gdur, kind="transform", extra="translate"))
        out.append(f'<g transform="rotate({turn[0][1]})">')
        out.append(anim("transform", turn, gdur, kind="transform", extra="rotate"))
        out.append(f'<rect x="3.2" y="-2" width="4.6" height="4" rx="1" fill="{th["cone"]}"/>')
        out.append("</g>")
        out.append(f'<circle r="5.6" fill="{th["guard"]}" stroke="{th["guard_edge"]}" stroke-width="1.5"/>')
        out.append(f'<circle r="2" fill="{th["guard_edge"]}" opacity="0.55"/>')
        alert = []
        for t in range(loop + 1):
            cell = route_cells.get(t % loop)
            show = "0"
            if cell is not None:
                gx, gy, _ = g.pose(t)
                if math.hypot(gx - cell[0] - 0.5, gy - cell[1] - 0.5) <= VIEW_RANGE + 0.6:
                    show = "1"
            alert.append((t, show))
        if any(v == "1" for _, v in alert):
            out.append(f'<g opacity="0">' + anim("opacity", alert, dur))
            out.append(f'<rect x="-4.5" y="-21" width="9" height="11" rx="2" fill="{th["bg"]}" stroke="{th["cone"]}" stroke-width="1"/>')
            out.append(stil.pixel_text("?", 0, -19, 1.2, th["cone"], anchor="middle"))
            out.append("</g>")
        out.append("</g>")

    bar_y = GRID_TOP + grid_h + 9
    out.append(f'<rect x="{fmt(gx0)}" y="{fmt(bar_y)}" width="{fmt(grid_w)}" height="3" rx="1.5" fill="{th["faint"]}"/>')
    span_in = px(*door_in)[0]
    span_out = px(*door_out)[0]
    bar = []
    for t in range(loop + 1):
        x = float(pos_series[t][1].split(",")[0])
        frac = (x - span_in) / (span_out - span_in)
        if t >= exit_time:
            frac = 1.0
        if t < start:
            frac = 0.0
        bar.append((t, fmt(max(0.0, min(1.0, frac)) * grid_w)))
    bar[-1] = (loop, "0")
    out.append(f'<rect x="{fmt(gx0)}" y="{fmt(bar_y)}" width="0" height="3" rx="1.5" fill="{th["you"]}">' + anim("width", bar, dur) + "</rect>")

    legend_y = HEIGHT - 20
    lx = 24
    for level in (1, 2, 3, 4):
        out.append(f'<rect x="{lx}" y="{legend_y - 9}" width="10" height="10" rx="2" fill="{th["levels"][level]}"/>')
        lx += 13
    out.append(f'<text x="{lx + 3}" y="{legend_y}" font-family="{stil.MONO}" font-size="10.5" fill="{th["muted"]}">commits = cover</text>')
    lx += 118
    out.append(f'<circle cx="{lx + 5}" cy="{legend_y - 4}" r="5" fill="{th["guard"]}"/>')
    out.append(f'<text x="{lx + 15}" y="{legend_y}" font-family="{stil.MONO}" font-size="10.5" fill="{th["muted"]}">guard</text>')
    lx += 62
    out.append(f'<path d="M{lx} {legend_y - 4} l16 -6 v12 z" fill="{th["cone"]}" opacity="0.6"/>')
    out.append(f'<text x="{lx + 21}" y="{legend_y}" font-family="{stil.MONO}" font-size="10.5" fill="{th["muted"]}">vision</text>')
    lx += 72
    out.append(f'<circle cx="{lx + 5}" cy="{legend_y - 4}" r="5" fill="{th["you"]}"/>')
    out.append(f'<text x="{lx + 15}" y="{legend_y}" font-family="{stil.MONO}" font-size="10.5" fill="{th["muted"]}">intruder</text>')
    dates = sorted(board.dates.values())
    summary = f"{board.total:,} contributions · {pretty_date(dates[0])} – {pretty_date(dates[-1])}"
    out.append(f'<text x="{WIDTH - 24}" y="{legend_y}" text-anchor="end" font-family="{stil.MONO}" font-size="10.5" fill="{th["muted"]}">{summary}</text>')

    cx = WIDTH / 2
    cy = GRID_TOP + grid_h / 2
    banner = [(0, "0"), (exit_time, "0"), (exit_time + 1, "1"), (loop - 1, "1"), (loop, "0")]
    out.append(f'<g opacity="0">' + anim("opacity", banner, dur))
    out.append(f'<rect x="{fmt(cx - 150)}" y="{fmt(cy - 32)}" width="300" height="64" rx="8" fill="{th["overlay"]}" fill-opacity="0.92" stroke="{th["green"]}"/>')
    out.append(stil.pixel_text("ESCAPED", cx, cy - 20, 4, th["green"], anchor="middle"))
    out.append(
        f'<text x="{fmt(cx)}" y="{fmt(cy + 23)}" text-anchor="middle" font-family="{stil.MONO}" font-size="11" fill="{th["muted"]}">'
        f"{len(board.weeks)} weeks crossed · detected 0 times</text>"
    )
    out.append("</g>")

    label = "Stealth run across a year of contributions: guards patrol the contribution graph while an intruder sneaks from the first week to the last without being seen"
    return stil.svg(WIDTH, HEIGHT, "\n".join(out), defs=defs, label=label)


def graph_seed(login, calendar):
    last_day = max(day["date"] for week in calendar["weeks"] for day in week["contributionDays"])
    text = f"{login.lower()}:{last_day}:{calendar['totalContributions']}"
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def main():
    parser = argparse.ArgumentParser(description="Turn a GitHub contribution graph into an animated stealth level.")
    parser.add_argument("--user", help="GitHub user name whose contribution graph is used")
    parser.add_argument("--json", help="read a saved GraphQL response instead of calling the API")
    parser.add_argument("--out", default="dist", help="folder for stealth-dark.svg and stealth-light.svg")
    args = parser.parse_args()
    if not args.user and not args.json:
        parser.error("--user is required")
    login = args.user or Path(args.json).stem
    calendar = load_calendar(args.json, login)
    board, (guards, period, loop, start, route) = make_plan(calendar, graph_seed(login, calendar))
    assert_never_seen(board, guards, loop, start, route)
    out_dir = Path(args.out)
    for name in ("dark", "light"):
        stil.write(out_dir / f"stealth-{name}.svg", render(board, guards, loop, start, route, name))
    waits = sum(1 for (a, _), (b, _) in zip(route, route[1:]) if a == b and a not in ("IN", "OUT"))
    print(
        f"guards={len(guards)} periods={[g.period for g in guards]} loop={loop} steps ({loop * STEP:.1f}s) "
        f"start={start} exit={route[-1][1]} waits_on_grid={waits} carved={len(board.carved)}"
    )


if __name__ == "__main__":
    main()
