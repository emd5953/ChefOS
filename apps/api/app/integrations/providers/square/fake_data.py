"""Restaurant model for the simulated seller "Juniper House" (Brooklyn, NY).

Menu, staff roster, demand curves and the shift schedule. Everything here is a pure,
deterministic function of the calendar date (seeded RNG), never of the wall clock.
"""

import math
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

TZ_NAME = "America/New_York"
TZ = ZoneInfo(TZ_NAME)
LOCATION_ID = "L_JUNIPER_BK"
MERCHANT_ID = "MLJUNIPER"
BUSINESS_NAME = "Juniper House"

# ---------------------------------------------------------------- menu


@dataclass(frozen=True, slots=True)
class Variation:
    id: str
    name: str
    price_cents: int


@dataclass(frozen=True, slots=True)
class MenuItem:
    id: str
    name: str
    category: str  # category name
    variations: tuple[Variation, ...]
    deleted: bool = False
    price_changed: bool = False  # catalog updated_at is "20 days ago"


CATEGORIES: tuple[tuple[str, str], ...] = (
    ("CAT_STARTERS", "Starters"),
    ("CAT_MAINS", "Mains"),
    ("CAT_SIDES", "Sides"),
    ("CAT_DESSERTS", "Desserts"),
    ("CAT_BRUNCH", "Brunch"),
    ("CAT_COCKTAILS", "Cocktails"),
    ("CAT_WINE", "Wine"),
    ("CAT_BEER", "Beer"),
    ("CAT_NA", "N/A Beverages"),
)
CATEGORY_ID_BY_NAME = {name: cid for cid, name in CATEGORIES}


def _item(
    slug: str,
    name: str,
    category: str,
    price: int,
    *,
    deleted: bool = False,
    changed: bool = False,
    extra: tuple[tuple[str, str, int], ...] = (),
    single_name: str = "Regular",
) -> MenuItem:
    variations = [Variation(f"VAR_{slug}", single_name, price * 100)]
    for suffix, vname, vprice in extra:
        variations.append(Variation(f"VAR_{slug}_{suffix}", vname, vprice * 100))
    return MenuItem(f"ITEM_{slug}", name, category, tuple(variations), deleted, changed)


MENU: tuple[MenuItem, ...] = (
    _item("RICOTTA_TOAST", "Whipped Ricotta Toast", "Starters", 14),
    _item("BRUSSELS", "Crispy Brussels Sprouts", "Starters", 13),
    _item("TARTARE", "Beef Tartare", "Starters", 19),
    _item("OYSTERS", "East Coast Oysters (6)", "Starters", 21),
    _item("TROUT_DIP", "Smoked Trout Dip", "Starters", 15, deleted=True),
    _item("CHICKEN", "Roast Half Chicken", "Mains", 31),
    _item("BURGER", "Dry-Aged Burger", "Mains", 24),
    _item("BRANZINO", "Pan-Seared Branzino", "Mains", 36),
    _item("HANGER", "Hanger Steak Frites", "Mains", 40, changed=True),
    _item("RIGATONI", "Cacio e Pepe Rigatoni", "Mains", 26),
    _item("STRIP", "Dry-Aged NY Strip", "Mains", 42),
    _item("FRIES", "Hand-Cut Fries", "Sides", 9),
    _item("BROCCOLINI", "Charred Broccolini", "Sides", 10),
    _item("CARROTS", "Honey-Roasted Carrots", "Sides", 10),
    _item("OLIVE_CAKE", "Olive Oil Cake", "Desserts", 12),
    _item("POT_CREME", "Chocolate Pot de Creme", "Desserts", 11),
    _item("SORBET", "Seasonal Sorbet", "Desserts", 9),
    _item("PANCAKES", "Ricotta Pancakes", "Brunch", 17),
    _item("SHAKSHUKA", "Shakshuka", "Brunch", 18),
    _item("BISCUIT", "Fried Chicken Biscuit", "Brunch", 19),
    _item("STEAK_EGGS", "Steak & Eggs", "Brunch", 28),
    _item("GIMLET", "Juniper Gimlet", "Cocktails", 16),
    _item("OLD_FASHIONED", "Old Fashioned", "Cocktails", 18, changed=True),
    _item("ESPRESSO_MARTINI", "Espresso Martini", "Cocktails", 17),
    _item("MIMOSA", "Mimosa", "Cocktails", 13),
    _item(
        "PINOT",
        "Pinot Noir",
        "Wine",
        15,
        single_name="Glass",
        extra=(("BTL", "Bottle", 58),),
    ),
    _item(
        "SAUV_BLANC",
        "Sauvignon Blanc",
        "Wine",
        14,
        single_name="Glass",
        extra=(("BTL", "Bottle", 52),),
    ),
    _item("ROSE", "Provence Rose", "Wine", 13, single_name="Glass", extra=(("BTL", "Bottle", 48),)),
    _item("IPA", "Draft IPA", "Beer", 8),
    _item("PILSNER", "Pilsner", "Beer", 7),
    _item("LEMONADE", "Fresh Lemonade", "N/A Beverages", 6),
    _item("SPARKLING_WATER", "Sparkling Water", "N/A Beverages", 7),
)
ITEMS_BY_ID = {m.id: m for m in MENU}


def active_items(category: str) -> tuple[MenuItem, ...]:
    return tuple(m for m in MENU if m.category == category and not m.deleted)


ADHOC_CATALOG_ID = "ADHOC_OPEN_FOOD"  # intentionally absent from the catalog

# ---------------------------------------------------------------- staff


@dataclass(frozen=True, slots=True)
class StaffMember:
    id: str
    given_name: str
    family_name: str
    role: str
    hourly_cents: int
    tipped: bool
    in_directory: bool = True  # False => exists only in timecards


def _s(i: str, g: str, f: str, role: str, rate: float, tipped: bool = False) -> StaffMember:
    return StaffMember(f"TM_{i}", g, f, role, round(rate * 100), tipped)


STAFF: tuple[StaffMember, ...] = (
    _s("SRV1", "Maya", "Okafor", "Server", 16.0, True),
    _s("SRV2", "Diego", "Marsh", "Server", 16.5, True),
    _s("SRV3", "Priya", "Raman", "Server", 17.0, True),
    _s("SRV4", "Jonah", "Feld", "Server", 17.5, True),
    _s("SRV5", "Lena", "Castillo", "Server", 18.0, True),
    _s("BAR1", "Theo", "Nakamura", "Bartender", 17.0, True),
    _s("BAR2", "Ines", "Duarte", "Bartender", 18.0, True),
    _s("BAR3", "Calvin", "Shaw", "Bartender", 19.0, True),
    _s("HOST1", "Zoe", "Bennett", "Host", 17.0),
    _s("HOST2", "Amir", "Haddad", "Host", 18.0),
    _s("COOK1", "Rosa", "Villanueva", "Line Cook", 24.0),
    _s("COOK2", "Marcus", "Bell", "Line Cook", 26.0),
    _s("COOK3", "Hana", "Sato", "Line Cook", 28.0),
    _s("SOUS1", "Elliot", "Park", "Sous Chef", 34.0),
    _s("DISH1", "Luis", "Ortega", "Dishwasher", 18.0),
    _s("DISH2", "Nadia", "Petrov", "Dishwasher", 19.0),
    _s("BUS1", "Sam", "Whitaker", "Busser", 16.0),
    _s("BUS2", "Dana", "Ibrahim", "Busser", 16.5),
)
# Exists only in timecards (never returned by search_team_members): unmapped-member path.
TEMP_SERVER = StaffMember("TM_TEMP_EVENT", "Riley", "Quinn", "Server", 1800, True, False)
STAFF_BY_ID = {s.id: s for s in (*STAFF, TEMP_SERVER)}
_POOL: dict[str, tuple[StaffMember, ...]] = {
    role: tuple(s for s in STAFF if s.role == role) for role in {s.role for s in STAFF}
}

# ---------------------------------------------------------------- demand

# weekday() -> (brunch, lunch, dinner) base orders; Monday is a light dinner-only night.
_BASE: dict[int, tuple[int, int, int]] = {
    0: (0, 0, 66),
    1: (0, 0, 86),
    2: (0, 0, 92),
    3: (0, 14, 88),
    4: (0, 24, 160),
    5: (58, 0, 140),
    6: (62, 0, 80),
}
_MONTH_FACTOR = {1: 0.85, 2: 0.95, 5: 1.03, 6: 1.08, 7: 1.08, 8: 1.08, 12: 1.08}
GROWTH_ANCHOR = date(2026, 1, 1)
YOY_GROWTH = 1.07


def season_factor(d: date) -> float:
    f = _MONTH_FACTOR.get(d.month, 1.0)
    if d.month == 12 and 15 <= d.day <= 23:
        f = 1.2  # holiday bump
    return f


def growth_factor(d: date) -> float:
    return float(YOY_GROWTH ** ((d - GROWTH_ANCHOR).days / 365.0))


def is_snowstorm(d: date) -> bool:
    """The Saturday on/after Jan 24 each year (-60% demand)."""
    if d.month != 1:
        return False
    first = date(d.year, 1, 24)
    return d == first + timedelta(days=(5 - first.weekday()) % 7)


def is_private_event(d: date) -> bool:
    """The Tuesday on/after May 12 each year: one $4,800 buyout order."""
    if d.month != 5:
        return False
    first = date(d.year, 5, 12)
    return d == first + timedelta(days=(1 - first.weekday()) % 7)


@dataclass(frozen=True, slots=True)
class DayPlan:
    date: date
    is_open: bool
    brunch: int
    lunch: int
    dinner: int
    planned_brunch: int  # what staffing was scheduled for (no noise, no snowstorm)
    planned_lunch: int
    planned_dinner: int
    snowstorm: bool
    private_event: bool

    @property
    def total(self) -> int:
        return self.brunch + self.lunch + self.dinner


@lru_cache(maxsize=1024)
def day_plan(d: date) -> DayPlan:
    base = _BASE.get(d.weekday())
    if base is None:
        return DayPlan(d, False, 0, 0, 0, 0, 0, 0, False, False)
    rng = random.Random(f"{d}:plan")
    level = season_factor(d) * growth_factor(d)
    snow = is_snowstorm(d)
    day_noise = rng.gauss(0, 0.06)
    realized: list[int] = []
    planned: list[int] = []
    for b in base:
        p = round(b * level)
        n = (
            0
            if b == 0
            else max(
                0, round(b * level * (1 + day_noise + rng.gauss(0, 0.03)) * (0.4 if snow else 1.0))
            )
        )
        planned.append(p)
        realized.append(n)
    return DayPlan(
        d, True, realized[0], realized[1], realized[2], planned[0], planned[1], planned[2],
        snow, is_private_event(d),
    )  # fmt: skip


# ---------------------------------------------------------------- schedule


@dataclass(frozen=True, slots=True)
class ShiftSpec:
    staff_id: str
    role: str
    start: datetime  # tz-aware, America/New_York
    end: datetime
    break_start: datetime | None  # 30-minute unpaid meal break when shift > 6h


def local_dt(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ) + timedelta(hours=hour, minutes=minute)


# role -> (start_h, start_m, end_h, end_m) per service
_DINNER = {
    "Server": (16, 0, 23, 0), "Bartender": (16, 0, 23, 30), "Host": (16, 0, 22, 30),
    "Busser": (17, 0, 23, 0), "Line Cook": (14, 0, 22, 30), "Dishwasher": (16, 0, 23, 0),
}  # fmt: skip
_BRUNCH = {
    "Server": (9, 0, 15, 30), "Bartender": (9, 30, 15, 30), "Host": (9, 30, 15, 30),
    "Busser": (9, 30, 15, 30), "Line Cook": (8, 30, 15, 30), "Dishwasher": (9, 0, 15, 30),
}  # fmt: skip
_LUNCH = {"Server": (10, 30, 16, 0), "Line Cook": (10, 30, 16, 0), "Dishwasher": (11, 0, 16, 0)}


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _pick(
    role: str, k: int, rng: random.Random, used: set[str], used_by_role: set[str]
) -> list[StaffMember]:
    pool = list(_POOL[role])
    fresh = [s for s in pool if s.id not in used_by_role]
    rng.shuffle(fresh)
    chosen = fresh[:k]
    if len(chosen) < k:  # doubles (non-overlapping services) when the pool runs short
        rest = [s for s in pool if s.id in used_by_role]
        rng.shuffle(rest)
        chosen += rest[: k - len(chosen)]
    for s in chosen:
        used_by_role.add(s.id)
        used.add(s.id)
    return chosen


@lru_cache(maxsize=1024)
def shifts_for_day(d: date) -> tuple[ShiftSpec, ...]:
    plan = day_plan(d)
    rng = random.Random(f"{d}:staff")
    shifts: list[ShiftSpec] = []

    def add(s: StaffMember, hours: tuple[int, int, int, int], jitter: bool = True) -> None:
        sh, sm, eh, em = hours
        start = local_dt(d, sh, sm) + timedelta(
            minutes=rng.choice((-15, 0, 0, 15)) if jitter else 0
        )
        end = local_dt(d, eh, em) + timedelta(minutes=rng.choice((0, 0, 15, 30)) if jitter else 0)
        brk = None
        if end - start > timedelta(hours=6):
            brk = start + (end - start) / 2 - timedelta(minutes=15)
            brk = brk.replace(second=0, microsecond=0)
        shifts.append(ShiftSpec(s.id, s.role, start, end, brk))

    if not plan.is_open:
        # Closed day: prep only (sous + one line cook), no service.
        add(STAFF_BY_ID["TM_SOUS1"], (10, 0, 15, 30), jitter=False)
        add(rng.choice(_POOL["Line Cook"]), (10, 0, 16, 0), jitter=False)
        return tuple(shifts)

    used: set[str] = set()
    by_role: dict[str, set[str]] = {}

    def crew(table: dict[str, tuple[int, int, int, int]], counts: dict[str, int]) -> None:
        for role, k in counts.items():
            for s in _pick(role, k, rng, used, by_role.setdefault(role, set())):
                add(s, table[role])

    if plan.planned_dinner:
        n = plan.planned_dinner
        crew(_DINNER, {
            "Server": _clamp(math.ceil(n / 28), 2, 5),
            "Bartender": 1 if n < 100 else 2,
            "Host": 1 if n < 140 else 2,
            "Line Cook": 2 if n < 120 else 3,
            "Dishwasher": 1 if n < 110 else 2,
            "Busser": 1 if n < 85 else 2,
        })  # fmt: skip
    if plan.planned_brunch:
        n = plan.planned_brunch
        crew(_BRUNCH, {
            "Server": _clamp(math.ceil(n / 22), 2, 4), "Bartender": 1, "Host": 1,
            "Line Cook": 2, "Dishwasher": 1, "Busser": 1,
        })  # fmt: skip
    if plan.planned_lunch:
        n = plan.planned_lunch
        crew(_LUNCH, {"Server": math.ceil(n / 16), "Line Cook": 1, "Dishwasher": 1})
    if d.weekday() != 6:  # sous chef works Tue-Sat (and Monday prep)
        add(STAFF_BY_ID["TM_SOUS1"], (9, 30, 18, 0) if d.weekday() == 5 else (11, 0, 19, 30))
    # Contract server who is not in the Team directory.
    if (d.weekday() in (4, 5) and rng.random() < 0.35) or plan.private_event:
        add(TEMP_SERVER, (17, 0, 22, 30))

    shifts.sort(key=lambda s: (s.start, s.staff_id))
    return tuple(shifts)


def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)
