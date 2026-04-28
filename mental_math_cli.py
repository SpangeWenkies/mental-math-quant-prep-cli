#!/usr/bin/env python3
"""Timed mental-math practice CLI.

Built from the mental-math portion of the provided cram guide. Sequence drills are
intentionally excluded.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable


SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_FILE = SCRIPT_DIR / "mental_math_history.jsonl"


@dataclass(frozen=True)
class Question:
    prompt: str
    answer: Fraction
    category_key: str
    category_name: str
    fastest_method: str
    answer_style: str = "auto"
    choices: tuple[Fraction, ...] = field(default_factory=tuple)
    display_prompt: str | None = None


@dataclass
class Result:
    question: Question
    user_answer: str | None
    elapsed: float
    correct: bool
    skipped: bool


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    focus: str
    generator: Callable[[random.Random, str], Question]


@dataclass(frozen=True)
class Preset:
    name: str
    questions: int
    seconds: float
    difficulty: str
    mode: str
    description: str
    total_seconds: float | None = None
    benchmark_low_pct: float | None = None
    benchmark_high_pct: float | None = None
    benchmark_low_score: int | None = None
    benchmark_high_score: int | None = None
    negative_marking: bool = False
    benchmark_reference_questions: int | None = None


PRESETS: dict[str, Preset] = {
    "quick": Preset(
        "Quick all-types sprint",
        12,
        15,
        "easy",
        "multiple-choice",
        "One pass through every mental-math family.",
    ),
    "core": Preset(
        "Core speed test",
        24,
        12,
        "mixed",
        "multiple-choice",
        "Balanced interview-style timed practice.",
    ),
    "interview": Preset(
        "Interview pressure test",
        36,
        8,
        "hard",
        "multiple-choice",
        "Fast pacing; skip ugly questions instead of donating time.",
    ),
    "real": Preset(
        "Real 8-minute simulation",
        80,
        0,
        "hard",
        "multiple-choice",
        "80 multiple-choice questions in 8 minutes; benchmark band is usually 55-65%.",
        total_seconds=8 * 60,
        benchmark_low_score=55,
        benchmark_high_score=65,
        negative_marking=True,
        benchmark_reference_questions=80,
    ),
    "endurance": Preset(
        "Endurance simulation",
        60,
        10,
        "mixed",
        "multiple-choice",
        "Longer run for pacing and recurring-error detection.",
    ),
    "weak": Preset(
        "Weak-spot drill",
        24,
        10,
        "mixed",
        "multiple-choice",
        "Uses your saved history to bias toward weak categories.",
    ),
}


REAL_CATEGORY_POOL = [
    "fractions_add_sub",
    "fractions_add_sub",
    "fractions_add_sub",
    "fractions_add_sub",
    "fractions_mul_div",
    "fractions_mul_div",
    "fractions_mul_div",
    "fractions_mul_div",
    "decimals_add_sub",
    "decimals_add_sub",
    "decimals_mul_div",
    "decimals_mul_div",
    "multiplication_decomp",
    "multiplication_decomp",
    "division_factor",
    "division_factor",
    "add_sub_2digit",
    "add_sub_3digit",
    "multiplication_near_base",
    "multiplication_x11",
    "reverse_equations",
]


def nearest_ten(n: int) -> int:
    return int(round(n / 10) * 10)


def fmt_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def stacked_fraction_lines(value: Fraction) -> list[str]:
    if value.denominator == 1:
        return [str(value.numerator)]
    numerator = str(value.numerator)
    denominator = str(value.denominator)
    width = max(len(numerator), len(denominator))
    return [
        numerator.center(width),
        ("─" * width),
        denominator.center(width),
    ]


def value_block(value: Fraction, *, fraction_style: str = "stacked") -> list[str]:
    if value.denominator == 1:
        text = str(value.numerator)
        return [" " * len(text), text, " " * len(text)]
    if fraction_style == "slash":
        text = fmt_fraction(value)
        return [" " * len(text), text, " " * len(text)]
    lines = stacked_fraction_lines(value)
    if len(lines) == 1:
        text = lines[0]
        return [" " * len(text), text, " " * len(text)]
    return lines


def join_expression_blocks(left: Fraction, operator: str, right: Fraction) -> str:
    display_operator = {"x": "×", "/": ":"}.get(operator, operator)
    left_block = value_block(left)
    right_block = value_block(right)
    operator_block = [" ", display_operator, " "]
    lines = [
        f"{left_block[row]} {operator_block[row]} {right_block[row]}".rstrip()
        for row in range(3)
    ]
    return "\n".join(lines)


def join_equation_blocks(left: str, operator: str, right: Fraction) -> str:
    display_operator = {"x": "×", "/": ":"}.get(operator, operator)
    left_width = len(left)
    left_block = [" " * left_width, left, " " * left_width]
    right_block = value_block(right)
    operator_block = [" ", display_operator, " "]
    lines = [
        f"{left_block[row]} {operator_block[row]} {right_block[row]}".rstrip()
        for row in range(3)
    ]
    return "\n".join(lines)


def finite_decimal_places(denominator: int) -> int | None:
    d = denominator
    twos = 0
    fives = 0
    while d % 2 == 0:
        twos += 1
        d //= 2
    while d % 5 == 0:
        fives += 1
        d //= 5
    if d != 1:
        return None
    return max(twos, fives)


def fmt_decimal(value: Fraction, min_places: int = 0) -> str:
    places = finite_decimal_places(value.denominator)
    if places is None:
        return fmt_fraction(value)
    places = max(places, min_places)
    sign = "-" if value < 0 else ""
    value = abs(value)
    scale = 10**places
    scaled = value.numerator * scale // value.denominator
    integer = scaled // scale
    decimal = scaled % scale
    if places == 0:
        return f"{sign}{integer}"
    text = f"{sign}{integer}.{decimal:0{places}d}"
    if min_places == 0:
        text = text.rstrip("0").rstrip(".")
    return text


def fmt_number(value: Fraction, style: str = "auto") -> str:
    if style == "fraction":
        return fmt_fraction(value)
    if style == "decimal":
        return fmt_decimal(value)
    if value.denominator == 1:
        return str(value.numerator)
    places = finite_decimal_places(value.denominator)
    if places is not None and places <= 3:
        return f"{fmt_fraction(value)} ({fmt_decimal(value)})"
    return fmt_fraction(value)


def fmt_number_display(value: Fraction, style: str = "auto") -> str:
    if style == "fraction":
        return "\n".join(stacked_fraction_lines(value))
    return fmt_number(value, style)


def dec_from_int(raw: int, places: int) -> Fraction:
    return Fraction(raw, 10**places)


def fmt_dec_int(raw: int, places: int) -> str:
    return fmt_decimal(dec_from_int(raw, places), min_places=1)


def parse_number(raw: str) -> Fraction:
    text = raw.strip().lower()
    text = text.replace("−", "-").replace("–", "-")
    text = text.replace(" ", " ").strip()
    if not text:
        raise ValueError("empty answer")
    if text.endswith("%"):
        return parse_number(text[:-1]) / 100
    if "," in text and "." not in text:
        parts = text.split(",")
        if len(parts) == 2 and 0 < len(parts[1]) <= 2:
            text = ".".join(parts)
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", "")
    mixed = re.fullmatch(r"([+-]?\d+)\s+(\d+)/(\d+)", text)
    if mixed:
        whole = int(mixed.group(1))
        numerator = int(mixed.group(2))
        denominator = int(mixed.group(3))
        sign = -1 if whole < 0 else 1
        return Fraction(whole, 1) + sign * Fraction(numerator, denominator)
    return Fraction(text)


def answer_display(question: Question) -> str:
    if question.choices:
        labels = "1234"
        for i, choice in enumerate(question.choices):
            if choice == question.answer:
                return fmt_number(choice, question.answer_style)
    return fmt_number(question.answer, question.answer_style)


def clean_prompt(prompt: str) -> str:
    return prompt.replace("\n", " | ")


def display_user_answer(question: Question, raw_answer: str | None) -> str:
    if raw_answer is None or not raw_answer.strip():
        return "(no answer)"
    answer = raw_answer.strip()
    if question.choices:
        upper = answer.upper()
        if upper in {"1", "2", "3", "4"}:
            return fmt_number(question.choices[int(upper) - 1], question.answer_style)
        if upper in {"A", "B", "C", "D"}:
            return fmt_number(question.choices["ABCD".index(upper)], question.answer_style)
    try:
        return fmt_number(parse_number(answer), question.answer_style)
    except (ValueError, ZeroDivisionError):
        return answer


def displayify_inline_prompt(prompt: str) -> str:
    return prompt.replace(" / ", " : ").replace(" x ", " × ")


def question_text(question: Question) -> str:
    return question.display_prompt or displayify_inline_prompt(question.prompt)


def format_choice_text(index: int, value: Fraction, style: str) -> str:
    return f"{index}) {fmt_number(value, style)}"


def render_question_block(question: Question) -> str:
    parts = [question_text(question)]
    if question.choices:
        parts.append("")
        parts.extend(render_choice_blocks(question))
    return "\n".join(parts)


def render_choice_blocks(question: Question) -> list[str]:
    if not question.choices:
        return []
    if question.answer_style == "fraction" and any(choice.denominator != 1 for choice in question.choices):
        columns: list[tuple[list[str], int]] = []
        for index, choice in enumerate(question.choices, 1):
            prefix = f"{index}) "
            block = value_block(choice, fraction_style="stacked")
            width = max(len(line) for line in block)
            column_lines = [
                (" " * len(prefix)) + block[0].center(width),
                prefix + block[1].center(width),
                (" " * len(prefix)) + block[2].center(width),
            ]
            columns.append((column_lines, len(prefix) + width))
        rows: list[str] = []
        for row in range(3):
            rows.append(
                "  "
                + "    ".join(
                    column_lines[row].ljust(column_width)
                    for column_lines, column_width in columns
                ).rstrip()
            )
        return rows
    choices = [
        format_choice_text(index, choice, question.answer_style)
        for index, choice in enumerate(question.choices, 1)
    ]
    return ["  " + "    ".join(choices)]


def print_labeled_block(label: str, text: str, *, indent: str = "     ") -> None:
    lines = text.splitlines() or [text]
    if len(lines) == 1:
        print(f"{label}{lines[0]}")
        return
    print(label.rstrip())
    for line in lines:
        print(f"{indent}{line}")


def check_answer(question: Question, raw_answer: str | None) -> bool:
    if raw_answer is None:
        return False
    answer = raw_answer.strip()
    if not answer:
        return False
    if question.choices:
        choice = answer.upper()
        if choice in {"1", "2", "3", "4"}:
            return question.choices[int(choice) - 1] == question.answer
        if choice in {"A", "B", "C", "D"}:
            return question.choices["ABCD".index(choice)] == question.answer
    try:
        return parse_number(answer) == question.answer
    except (ValueError, ZeroDivisionError):
        return False


def round_method(a: int, b: int, op: str, anchor: int) -> str:
    delta = b - anchor
    if op == "+":
        if delta == 0:
            return f"Add directly left-to-right: {a} + {b} = {a + b}."
        correction = "add" if delta > 0 else "subtract"
        return (
            f"Round {b} to {anchor}: {a} + {anchor} = {a + anchor}; "
            f"{correction} {abs(delta)} -> {a + b}."
        )
    if delta == 0:
        return f"Subtract directly left-to-right: {a} - {b} = {a - b}."
    if delta > 0:
        return (
            f"Round {b} down to {anchor}: {a} - {anchor} = {a - anchor}; "
            f"subtract the extra {delta} -> {a - b}."
        )
    return (
        f"Round {b} up to {anchor}: {a} - {anchor} = {a - anchor}; "
        f"add back {abs(delta)} -> {a - b}."
    )


def gen_add_sub_2(rng: random.Random, difficulty: str) -> Question:
    op = rng.choice(["+", "-"])
    if op == "+":
        a = rng.randint(20, 99)
        if rng.random() < 0.75:
            anchor = rng.choice([30, 40, 50, 60, 70, 80, 90])
            offset = rng.choice([-3, -2, -1, 1, 2, 3])
            b = anchor + offset
        else:
            b = rng.randint(20, 99)
            anchor = nearest_ten(b)
        answer = Fraction(a + b)
        prompt = f"{a} + {b}"
    else:
        a = rng.randint(65, 160 if difficulty == "hard" else 130)
        anchor = rng.choice([30, 40, 50, 60, 70, 80, 90])
        offset = rng.choice([-3, -2, -1, 1, 2, 3])
        b = min(anchor + offset, a - 1)
        if b < 10:
            b = rng.randint(10, min(99, a - 1))
            anchor = nearest_ten(b)
        answer = Fraction(a - b)
        prompt = f"{a} - {b}"
    return Question(
        prompt,
        answer,
        "add_sub_2digit",
        "2-digit addition/subtraction",
        round_method(a, b, op, anchor),
    )


def gen_add_sub_3(rng: random.Random, difficulty: str) -> Question:
    op = rng.choice(["+", "-"])
    base_choices = [100, 200, 300, 400, 500] if difficulty != "easy" else [100, 200, 300]
    anchor = rng.choice(base_choices)
    offset = rng.choice([-9, -8, -6, -4, -2, -1, 1, 2, 4, 6, 8, 9])
    b = anchor + offset
    if op == "+":
        a = rng.randint(120, 850)
        answer = Fraction(a + b)
        prompt = f"{a} + {b}"
    else:
        a = rng.randint(max(anchor + 40, 180), 950)
        answer = Fraction(a - b)
        prompt = f"{a} - {b}"
    return Question(
        prompt,
        answer,
        "add_sub_3digit",
        "3-digit compensation arithmetic",
        round_method(a, b, op, anchor),
    )


def gen_mult_decomp(rng: random.Random, difficulty: str) -> Question:
    a = rng.randint(12, 49 if difficulty != "hard" else 79)
    b = rng.randint(12, 29 if difficulty == "easy" else 69)
    if b % 10 == 0:
        b += rng.choice([1, 2, 3])
    tens = b // 10 * 10
    ones = b % 10
    answer = Fraction(a * b)
    method = (
        f"Decompose {b} as {tens} + {ones}: "
        f"{a} x {tens} = {a * tens}, {a} x {ones} = {a * ones}; "
        f"add -> {a * b}."
    )
    return Question(
        f"{a} x {b}",
        answer,
        "multiplication_decomp",
        "2-digit multiplication by decomposition",
        method,
    )


def gen_mult_near_base(rng: random.Random, difficulty: str) -> Question:
    case = rng.choice(["symmetric", "near_100"])
    if case == "symmetric":
        base = rng.choice([50, 100] if difficulty != "easy" else [50])
        delta = rng.randint(1, 9 if base == 50 else 12)
        a = base - delta
        b = base + delta
        answer = Fraction(a * b)
        method = (
            f"Use difference of squares: ({base} - {delta})({base} + {delta}) "
            f"= {base * base} - {delta * delta} = {a * b}."
        )
    else:
        delta = rng.randint(1, 9)
        sign = rng.choice([-1, 1])
        a = 100 + sign * delta
        b = rng.randint(12, 99 if difficulty != "easy" else 49)
        answer = Fraction(a * b)
        symbol = "+" if sign > 0 else "-"
        method = (
            f"Treat {a} as 100 {symbol} {delta}: "
            f"100 x {b} = {100 * b}; {delta} x {b} = {delta * b}; "
            f"adjust -> {a * b}."
        )
    return Question(
        f"{a} x {b}",
        answer,
        "multiplication_near_base",
        "near-base multiplication",
        method,
    )


def gen_times_11(rng: random.Random, difficulty: str) -> Question:
    n = rng.randint(12, 99)
    tens, ones = divmod(n, 10)
    digit_sum = tens + ones
    answer = Fraction(n * 11)
    if digit_sum < 10:
        method = (
            f"For {n} x 11, keep {tens} and {ones} outside and put "
            f"{tens}+{ones}={digit_sum} in the middle -> {n * 11}."
        )
    else:
        method = (
            f"For {n} x 11, {tens}+{ones}={digit_sum}; put {digit_sum % 10} "
            f"in the middle and carry {digit_sum // 10} to the first digit -> {n * 11}."
        )
    return Question(
        f"{n} x 11",
        answer,
        "multiplication_x11",
        "times-11 trick",
        method,
    )


def gen_division_factor(rng: random.Random, difficulty: str) -> Question:
    divisors = [6, 7, 8, 9, 11, 12, 13, 14, 16, 18, 24, 25]
    if difficulty == "hard":
        divisors += [27, 32, 36]
    divisor = rng.choice(divisors)
    quotient = rng.randint(2, 12 if difficulty != "hard" else 18)
    dividend = divisor * quotient
    method = (
        f"Reverse the division: ask {divisor} x ? = {dividend}. "
        f"{divisor} x {quotient} = {dividend}, so the answer is {quotient}."
    )
    return Question(
        f"{dividend} / {divisor}",
        Fraction(quotient),
        "division_factor",
        "division by factor structure",
        method,
    )


def gen_fractions_add_sub(rng: random.Random, difficulty: str) -> Question:
    denominators = [2, 3, 4, 5, 6, 8, 9, 10, 12]
    if difficulty == "hard":
        denominators += [15, 16, 18]
    d1, d2 = rng.sample(denominators, 2)
    f1 = Fraction(rng.randint(1, d1 - 1), d1)
    f2 = Fraction(rng.randint(1, d2 - 1), d2)
    op = rng.choice(["+", "-"])
    if op == "-" and f1 < f2:
        f1, f2 = f2, f1
    answer = f1 + f2 if op == "+" else f1 - f2
    lcm = math.lcm(f1.denominator, f2.denominator)
    left = f1.numerator * (lcm // f1.denominator)
    right = f2.numerator * (lcm // f2.denominator)
    combined = left + right if op == "+" else left - right
    method = (
        f"Use common denominator {lcm}: {fmt_fraction(f1)} = {left}/{lcm}, "
        f"{fmt_fraction(f2)} = {right}/{lcm}; combine to {combined}/{lcm} "
        f"and reduce -> {fmt_fraction(answer)}."
    )
    return Question(
        f"{fmt_fraction(f1)} {op} {fmt_fraction(f2)}",
        answer,
        "fractions_add_sub",
        "fraction addition/subtraction",
        method,
        "fraction",
        display_prompt=join_expression_blocks(f1, op, f2),
    )


def gen_fractions_mul_div(rng: random.Random, difficulty: str) -> Question:
    kind = rng.choice(["int_mul", "int_div", "frac_mul", "frac_div"])
    if kind == "int_mul":
        denominator = rng.choice([3, 4, 5, 6, 8, 9, 12])
        whole = denominator * rng.randint(2, 5 if difficulty == "easy" else 9)
        numerator = rng.randint(1, denominator - 1)
        frac = Fraction(numerator, denominator)
        answer = whole * frac
        cancelled = whole // frac.denominator
        method = (
            f"Cancel before multiplying: use {fmt_fraction(frac)}, so "
            f"{whole}/{frac.denominator} = {cancelled}; then "
            f"{cancelled} x {frac.numerator} "
            f"= {fmt_fraction(answer)}."
        )
        prompt = f"{whole} x {fmt_fraction(frac)}"
    elif kind == "int_div":
        numerator = rng.choice([2, 3, 4, 5, 6, 7, 8])
        denominator = rng.choice([3, 4, 5, 6, 7, 8, 9, 12])
        if numerator == denominator:
            denominator += 1
        whole = numerator * rng.randint(2, 8 if difficulty != "easy" else 5)
        frac = Fraction(numerator, denominator)
        answer = Fraction(whole, 1) / frac
        if frac.numerator == 1:
            method = (
                f"Divide by a unit fraction by flipping it: {fmt_fraction(frac)} "
                f"becomes {frac.denominator}. Then {whole} x {frac.denominator} "
                f"= {fmt_fraction(answer)}."
            )
        else:
            cancelled = whole // frac.numerator
            method = (
                f"Divide by a fraction by flipping it: {fmt_fraction(frac)} becomes "
                f"{frac.denominator}/{frac.numerator}. Cancel "
                f"{whole}/{frac.numerator} = {cancelled}, then multiply "
                f"{cancelled} x {frac.denominator} -> {fmt_fraction(answer)}."
            )
        prompt = f"{whole} / ({fmt_fraction(frac)})"
    else:
        d1 = rng.choice([4, 6, 8, 9, 10, 12, 16])
        d2 = rng.choice([3, 4, 5, 6, 8, 12])
        n1 = rng.randint(1, d1 - 1)
        n2 = rng.randint(1, d2 - 1)
        f1 = Fraction(n1, d1)
        f2 = Fraction(n2, d2)
        if kind == "frac_mul":
            answer = f1 * f2
            prompt = f"{fmt_fraction(f1)} x {fmt_fraction(f2)}"
            method = (
                f"Cancel any numerator with any denominator before multiplying. "
                f"Then multiply tops and bottoms and reduce -> {fmt_fraction(answer)}."
            )
        else:
            answer = f1 / f2
            prompt = f"{fmt_fraction(f1)} / ({fmt_fraction(f2)})"
            method = (
                f"Flip the second fraction: {fmt_fraction(f1)} x "
                f"{f2.denominator}/{f2.numerator}. Cancel first, then multiply "
                f"and reduce -> {fmt_fraction(answer)}."
            )
    return Question(
        prompt,
        answer,
        "fractions_mul_div",
        "fraction multiplication/division",
        method,
        "fraction",
        display_prompt=join_expression_blocks(
            Fraction(whole) if kind in {"int_mul", "int_div"} else f1,
            "x" if kind in {"int_mul", "frac_mul"} else "/",
            frac if kind in {"int_mul", "int_div"} else f2,
        ),
    )


def gen_decimals_add_sub(rng: random.Random, difficulty: str) -> Question:
    places1 = rng.choice([1, 2])
    places2 = rng.choice([1, 2])
    max_raw = 250 if difficulty == "easy" else 950
    raw1 = rng.randint(12, max_raw)
    raw2 = rng.randint(5, min(max_raw, raw1 + 200))
    f1 = dec_from_int(raw1, places1)
    f2 = dec_from_int(raw2, places2)
    op = rng.choice(["+", "-"])
    if op == "-" and f1 < f2:
        f1, f2 = f2, f1
        raw1, raw2 = raw2, raw1
        places1, places2 = places2, places1
    answer = f1 + f2 if op == "+" else f1 - f2
    method = (
        f"Line up decimal places or scale both to hundredths: "
        f"{fmt_decimal(f1)} {op} {fmt_decimal(f2)} = {fmt_decimal(answer)}."
    )
    return Question(
        f"{fmt_decimal(f1)} {op} {fmt_decimal(f2)}",
        answer,
        "decimals_add_sub",
        "decimal addition/subtraction",
        method,
        "decimal",
    )


def gen_decimals_mul_div(rng: random.Random, difficulty: str) -> Question:
    kind = rng.choice(["mul", "div"])
    if kind == "mul":
        places1 = rng.choice([1, 2])
        places2 = rng.choice([1, 2])
        upper = 99 if difficulty != "easy" else 49
        raw1 = rng.randint(12, upper)
        raw2 = rng.randint(12, upper)
        f1 = dec_from_int(raw1, places1)
        f2 = dec_from_int(raw2, places2)
        answer = f1 * f2
        raw_product = raw1 * raw2
        total_places = places1 + places2
        method = (
            f"Ignore decimals first: {raw1} x {raw2} = {raw_product}. "
            f"There are {total_places} total decimal places, so restore them "
            f"-> {fmt_decimal(answer)}."
        )
        prompt = f"{fmt_decimal(f1)} x {fmt_decimal(f2)}"
    else:
        divisor_places = rng.choice([1, 2])
        divisor_raw = rng.choice([4, 5, 6, 7, 8, 12, 15, 16, 24, 25])
        quotient = rng.randint(2, 30 if difficulty != "easy" else 12)
        divisor = dec_from_int(divisor_raw, divisor_places)
        dividend = divisor * quotient
        scale = 10 ** max(
            finite_decimal_places(dividend.denominator) or 0,
            finite_decimal_places(divisor.denominator) or 0,
        )
        method = (
            f"Scale both numbers by {scale} to remove decimals: "
            f"{fmt_decimal(dividend)} / {fmt_decimal(divisor)} = "
            f"{int(dividend * scale)} / {int(divisor * scale)} = {quotient}."
        )
        answer = Fraction(quotient)
        prompt = f"{fmt_decimal(dividend)} / {fmt_decimal(divisor)}"
    return Question(
        prompt,
        answer,
        "decimals_mul_div",
        "decimal multiplication/division",
        method,
        "decimal",
    )


def gen_reverse_equations(rng: random.Random, difficulty: str) -> Question:
    kind = rng.choice(["blank_dividend", "blank_factor", "blank_divisor", "blank_addend", "blank_start"])
    if kind == "blank_dividend":
        divisor = rng.choice([4, 5, 8, 10, 12])
        rhs = rng.choice([Fraction(5, 2), Fraction(13, 2), Fraction(7), Fraction(9), Fraction(11, 4)])
        answer = rhs * divisor
        prompt = f"? / {divisor} = {fmt_number(rhs, 'decimal')}"
        method = (
            f"Undo division by multiplying both sides by {divisor}: "
            f"? = {fmt_number(rhs, 'decimal')} x {divisor} = {fmt_number(answer)}."
        )
    elif kind == "blank_factor":
        factor = rng.choice([6, 7, 8, 9, 12, 18])
        result = rng.randint(5, 30 if difficulty != "easy" else 18)
        answer = Fraction(result, factor)
        prompt = f"{factor} x ? = {result}"
        method = (
            f"Reverse multiplication with division: ? = {result}/{factor}, "
            f"reduce -> {fmt_fraction(answer)}."
        )
    elif kind == "blank_divisor":
        dividend = rng.choice([24, 36, 42, 52, 60, 84, 96])
        rhs = rng.choice([6, 7, 8, 12, 13, 24, 65])
        answer = Fraction(dividend, rhs)
        prompt = f"{dividend} / ? = {rhs}"
        method = (
            f"Use divisor = dividend / quotient: ? = {dividend}/{rhs}, "
            f"reduce -> {fmt_fraction(answer)}."
        )
    elif kind == "blank_addend":
        addend = rng.randint(18, 75)
        result = addend + rng.randint(15, 90)
        answer = Fraction(result - addend)
        prompt = f"? + {addend} = {result}"
        method = f"Undo addition: ? = {result} - {addend} = {answer.numerator}."
    else:
        subtract = rng.randint(15, 75)
        result = rng.randint(10, 90)
        answer = Fraction(result + subtract)
        prompt = f"? - {subtract} = {result}"
        method = f"Undo subtraction: ? = {result} + {subtract} = {answer.numerator}."
    return Question(
        prompt,
        answer,
        "reverse_equations",
        "fill-in reverse equation",
        method,
    )


def add_candidate(candidates: list[Fraction], answer: Fraction, value: Fraction | int | float) -> None:
    try:
        candidate = value if isinstance(value, Fraction) else Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return
    if candidate == answer:
        return
    if answer > 0 and candidate <= 0:
        return
    if candidate not in candidates:
        candidates.append(candidate)


def parse_binary_expression(prompt: str) -> tuple[Fraction, str, Fraction] | None:
    line = prompt.splitlines()[0].strip()
    parts = line.split()
    if len(parts) != 3:
        return None
    left, operator, right = parts
    if operator not in {"+", "-", "x", "/"}:
        return None
    right = right.strip("()")
    try:
        return parse_number(left), operator, parse_number(right)
    except (ValueError, ZeroDivisionError):
        return None


def parse_reverse_expression(prompt: str) -> tuple[str, list[Fraction]] | None:
    line = prompt.splitlines()[0].strip()
    parts = line.split()
    try:
        if len(parts) == 5 and parts[0] == "?" and parts[3] == "=":
            return f"unknown_left_{parts[1]}", [parse_number(parts[2]), parse_number(parts[4])]
        if len(parts) == 5 and parts[2] == "?" and parts[3] == "=":
            return f"unknown_right_{parts[1]}", [parse_number(parts[0]), parse_number(parts[4])]
        if len(parts) == 5 and parts[1] == "x" and parts[2] == "?" and parts[3] == "=":
            return "unknown_factor", [parse_number(parts[0]), parse_number(parts[4])]
    except (ValueError, ZeroDivisionError):
        return None
    return None


def add_digit_slips(candidates: list[Fraction], answer: Fraction) -> None:
    places = finite_decimal_places(answer.denominator)
    if places is None:
        return
    scale = 10**places
    scaled = answer * scale
    if scaled.denominator != 1:
        return
    step_last = Fraction(1, scale)
    step_next_to_last = Fraction(10, scale)
    for step in (step_next_to_last, step_last):
        add_candidate(candidates, answer, answer + step)
        add_candidate(candidates, answer, answer - step)


def add_decimal_slips(candidates: list[Fraction], answer: Fraction) -> None:
    add_candidate(candidates, answer, answer * 10)
    add_candidate(candidates, answer, answer / 10)


def add_last_digit_operation_slips(
    candidates: list[Fraction],
    answer: Fraction,
    left: Fraction,
    operator: str,
    right: Fraction,
) -> None:
    if answer.denominator != 1 or left.denominator != 1 or right.denominator != 1:
        return
    base = answer.numerator - (answer.numerator % 10)
    left_digit = abs(left.numerator) % 10
    right_digit = abs(right.numerator) % 10
    wrong_digits: list[int] = []
    if operator in {"-", "/"}:
        wrong_digits.extend([(left_digit + right_digit) % 10, (left_digit * right_digit) % 10])
    elif operator == "+":
        wrong_digits.extend([abs(left_digit - right_digit), (left_digit * right_digit) % 10])
    elif operator == "x":
        wrong_digits.extend([(left_digit + right_digit) % 10, abs(left_digit - right_digit)])
    for digit in wrong_digits:
        add_candidate(candidates, answer, base + digit)


def add_binary_operation_slips(
    candidates: list[Fraction],
    question: Question,
    left: Fraction,
    operator: str,
    right: Fraction,
) -> None:
    answer = question.answer
    add_last_digit_operation_slips(candidates, answer, left, operator, right)
    if operator == "+":
        add_candidate(candidates, answer, left - right)
    elif operator == "-":
        add_candidate(candidates, answer, abs(right - left))
    elif operator == "x":
        if left.denominator == 1 and right.denominator == 1:
            tens = (right.numerator // 10) * 10
            ones = right.numerator % 10
            if tens and ones:
                add_candidate(candidates, answer, left * tens - left * ones)
                add_candidate(candidates, answer, left * (right.numerator // 10) + left * ones)
    elif operator == "/":
        if right.denominator != 1:
            add_candidate(candidates, answer, left * right)


def add_reverse_equation_slips(candidates: list[Fraction], question: Question) -> None:
    parsed = parse_reverse_expression(question.prompt)
    if parsed is None:
        return
    kind, values = parsed
    answer = question.answer
    if kind == "unknown_left_/":
        divisor, result = values
        add_candidate(candidates, answer, result / divisor)
        add_candidate(candidates, answer, result + divisor)
    elif kind == "unknown_left_+":
        addend, result = values
        add_candidate(candidates, answer, result + addend)
        add_candidate(candidates, answer, addend - result)
    elif kind == "unknown_left_-":
        subtract, result = values
        add_candidate(candidates, answer, result - subtract)
        add_candidate(candidates, answer, subtract + result + 1)
    elif kind in {"unknown_right_x", "unknown_factor"}:
        factor, result = values
        add_candidate(candidates, answer, result * factor)
        add_candidate(candidates, answer, result - factor)
    elif kind == "unknown_right_/":
        dividend, result = values
        add_candidate(candidates, answer, dividend * result)
        add_candidate(candidates, answer, result / dividend)


def add_fraction_slips(candidates: list[Fraction], answer: Fraction) -> None:
    if answer.denominator == 1:
        for offset in (1, -1, 2, -2):
            add_candidate(candidates, answer, answer + offset)
    else:
        step = Fraction(1, answer.denominator)
        add_candidate(candidates, answer, answer + step)
        add_candidate(candidates, answer, answer - step)
        if answer != 0:
            add_candidate(candidates, answer, 1 / answer)


def build_distractors(question: Question, rng: random.Random) -> list[Fraction]:
    answer = question.answer
    candidates: list[Fraction] = []
    parsed = parse_binary_expression(question.prompt)
    if parsed is not None:
        left, operator, right = parsed
        if question.category_key.startswith("decimals"):
            add_decimal_slips(candidates, answer)
        if question.category_key.startswith("fractions"):
            add_binary_operation_slips(candidates, question, left, operator, right)
            add_fraction_slips(candidates, answer)
        add_digit_slips(candidates, answer)
        if not question.category_key.startswith("fractions"):
            add_binary_operation_slips(candidates, question, left, operator, right)
    if question.category_key == "reverse_equations":
        add_digit_slips(candidates, answer)
        add_reverse_equation_slips(candidates, question)
    if parsed is None and question.category_key.startswith("fractions"):
        add_fraction_slips(candidates, answer)
    elif not question.category_key.startswith("fractions") and answer.denominator != 1:
        add_fraction_slips(candidates, answer)
    if parsed is None:
        add_digit_slips(candidates, answer)
    if question.category_key.startswith("decimals"):
        add_decimal_slips(candidates, answer)

    offsets = [1, -1, 2, -2, 5, -5, 10, -10]
    for offset in offsets:
        if answer.denominator == 1:
            add_candidate(candidates, answer, answer + offset)
        else:
            add_candidate(candidates, answer, answer + Fraction(offset, 10))
    while len(candidates) < 3:
        if answer.denominator == 1:
            add_candidate(candidates, answer, answer + rng.randint(-25, 25))
        else:
            add_candidate(candidates, answer, answer + Fraction(rng.randint(-12, 12), 10))
    return candidates[:3]


def make_choices(question: Question, rng: random.Random) -> Question:
    distractors = build_distractors(question, rng)
    choices = distractors + [question.answer]
    rng.shuffle(choices)
    return Question(
        prompt=question.prompt,
        answer=question.answer,
        category_key=question.category_key,
        category_name=question.category_name,
        fastest_method=question.fastest_method,
        answer_style=question.answer_style,
        choices=tuple(choices),
        display_prompt=question.display_prompt,
    )


def gen_elimination(rng: random.Random, difficulty: str) -> Question:
    base_generator = rng.choice(
        [
            gen_mult_near_base,
            gen_add_sub_3,
            gen_decimals_mul_div,
            gen_fractions_mul_div,
            gen_division_factor,
        ]
    )
    base = base_generator(rng, difficulty)
    method = (
        "With choices, filter first by magnitude, last digit, divisibility, or decimal size; "
        f"then finish with the fastest route: {base.fastest_method}"
    )
    base = Question(
        prompt=base.prompt,
        answer=base.answer,
        category_key="elimination",
        category_name="answer-choice elimination",
        fastest_method=method,
        answer_style=base.answer_style,
    )
    return make_choices(base, rng)


CATEGORIES: dict[str, Category] = {
    "add_sub_2digit": Category(
        "add_sub_2digit",
        "2-digit addition/subtraction",
        "Compensation around nearby tens.",
        gen_add_sub_2,
    ),
    "add_sub_3digit": Category(
        "add_sub_3digit",
        "3-digit compensation arithmetic",
        "Round to 100/200/300 anchors and correct.",
        gen_add_sub_3,
    ),
    "multiplication_decomp": Category(
        "multiplication_decomp",
        "2-digit multiplication by decomposition",
        "Break a factor into tens plus ones.",
        gen_mult_decomp,
    ),
    "multiplication_near_base": Category(
        "multiplication_near_base",
        "near-base multiplication",
        "Use 50/100 anchors and difference of squares.",
        gen_mult_near_base,
    ),
    "multiplication_x11": Category(
        "multiplication_x11",
        "times-11 trick",
        "Insert the digit sum between the outside digits.",
        gen_times_11,
    ),
    "division_factor": Category(
        "division_factor",
        "division by factor structure",
        "Reverse division into multiplication.",
        gen_division_factor,
    ),
    "fractions_add_sub": Category(
        "fractions_add_sub",
        "fraction addition/subtraction",
        "Use a common denominator and reduce.",
        gen_fractions_add_sub,
    ),
    "fractions_mul_div": Category(
        "fractions_mul_div",
        "fraction multiplication/division",
        "Flip division and cancel before multiplying.",
        gen_fractions_mul_div,
    ),
    "decimals_add_sub": Category(
        "decimals_add_sub",
        "decimal addition/subtraction",
        "Line up decimals or scale to integer cents/hundredths.",
        gen_decimals_add_sub,
    ),
    "decimals_mul_div": Category(
        "decimals_mul_div",
        "decimal multiplication/division",
        "Ignore decimals for products; scale both sides for division.",
        gen_decimals_mul_div,
    ),
    "reverse_equations": Category(
        "reverse_equations",
        "fill-in reverse equation",
        "Undo the operation from the equation.",
        gen_reverse_equations,
    ),
    "elimination": Category(
        "elimination",
        "answer-choice elimination",
        "Use magnitude, last digit, parity, and divisibility before exact arithmetic.",
        gen_elimination,
    ),
}

ALL_CATEGORY_KEYS = tuple(CATEGORIES)


def timed_input(
    prompt: str,
    seconds: float,
    quick_keys: set[str] | None = None,
) -> tuple[str | None, float, bool]:
    print(prompt, end="", flush=True)
    start = time.perf_counter()
    if not sys.stdin.isatty():
        try:
            import select

            ready, _, _ = select.select([sys.stdin], [], [], seconds)
            elapsed = time.perf_counter() - start
            if ready:
                return sys.stdin.readline().strip(), elapsed, False
            print()
            return None, seconds, True
        except (ImportError, OSError):
            answer = sys.stdin.readline().strip()
            elapsed = time.perf_counter() - start
            return answer, elapsed, elapsed > seconds

    if os.name == "nt":
        return timed_input_windows(seconds, start, quick_keys)
    return timed_input_posix(seconds, start, quick_keys)


def timed_input_windows(
    seconds: float,
    start: float,
    quick_keys: set[str] | None = None,
) -> tuple[str | None, float, bool]:
    import msvcrt

    chars: list[str] = []
    while True:
        elapsed = time.perf_counter() - start
        if elapsed >= seconds:
            print("\n[auto-skip: time limit reached]")
            return None, seconds, True
        if not msvcrt.kbhit():
            time.sleep(0.03)
            continue
        char = msvcrt.getwch()
        if char in ("\r", "\n"):
            print()
            return "".join(chars).strip(), time.perf_counter() - start, False
        if char == "\003":
            raise KeyboardInterrupt
        if quick_keys and char in quick_keys:
            print(char)
            return char, time.perf_counter() - start, False
        if char in ("\b", "\x7f"):
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
            continue
        if char.isprintable():
            chars.append(char)
            print(char, end="", flush=True)


def timed_input_posix(
    seconds: float,
    start: float,
    quick_keys: set[str] | None = None,
) -> tuple[str | None, float, bool]:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    chars: list[str] = []
    try:
        tty.setcbreak(fd)
        while True:
            elapsed = time.perf_counter() - start
            remaining = seconds - elapsed
            if remaining <= 0:
                print("\n[auto-skip: time limit reached]")
                return None, seconds, True
            ready, _, _ = select.select([sys.stdin], [], [], min(0.05, remaining))
            if not ready:
                continue
            char = sys.stdin.read(1)
            if char in ("\n", "\r"):
                print()
                return "".join(chars).strip(), time.perf_counter() - start, False
            if char == "\x03":
                raise KeyboardInterrupt
            if char == "\x04":
                raise EOFError
            if quick_keys and char in quick_keys:
                print(char)
                return char, time.perf_counter() - start, False
            if char in ("\x7f", "\b"):
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            if char == "\x15":
                while chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            if char.isprintable():
                chars.append(char)
                print(char, end="", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def build_questions(
    count: int,
    category_keys: list[str],
    difficulty: str,
    mode: str,
    rng: random.Random,
) -> list[Question]:
    if not category_keys:
        category_keys = list(ALL_CATEGORY_KEYS)
    if mode == "exact":
        category_keys = [key for key in category_keys if key != "elimination"] or list(ALL_CATEGORY_KEYS[:-1])

    if count >= len(category_keys):
        schedule = list(category_keys)
        while len(schedule) < count:
            schedule.append(rng.choice(category_keys))
        rng.shuffle(schedule)
    else:
        schedule = rng.sample(category_keys, count)

    questions: list[Question] = []
    for key in schedule:
        question = CATEGORIES[key].generator(rng, difficulty)
        if mode == "multiple-choice" and not question.choices:
            question = make_choices(question, rng)
        questions.append(question)
    return questions


def default_categories_for_preset(preset_key: str, records: list[dict[str, object]] | None = None) -> list[str]:
    if preset_key == "real":
        return list(REAL_CATEGORY_POOL)
    if preset_key == "weak":
        if records is None:
            records = load_history()
        return weak_categories_from_history(records)
    return list(ALL_CATEGORY_KEYS)


def run_test(
    questions: list[Question],
    seconds: float,
    *,
    show_type: bool,
    total_seconds: float | None = None,
    allow_quit: bool = True,
) -> list[Result]:
    print()
    has_question_limit = seconds > 0
    if total_seconds is None:
        if has_question_limit:
            print(f"Starting test: {len(questions)} questions, {seconds:g}s max each.")
        else:
            print(f"Starting test: {len(questions)} questions, no per-question time limit.")
    else:
        total_text = (
            f"{total_seconds / 60:g} minutes total"
            if total_seconds % 60 == 0
            else f"{total_seconds:g}s total"
        )
        question_text = f"{seconds:g}s max each" if has_question_limit else "no per-question cap"
        print(f"Starting test: {len(questions)} questions, {total_text}, {question_text}.")
    all_choice = all(question.choices for question in questions)
    if all_choice:
        if allow_quit:
            print("Press 1, 2, 3, or 4 to answer. A-D also work. Press q to quit.")
        else:
            print("Press 1, 2, 3, or 4 to answer. A-D also work.")
    else:
        if allow_quit:
            print("Type your answer and press Enter. For choices, use 1-4. Press q to quit.")
        else:
            print("Type your answer and press Enter. For choices, use 1-4.")
    if has_question_limit:
        print("Press Enter to submit. If time expires, the question auto-skips.")
    elif total_seconds is not None:
        print("Only the total clock matters; there is no auto-skip per question.")
    else:
        print("Press Enter to submit.")
    print()
    results: list[Result] = []
    deadline = time.perf_counter() + total_seconds if total_seconds is not None else None
    for index, question in enumerate(questions, 1):
        allowed_seconds = seconds if has_question_limit else 10**9
        if deadline is not None:
            remaining_total = deadline - time.perf_counter()
            if remaining_total <= 0:
                remaining = len(questions) - len(results)
                print(f"Total time limit reached. Counting {remaining} remaining questions as skipped.")
                for skipped_question in questions[index - 1 :]:
                    results.append(Result(skipped_question, None, 0.0, False, True))
                break
            allowed_seconds = min(seconds, remaining_total) if has_question_limit else remaining_total
        type_hint = f" [{question.category_name}]" if show_type else ""
        print(f"Q{index}/{len(questions)}{type_hint}")
        prompt = f"{render_question_block(question)}\nAnswer: "
        quick_keys = set("1234abcdABCD") if question.choices else None
        if allow_quit and quick_keys is not None:
            quick_keys.update("qQ")
        question_start = time.perf_counter()
        while True:
            remaining_for_question = allowed_seconds - (time.perf_counter() - question_start)
            if remaining_for_question <= 0:
                raw_answer, elapsed, skipped = None, max(allowed_seconds, 0.0), True
                break
            try:
                raw_answer, elapsed, skipped = timed_input(prompt, remaining_for_question, quick_keys)
            except KeyboardInterrupt:
                print("\nInterrupted. Scoring the questions answered so far.")
                return results
            if raw_answer is not None and raw_answer.strip().lower() in {"q", "quit", "exit"}:
                if allow_quit:
                    print("Quit requested. Scoring the questions answered so far.")
                    return results
                print("Quit is disabled in real mode.")
                print()
                continue
            break
        correct = check_answer(question, raw_answer) if not skipped else False
        if skipped:
            print_labeled_block("Correct answer: ", answer_display(question))
        elif correct:
            print(f"Correct ({elapsed:.1f}s)")
        else:
            print("Incorrect.")
            print_labeled_block("Correct answer: ", answer_display(question))
        print()
        results.append(Result(question, raw_answer, elapsed, correct, skipped))
    return results


def summarize_results(results: list[Result]) -> dict[str, object]:
    total = len(results)
    correct = sum(1 for result in results if result.correct)
    skipped = sum(1 for result in results if result.skipped)
    wrong = total - correct - skipped
    correct_times = [result.elapsed for result in results if result.correct]
    mean_correct = statistics.mean(correct_times) if correct_times else None
    category_stats: dict[str, dict[str, object]] = {}
    for result in results:
        key = result.question.category_key
        stats = category_stats.setdefault(
            key,
            {
                "name": result.question.category_name,
                "attempts": 0,
                "correct": 0,
                "skipped": 0,
                "correct_times": [],
            },
        )
        stats["attempts"] = int(stats["attempts"]) + 1
        if result.correct:
            stats["correct"] = int(stats["correct"]) + 1
            stats["correct_times"].append(result.elapsed)
        if result.skipped:
            stats["skipped"] = int(stats["skipped"]) + 1
    for stats in category_stats.values():
        times = stats.pop("correct_times")
        stats["mean_correct_time"] = statistics.mean(times) if times else None
    return {
        "total": total,
        "correct": correct,
        "skipped": skipped,
        "wrong": wrong,
        "net_score": correct - wrong,
        "score_percent": (correct / total * 100) if total else 0.0,
        "mean_correct_time": mean_correct,
        "category_stats": category_stats,
    }


def print_scoreboard(summary: dict[str, object], config: dict[str, object] | None = None) -> None:
    total = int(summary["total"])
    correct = int(summary["correct"])
    skipped = int(summary["skipped"])
    wrong = int(summary["wrong"])
    net_score = int(summary["net_score"])
    percent = float(summary["score_percent"])
    mean_correct = summary["mean_correct_time"]
    mean_text = f"{mean_correct:.2f}s" if isinstance(mean_correct, float) else "n/a"
    print("Score")
    if config is not None and bool(config.get("negative_marking", False)):
        print(f"  Net score (+1/-1/0): {net_score}")
    print(f"  Correct: {correct}/{total} ({percent:.1f}%)")
    print(f"  Wrong: {wrong}")
    print(f"  Skipped: {skipped}")
    print(f"  Mean time on correct answers: {mean_text}")
    if config is not None:
        score_low = config.get("benchmark_low_score")
        score_high = config.get("benchmark_high_score")
        low = config.get("benchmark_low_pct")
        high = config.get("benchmark_high_pct")
        planned_total = config.get("questions")
        benchmark_reference = config.get("benchmark_reference_questions")
        full_test_completed = not isinstance(planned_total, int) or planned_total == total
        benchmark_matches_reference = (
            not isinstance(benchmark_reference, int) or benchmark_reference == total
        )
        if (
            full_test_completed
            and benchmark_matches_reference
            and isinstance(score_low, int)
            and isinstance(score_high, int)
        ):
            if net_score < score_low:
                benchmark_status = "below the usual passing band"
            elif net_score <= score_high:
                benchmark_status = "inside the usual passing band"
            else:
                benchmark_status = "above the usual passing band"
            print(
                f"  Score benchmark: {score_low}-{score_high} "
                f"- {benchmark_status}"
            )
        elif (
            full_test_completed
            and benchmark_matches_reference
            and isinstance(low, (int, float))
            and isinstance(high, (int, float))
        ):
            low_needed = math.ceil(total * float(low) / 100)
            high_needed = max(low_needed, math.ceil(total * float(high) / 100))
            if percent < float(low):
                benchmark_status = "below the usual passing band"
            elif percent <= float(high):
                benchmark_status = "inside the usual passing band"
            else:
                benchmark_status = "above the usual passing band"
            print(
                f"  Benchmark: {low:g}-{high:g}% "
                f"({low_needed}-{high_needed}/{total}) - {benchmark_status}"
            )
    print()
    print("Category breakdown")
    category_stats = summary["category_stats"]
    for key, stats in sorted(category_stats.items(), key=lambda item: item[1]["name"]):
        attempts = int(stats["attempts"])
        cat_correct = int(stats["correct"])
        cat_skipped = int(stats["skipped"])
        accuracy = cat_correct / attempts * 100 if attempts else 0
        mean = stats["mean_correct_time"]
        mean_text = f"{mean:.2f}s" if isinstance(mean, float) else "n/a"
        print(
            f"  {stats['name']}: {cat_correct}/{attempts} ({accuracy:.0f}%), "
            f"skipped {cat_skipped}, mean correct {mean_text}"
        )
    print()


def print_review(results: list[Result]) -> None:
    print("Question review")
    for index, result in enumerate(results, 1):
        question = result.question
        status = "correct" if result.correct else "skipped" if result.skipped else "incorrect"
        lines = question_text(question).splitlines()
        if len(lines) == 1:
            print(f"{index}. {lines[0]}")
        else:
            print(f"{index}.")
            for line in lines:
                print(f"   {line}")
        choice_blocks = render_choice_blocks(question)
        if choice_blocks:
            print("   Choices:")
            for block in choice_blocks:
                for line in block.splitlines():
                    print(f"     {line.rstrip()}")
        print(f"   Type: {question.category_name}")
        print(f"   Result: {status}")
        print_labeled_block("   Your answer: ", display_user_answer(question, result.user_answer), indent="     ")
        print(f"   Time: {result.elapsed:.1f}s")
        print_labeled_block("   Correct answer: ", answer_display(question), indent="     ")
        print(f"   Fastest route: {question.fastest_method}")
    print()


def retry_misses(results: list[Result]) -> None:
    misses = [result.question for result in results if not result.correct]
    if not misses:
        return
    print(f"Correction round: {len(misses)} missed/skipped questions, untimed.")
    for index, question in enumerate(misses, 1):
        block_lines = render_question_block(question).splitlines()
        if len(block_lines) == 1:
            print(f"{index}. {block_lines[0]}")
        else:
            print(f"{index}.")
            for line in block_lines:
                print(f"   {line}")
        raw = input("Answer: ").strip()
        if check_answer(question, raw):
            print("Correct.")
        else:
            print("Still missed.")
            print_labeled_block("Correct answer: ", answer_display(question))
        print(f"Fastest route: {question.fastest_method}")
        print()


def save_history(
    results: list[Result],
    summary: dict[str, object],
    config: dict[str, object],
) -> None:
    record = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "config": config,
        "summary": summary,
        "results": [
            {
                "prompt": result.question.prompt,
                "category_key": result.question.category_key,
                "category_name": result.question.category_name,
                "answer": answer_display(result.question),
                "user_answer": result.user_answer,
                "elapsed": round(result.elapsed, 3),
                "correct": result.correct,
                "skipped": result.skipped,
            }
            for result in results
        ],
    }
    with HISTORY_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def load_history() -> list[dict[str, object]]:
    if not HISTORY_FILE.exists():
        return []
    records: list[dict[str, object]] = []
    with HISTORY_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def record_config(record: dict[str, object]) -> dict[str, object]:
    config = record.get("config", {})
    return config if isinstance(config, dict) else {}


def record_summary(record: dict[str, object]) -> dict[str, object]:
    summary = record.get("summary", {})
    return summary if isinstance(summary, dict) else {}


def record_results(record: dict[str, object]) -> list[dict[str, object]]:
    results = record.get("results", [])
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def record_category_keys(record: dict[str, object]) -> set[str]:
    config = record_config(record)
    raw_categories = config.get("categories", [])
    keys: set[str] = set()
    if isinstance(raw_categories, list):
        keys.update(str(item) for item in raw_categories if str(item) in CATEGORIES)
    if keys:
        return keys
    for result in record_results(record):
        key = str(result.get("category_key", ""))
        if key in CATEGORIES:
            keys.add(key)
    return keys


def is_completed_record(record: dict[str, object]) -> bool:
    config = record_config(record)
    summary = record_summary(record)
    planned = config.get("questions")
    total = summary.get("total")
    return isinstance(planned, int) and isinstance(total, int) and total == planned


def is_general_practice_record(record: dict[str, object]) -> bool:
    config = record_config(record)
    preset = str(config.get("preset", ""))
    if preset == "weak":
        return False
    categories = record_category_keys(record)
    if len(categories) < 6:
        return False
    if not is_completed_record(record):
        return False
    return True


def recent_general_records(records: list[dict[str, object]], limit: int = 5) -> list[dict[str, object]]:
    general = [record for record in records if is_general_practice_record(record)]
    return general[-limit:]


def aggregate_category_outcomes(records: list[dict[str, object]]) -> tuple[dict[str, dict[str, float]], int]:
    aggregate: dict[str, dict[str, float]] = {}
    total_failures = 0
    for record in records:
        for result in record_results(record):
            key = str(result.get("category_key", ""))
            if key not in CATEGORIES:
                continue
            stats = aggregate.setdefault(
                key,
                {
                    "name": CATEGORIES[key].name,
                    "attempts": 0.0,
                    "failures": 0.0,
                    "skipped": 0.0,
                    "correct_times": 0.0,
                    "correct_count": 0.0,
                },
            )
            stats["attempts"] += 1
            correct = bool(result.get("correct", False))
            skipped = bool(result.get("skipped", False))
            elapsed = result.get("elapsed")
            if correct:
                stats["correct_count"] += 1
                if isinstance(elapsed, (int, float)):
                    stats["correct_times"] += float(elapsed)
            else:
                stats["failures"] += 1
                total_failures += 1
            if skipped:
                stats["skipped"] += 1
    return aggregate, total_failures


def rank_category_rows(records: list[dict[str, object]]) -> list[dict[str, object]]:
    aggregate, total_failures = aggregate_category_outcomes(records)
    rows: list[dict[str, object]] = []
    for key, stats in aggregate.items():
        attempts = int(stats["attempts"])
        failures = int(stats["failures"])
        skipped = int(stats["skipped"])
        correct_count = int(stats["correct_count"])
        fail_rate = failures / attempts if attempts else 0.0
        mean_correct_time = (
            stats["correct_times"] / correct_count if correct_count else None
        )
        failure_share = failures / total_failures * 100 if total_failures else 0.0
        rows.append(
            {
                "key": key,
                "name": stats["name"],
                "attempts": attempts,
                "failures": failures,
                "skipped": skipped,
                "accuracy": correct_count / attempts * 100 if attempts else 0.0,
                "failure_rate": fail_rate * 100,
                "failure_share": failure_share,
                "mean_correct_time": mean_correct_time,
            }
        )
    rows.sort(
        key=lambda row: (
            row["failures"],
            row["failure_rate"],
            row["skipped"],
            row["mean_correct_time"] if isinstance(row["mean_correct_time"], float) else 0.0,
        ),
        reverse=True,
    )
    return rows


def weak_categories_from_history(records: list[dict[str, object]]) -> list[str]:
    general_records = recent_general_records(records, limit=5)
    if not general_records:
        return list(ALL_CATEGORY_KEYS)
    rows = rank_category_rows(general_records)
    if not rows:
        return list(ALL_CATEGORY_KEYS)
    failing_rows = [row for row in rows if row["failures"] > 0]
    ranked_rows = failing_rows or rows
    return [str(row["key"]) for row in ranked_rows[: min(5, len(ranked_rows))]]


def print_history_summary(records: list[dict[str, object]]) -> None:
    if not records:
        print(f"No practice history yet. History will be saved to {HISTORY_FILE}.")
        return
    print(f"History file: {HISTORY_FILE}")
    print("Recent sessions")
    for record in records[-10:]:
        config = record_config(record)
        timestamp = str(record.get("timestamp", "unknown"))
        summary = record_summary(record)
        total = int(summary.get("total", 0) or 0)
        correct = int(summary.get("correct", 0) or 0)
        percent = float(summary.get("score_percent", 0.0) or 0.0)
        mean = summary.get("mean_correct_time")
        mean_text = f"{mean:.2f}s" if isinstance(mean, (int, float)) else "n/a"
        preset = str(config.get("preset", "custom"))
        scope = "general" if is_general_practice_record(record) else "specialized"
        print(
            f"  {timestamp} [{preset}, {scope}]: "
            f"{correct}/{total} ({percent:.1f}%), mean correct {mean_text}"
        )
    general_records = recent_general_records(records, limit=5)
    if not general_records:
        print("Weak-spot analysis")
        print("  No completed broad non-weak runs yet, so weak-spot practice uses all categories.")
        return
    rows = rank_category_rows(general_records)
    print(f"Weak-spot analysis (last {len(general_records)} completed broad non-weak runs)")
    if not rows:
        print("  No category data available yet.")
        return
    display_rows = [row for row in rows if row["failures"] > 0] or rows
    for row in display_rows[: min(5, len(display_rows))]:
        mean = row["mean_correct_time"]
        mean_text = f", mean correct {mean:.2f}s" if isinstance(mean, float) else ""
        print(
            f"  {row['name']}: {row['failures']} failed, "
            f"{row['failure_share']:.1f}% of failed questions, "
            f"{row['accuracy']:.0f}% accuracy over {row['attempts']} attempts, "
            f"skipped {row['skipped']}{mean_text}"
        )


def parse_categories(raw: str | None) -> list[str]:
    if not raw:
        return list(ALL_CATEGORY_KEYS)
    keys: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item.isdigit():
            index = int(item) - 1
            if 0 <= index < len(ALL_CATEGORY_KEYS):
                keys.append(ALL_CATEGORY_KEYS[index])
                continue
        if item not in CATEGORIES:
            raise SystemExit(f"Unknown category: {item}. Use --list-categories to see valid keys.")
        keys.append(item)
    return keys or list(ALL_CATEGORY_KEYS)


def print_categories() -> None:
    for index, key in enumerate(ALL_CATEGORY_KEYS, 1):
        category = CATEGORIES[key]
        print(f"{index:2}. {key}: {category.name} - {category.focus}")


def prompt_int(label: str, default: int, minimum: int = 1) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("Enter a whole number.")
            continue
        if value < minimum:
            print(f"Enter at least {minimum}.")
            continue
        return value


def prompt_float(label: str, default: float, minimum: float = 1.0) -> float:
    while True:
        raw = input(f"{label} [{default:g}]: ").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            print("Enter a number.")
            continue
        if value < minimum:
            print(f"Enter at least {minimum:g}.")
            continue
        return value


def prompt_yes_no(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"{label} [{suffix}]: ").strip().lower()
        except EOFError:
            print()
            return default
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Enter y or n.")


def preset_timing_text(preset: Preset) -> str:
    question_limit = f"{preset.seconds:g}s each" if preset.seconds > 0 else "no per-question cap"
    if preset.total_seconds:
        return f"{question_limit}, {preset.total_seconds / 60:g} min total"
    return question_limit


def interactive_config() -> dict[str, object] | None:
    print("Mental Math Interview Practice")
    print("Sequences are intentionally excluded.")
    print()
    options = list(PRESETS)
    for index, key in enumerate(options, 1):
        preset = PRESETS[key]
        print(
            f"{index}. {preset.name}: {preset.questions} questions, "
            f"{preset_timing_text(preset)} - {preset.description}"
        )
    custom_choice = len(options) + 1
    history_choice = len(options) + 2
    quit_choice = len(options) + 3
    print(f"{custom_choice}. Custom test")
    print(f"{history_choice}. Show history")
    print(f"{quit_choice}. Quit")
    print()
    choice = input("Choose an option: ").strip()
    if choice == str(quit_choice):
        return None
    if choice == str(history_choice):
        print_history_summary(load_history())
        print()
        return interactive_config()
    if choice.isdigit() and 1 <= int(choice) <= len(options):
        key = options[int(choice) - 1]
        preset = PRESETS[key]
        records = load_history()
        categories = default_categories_for_preset(key, records)
        if key == "weak" and not records:
            print("No history yet, so the weak-spot drill will use all categories.")
        return {
            "preset": key,
            "questions": preset.questions,
            "seconds": preset.seconds,
            "difficulty": preset.difficulty,
            "mode": preset.mode,
            "categories": categories,
            "show_type": prompt_yes_no("Show question type during the test", False),
            "review": True,
            "history_mode": "ask",
            "retry_misses": False,
            "total_seconds": preset.total_seconds,
            "allow_quit": key != "real",
            "benchmark_low_pct": preset.benchmark_low_pct,
            "benchmark_high_pct": preset.benchmark_high_pct,
            "benchmark_low_score": preset.benchmark_low_score,
            "benchmark_high_score": preset.benchmark_high_score,
            "negative_marking": preset.negative_marking,
            "benchmark_reference_questions": preset.benchmark_reference_questions,
        }
    if choice != str(custom_choice):
        print("Unknown option.")
        return interactive_config()

    questions = prompt_int("Number of questions", 24)
    seconds = prompt_float("Seconds per question before auto-skip", 10)
    difficulty = input("Difficulty: easy, mixed, hard [mixed]: ").strip().lower() or "mixed"
    if difficulty not in {"easy", "mixed", "hard"}:
        difficulty = "mixed"
    mode = input("Mode: mixed, exact, multiple-choice [multiple-choice]: ").strip().lower() or "multiple-choice"
    if mode not in {"mixed", "exact", "multiple-choice"}:
        mode = "mixed"
    print()
    print_categories()
    raw_categories = input("Categories by number/key, comma-separated, or Enter for all: ").strip()
    return {
        "preset": "custom",
        "questions": questions,
        "seconds": seconds,
        "difficulty": difficulty,
        "mode": mode,
        "categories": parse_categories(raw_categories),
        "show_type": prompt_yes_no("Show question type during the test", False),
        "review": prompt_yes_no("Show full review afterward", True),
        "history_mode": "ask",
        "retry_misses": prompt_yes_no("Redo missed/skipped questions afterward", False),
        "total_seconds": None,
        "allow_quit": True,
        "benchmark_low_pct": None,
        "benchmark_high_pct": None,
        "benchmark_low_score": None,
        "benchmark_high_score": None,
        "negative_marking": False,
        "benchmark_reference_questions": None,
    }


def sample_questions(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    preset = PRESETS[args.preset] if args.preset else None
    preset_key = args.preset or "custom"
    records = load_history() if preset_key == "weak" else None
    categories = parse_categories(args.categories) if args.categories else default_categories_for_preset(preset_key, records)
    difficulty = args.difficulty or (preset.difficulty if preset else "mixed")
    mode = args.mode or (preset.mode if preset else "multiple-choice")
    questions = build_questions(args.sample, categories, difficulty, mode, rng)
    for index, question in enumerate(questions, 1):
        block_lines = render_question_block(question).splitlines()
        if len(block_lines) == 1:
            print(f"{index}. {block_lines[0]}")
        else:
            print(f"{index}.")
            for line in block_lines:
                print(f"   {line}")
        print(f"   Type: {question.category_name}")
        print_labeled_block("   Answer: ", answer_display(question), indent="     ")
        print(f"   Fastest route: {question.fastest_method}")


def run_from_config(config: dict[str, object], seed: int | None = None) -> None:
    rng = random.Random(seed)
    questions = build_questions(
        int(config["questions"]),
        list(config["categories"]),
        str(config["difficulty"]),
        str(config["mode"]),
        rng,
    )
    total_seconds = config.get("total_seconds")
    results = run_test(
        questions,
        float(config["seconds"]),
        show_type=bool(config["show_type"]),
        total_seconds=float(total_seconds) if isinstance(total_seconds, (int, float)) else None,
        allow_quit=bool(config.get("allow_quit", True)),
    )
    if not results:
        print("No questions answered.")
        return
    summary = summarize_results(results)
    print_scoreboard(summary, config)
    if bool(config["review"]):
        print_review(results)
    if bool(config["retry_misses"]):
        retry_misses(results)
    elif bool(config["review"]) and any(not result.correct for result in results):
        if sys.stdin.isatty() and prompt_yes_no("Redo missed/skipped questions untimed now", False):
            retry_misses(results)
    history_mode = str(config.get("history_mode", "ask"))
    if history_mode == "always":
        save_history(results, summary, config)
        print(f"Saved history to {HISTORY_FILE}")
    elif history_mode == "ask" and sys.stdin.isatty():
        if prompt_yes_no("Save this run to history", True):
            save_history(results, summary, config)
            print(f"Saved history to {HISTORY_FILE}")


def build_config_from_args(args: argparse.Namespace) -> dict[str, object]:
    preset_key = args.preset or "core"
    preset = PRESETS[preset_key]
    records = load_history()
    categories = parse_categories(args.categories) if args.categories else default_categories_for_preset(preset_key, records)
    if preset_key == "weak" and not args.categories and not records:
        print("No history yet, so the weak-spot drill will use all categories.")
    return {
        "preset": preset_key,
        "questions": args.questions if args.questions is not None else preset.questions,
        "seconds": args.seconds if args.seconds is not None else preset.seconds,
        "difficulty": args.difficulty or preset.difficulty,
        "mode": args.mode or preset.mode,
        "categories": categories,
        "show_type": args.show_type,
        "review": not args.no_review,
        "history_mode": "never" if args.no_history else ("ask" if sys.stdin.isatty() else "always"),
        "retry_misses": args.retry_misses,
        "total_seconds": args.total_seconds if args.total_seconds is not None else preset.total_seconds,
        "allow_quit": preset_key != "real",
        "benchmark_low_pct": preset.benchmark_low_pct,
        "benchmark_high_pct": preset.benchmark_high_pct,
        "benchmark_low_score": preset.benchmark_low_score,
        "benchmark_high_score": preset.benchmark_high_score,
        "negative_marking": preset.negative_marking,
        "benchmark_reference_questions": preset.benchmark_reference_questions,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Timed CLI practice for mental-math interview screens.",
    )
    parser.add_argument("--preset", choices=sorted(PRESETS), help="Preset test configuration.")
    parser.add_argument("-n", "--questions", type=int, help="Number of questions.")
    parser.add_argument("-s", "--seconds", type=float, help="Seconds per question before auto-skip.")
    parser.add_argument("--total-seconds", type=float, help="Optional total test clock in seconds.")
    parser.add_argument("--difficulty", choices=["easy", "mixed", "hard"], help="Question difficulty.")
    parser.add_argument(
        "--mode",
        choices=["mixed", "exact", "multiple-choice"],
        help="Answer mode. Mixed includes exact-entry questions and answer-choice elimination.",
    )
    parser.add_argument(
        "--categories",
        help="Comma-separated category keys or numbers. Use --list-categories to inspect them.",
    )
    parser.add_argument("--show-type", action="store_true", help="Show the category during the timed test.")
    parser.add_argument("--no-review", action="store_true", help="Skip the full post-test question review.")
    parser.add_argument("--no-history", action="store_true", help="Do not append this session to history.")
    parser.add_argument("--retry-misses", action="store_true", help="Redo missed/skipped questions after review.")
    parser.add_argument("--seed", type=int, help="Random seed for repeatable drills.")
    parser.add_argument("--list-categories", action="store_true", help="List available question categories and exit.")
    parser.add_argument("--history", action="store_true", help="Show saved practice history and exit.")
    parser.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help="Print N generated questions with answers/methods, then exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)

    if args.list_categories:
        print_categories()
        return 0
    if args.history:
        print_history_summary(load_history())
        return 0
    if args.sample is not None:
        sample_questions(args)
        return 0

    no_runtime_args = (argv is None and len(sys.argv) == 1) or (argv is not None and len(argv) == 0)
    if no_runtime_args:
        config = interactive_config()
        if config is None:
            return 0
    else:
        config = build_config_from_args(args)
    run_from_config(config, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
