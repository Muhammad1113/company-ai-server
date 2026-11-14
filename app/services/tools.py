"""Chhote deterministic tools (LLM nahi, network nahi). Pure module: asaani se test hota hai.

Kyun: 4B model arithmetic, date aur unit conversion mein ghalti karta hai. Ye kaam code se hote hain,
phir natija model ko "verified fact" ki tarah diya jata hai (router.build_tool_prompt).

run_tools(message, now) -> list[ToolResult]   (khali list = koi tool nahi laga)
"""
from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

__all__ = ["ToolResult", "now_in", "format_now", "safe_eval", "run_tools"]


@dataclass(frozen=True)
class ToolResult:
    name: str
    summary: str          # ek line; model ko authoritative fact ke taur par jati hai


# ---------------------------------------------------------------- date / time
def now_in(tz_name: str = "Asia/Karachi") -> datetime:
    """Windows par IANA database aksar nahi hota (pip install tzdata). Fallback: UTC+5 (Pakistan, DST nahi)."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone(timedelta(hours=5), name="PKT"))


def format_now(now: datetime) -> str:
    tz = now.tzname() or ""
    return f"{now.strftime('%A, %d %B %Y, %H:%M')} {tz}".strip()


_TIME_DATE = re.compile(
    r"\b(what(?:'s| is) the (?:current )?(?:time|date|day)|current (?:time|date)|today'?s date|time now|"
    r"what day is (?:it|today)|which day|"
    r"kitne baje|time kya|kya time|aaj (?:ki|ka|kya) (?:tarikh|tareekh|date|din)|aaj kaun sa din|"
    r"(?:tarikh|tareekh|date) kya|abhi (?:kya )?(?:time|waqt)|abhi kitne)\b",
    re.I,
)


def _datetime_tool(text: str, now: datetime) -> ToolResult | None:
    if _TIME_DATE.search(text):
        return ToolResult("datetime", f"Current local date and time: {format_now(now)}.")
    return None


# ------------------------------------------------------------------ calculator
_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max,
    "log": math.log, "ln": math.log, "log10": math.log10,
}
_CONST = {"pi": math.pi, "e": math.e}
_LIMIT = 1e300
_MAX_LEN = 200


class CalcError(ValueError):
    pass


def safe_eval(expr: str) -> float | int:
    """Sirf numbers, + - * / // % **, brackets aur chand math functions. eval() kabhi nahi."""
    if len(expr) > _MAX_LEN:
        raise CalcError("expression too long")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise CalcError("syntax") from exc

    def check(v):
        if isinstance(v, (int, float)) and abs(v) > _LIMIT:
            raise CalcError("number too large")
        return v

    def ev(node, depth=0):
        if depth > 30:
            raise CalcError("too deep")
        if isinstance(node, ast.Expression):
            return ev(node.body, depth + 1)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return check(node.value)
        if isinstance(node, ast.Name) and node.id in _CONST:
            return _CONST[node.id]
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return check(_UN[type(node.op)](ev(node.operand, depth + 1)))
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left, depth + 1), ev(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                if abs(b) > 100:
                    raise CalcError("exponent too large")
                try:
                    return check(float(a) ** float(b))     # float: bade int power se CPU nahi jalta
                except (OverflowError, ZeroDivisionError) as exc:
                    raise CalcError("overflow") from exc
            op = _BIN.get(type(node.op))
            if op is None:
                raise CalcError("operator not allowed")
            try:
                return check(op(a, b))
            except ZeroDivisionError as exc:
                raise CalcError("division by zero") from exc
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            if len(node.args) > 10:
                raise CalcError("too many arguments")
            try:
                return check(_FUNCS[node.func.id](*[ev(a, depth + 1) for a in node.args]))
            except (ValueError, TypeError, OverflowError) as exc:
                raise CalcError("bad arguments") from exc
        raise CalcError("not allowed")

    return ev(tree)


def _fmt_num(x: float | int) -> str:
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        x = int(x)
    if isinstance(x, int):
        return f"{x:,}"
    return f"{x:,.6f}".rstrip("0").rstrip(".")


_FILLER = re.compile(
    r"\b(calculate|calc|compute|solve|what is|what's|whats|how much is|hisab karo|hisab|hisaab|nikalo|batao|"
    r"kitne hote hain|kitna hota hai|kitna hoga|kya hoga|equals|equal to|please|plz|karo|kar do)\b|[=?]",
    re.I,
)
_KEYWORD = re.compile(r"\b(calculate|calc|compute|solve|hisab|hisaab|how much is)\b", re.I)
_PCT_OF = re.compile(r"^(\d+(?:\.\d+)?)\s*%\s*(?:of|ka)\s*(\d+(?:\.\d+)?)$", re.I)
_PCT_KA = re.compile(r"^(\d+(?:\.\d+)?)\s*ka\s*(\d+(?:\.\d+)?)\s*%$", re.I)
_ALLOWED = re.compile(r"^(?:[\d\s+\-*/().,^]|sqrt|abs|round|min|max|log10|log|ln|pi)+$")


def _calc_tool(text: str) -> ToolResult | None:
    if len(text) > 120:
        return None
    explicit = bool(_KEYWORD.search(text))
    body = _FILLER.sub(" ", text)
    body = body.replace("×", "*").replace("÷", "/").replace("−", "-")
    body = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "*", body)
    body = re.sub(r"(?<=\d),(?=\d{3}\b)", "", body)          # 1,000 -> 1000
    body = re.sub(r"\s+", " ", body).strip()

    expr = None
    m = _PCT_OF.match(body)
    if m:
        expr, explicit = f"({m.group(1)}/100)*{m.group(2)}", True
    else:
        m = _PCT_KA.match(body)
        if m:
            expr, explicit = f"({m.group(1)}/100)*{m.group(2)}", True
    if expr is None:
        body = body.replace("^", "**")
        if not body or not _ALLOWED.match(body) or not re.search(r"\d", body):
            return None
        has_op = re.search(r"[+*/()]|\s-\s|sqrt|log|ln|min|max|abs|round", body) or (explicit and "-" in body)
        if not has_op:
            return None                                        # "2024", "0300-1234567", "11-11-2026"
        expr = body
    try:
        value = safe_eval(expr)
    except CalcError:
        return None
    shown = body.replace("**", "^") if m is None else text.strip()
    return ToolResult("calculator", f"{shown.strip()} = {_fmt_num(value)}")


# ------------------------------------------------------------- unit conversion
_LINEAR = {
    "length": {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "in": 0.0254, "ft": 0.3048, "mile": 1609.344},
    "mass": {"mg": 1e-6, "g": 1e-3, "kg": 1.0, "lb": 0.45359237, "oz": 0.028349523125},
    "volume": {"ml": 1e-3, "l": 1.0, "floz": 0.0295735295625, "tsp": 0.00492892159375, "tbsp": 0.01478676478125},
    "pressure": {"pa": 1.0, "kpa": 1000.0, "mmhg": 133.322387415, "atm": 101325.0, "bar": 100000.0, "psi": 6894.757293168},
}
_ALIAS = {
    "inch": "in", "inches": "in", "feet": "ft", "foot": "ft", "miles": "mile", "mi": "mile",
    "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "lbs": "lb", "pound": "lb", "pounds": "lb", "gram": "g", "grams": "g", "ounce": "oz", "ounces": "oz",
    "litre": "l", "liter": "l", "litres": "l", "liters": "l", "cc": "ml", "millilitre": "ml", "milliliter": "ml",
    "meter": "m", "metre": "m", "meters": "m", "metres": "m", "centimeter": "cm", "centimetre": "cm",
    "millimeter": "mm", "millimetre": "mm", "kilometer": "km", "kilometre": "km",
    "celsius": "c", "centigrade": "c", "fahrenheit": "f", "kelvin": "k",
    "french": "fr", "mmhg": "mmhg",
}
_UNIT_NAME = {"in": "inch", "ft": "ft", "mile": "mile", "lb": "lb", "oz": "oz", "l": "L", "ml": "mL", "mmhg": "mmHg",
              "kpa": "kPa", "pa": "Pa", "c": "°C", "f": "°F", "k": "K", "fr": "Fr"}
_CONVERT = re.compile(
    r"(?P<v>-?\d+(?:\.\d+)?)\s*°?\s*(?P<a>[a-zA-Z]+)\s*(?:to|in|into|ko|se|=|->)\s*°?\s*(?P<b>[a-zA-Z]+)", re.I
)


_KNOWN = {"c", "f", "k", "fr"} | {u for d in _LINEAR.values() for u in d}


def _unit(u: str) -> str:
    u = u.lower().strip("°")
    u = _ALIAS.get(u, u)
    if u not in _KNOWN and u.endswith("s"):          # "millimeters", "inches", "mmhgs"
        u = _ALIAS.get(u[:-1], u[:-1])
    return u


def _label(u: str) -> str:
    return _UNIT_NAME.get(u, u)


def _to_celsius(v: float, u: str) -> float:
    return v if u == "c" else (v - 32) * 5 / 9 if u == "f" else v - 273.15


def _from_celsius(c: float, u: str) -> float:
    return c if u == "c" else c * 9 / 5 + 32 if u == "f" else c + 273.15


def _convert_tool(text: str) -> ToolResult | None:
    if len(text) > 240:
        return None
    for m in _CONVERT.finditer(text):
        res = _convert_match(m)
        if res:
            return res
    return None


def _convert_match(m) -> ToolResult | None:
    v, a, b = float(m.group("v")), _unit(m.group("a")), _unit(m.group("b"))

    if a in ("c", "f", "k") and b in ("c", "f", "k"):
        out = _from_celsius(_to_celsius(v, a), b)
    elif {a, b} == {"fr", "mm"}:                       # French catheter size: 1 Fr = 1/3 mm
        out = v / 3 if a == "fr" else v * 3
    else:
        out = None
        for units in _LINEAR.values():
            if a in units and b in units:
                out = v * units[a] / units[b]
                break
        if out is None:
            return None
    return ToolResult("unit_conversion", f"{_fmt_num(v)} {_label(a)} = {_fmt_num(round(out, 4))} {_label(b)}")


# ------------------------------------------------------------------------- BMI
_W = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|kgs|kilo|kilos|lb|lbs|pounds?)\b", re.I)
_H = re.compile(r"(\d+(?:\.\d+)?)\s*(cm|m|meters?|metres?)\b", re.I)


def _bmi_tool(text: str) -> ToolResult | None:
    if not re.search(r"\bbmi\b", text, re.I):
        return None
    w, h = _W.search(text), _H.search(text)
    if not (w and h):
        return None
    kg = float(w.group(1)) * (0.45359237 if w.group(2).lower().startswith(("lb", "pound")) else 1.0)
    metres = float(h.group(1)) / (100 if h.group(2).lower() == "cm" else 1)
    if not (2 <= kg <= 500 and 0.5 <= metres <= 2.8):
        return None
    bmi = kg / metres ** 2
    cat = "underweight" if bmi < 18.5 else "normal" if bmi < 25 else "overweight" if bmi < 30 else "obese"
    return ToolResult(
        "bmi",
        f"BMI = {bmi:.1f} kg/m² ({kg:.1f} kg, {metres * 100:.0f} cm); WHO standard category: {cat}. "
        "(Asian-population cut-offs are lower: overweight from 23.)",
    )


# ------------------------------------------------------------------ AQL sample size (ISO 2859-1, level II)
_AQL_TABLE = [   # (lot size upto, sample size): single sampling, general inspection level II
    (8, 2), (15, 3), (25, 5), (50, 8), (90, 13), (150, 20), (280, 32), (500, 50), (1200, 80),
    (3200, 125), (10000, 200), (35000, 315), (150000, 500), (500000, 800),
]


def aql_sample_size(lot: int) -> int:
    for upto, n in _AQL_TABLE:
        if lot <= upto:
            return min(n, lot)
    return 1250


def _aql_tool(text: str) -> ToolResult | None:
    t = text.lower()
    if not re.search(r"\b(sample size|sampling|sample)\b", t):
        return None
    if re.search(r"\blevel\s*(?:i|1|iii|3|s-?\d)\b", t):          # sirf level II (default) ka table
        return None
    m = re.search(r"\b(?:lot|batch|consignment)\s*(?:size)?\s*(?:of|is|=|:)?\s*([\d,]{1,9})\b", t) \
        or re.search(r"\b([\d,]{1,9})\s*(?:pcs|pieces|units|items|gloves|pairs)\b", t)
    if not m:
        return None
    try:
        lot = int(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if lot < 2:
        return None
    return ToolResult(
        "aql_sample_size",
        f"ISO 2859-1 single sampling, general inspection level II: lot of {lot:,} pieces -> sample size {aql_sample_size(lot)}.",
    )


# ------------------------------------------------------------------ entrypoint
def run_tools(message: str, now: datetime | None = None) -> list[ToolResult]:
    """Message par jo tools lagte hain (max 2). Pehle sab se khaas (BMI, unit), phir calculator, phir date/time."""
    now = now or now_in()
    out: list[ToolResult] = []
    for fn in (_bmi_tool, _convert_tool, _aql_tool, _calc_tool):
        res = fn(message)
        if res:
            out.append(res)
    dt = _datetime_tool(message, now)
    if dt:
        out.append(dt)
    return out[:2]
