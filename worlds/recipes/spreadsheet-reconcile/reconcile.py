"""The spreadsheet-reconcile world: three months of a bank statement and a
company's books, seeded with the discrepancies a bookkeeper meets, reconciled
one month at a time. A pack of the local archetype (worlds/README.md).

    python3 reconcile.py setup --workdir W --seed S    writes bank.csv, books.csv and state.json in W
    python3 reconcile.py observe --workdir W           prints the observation data, one JSON object
    python3 reconcile.py act ACTION --workdir W        applies ACTION; the parameters arrive on stdin
    python3 reconcile.py truth --seed S                prints the answer key, for checks/score.py

The command-world engine runs the first three inside a run's working copy.
The answer key comes from the seed and is never written into the copy. The
standard library only, and every output is a function of the seed and the
actions, which local replay needs.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

YEAR = 2026
FIRST_MONTH = 7
MONTHS = 3
VENDORS = (
    ("Harbor Paper Co", "HARBOR", "CARD PURCHASE HARBOR PAPER CO"),
    ("Juniper Freight", "JUNIPER", "ACH DEBIT JUNIPER FREIGHT"),
    ("Kestrel Software", "KESTREL", "CARD PURCHASE KESTREL SOFTWARE"),
    ("Lumen Utilities", "LUMEN", "ACH DEBIT LUMEN UTILITIES"),
    ("Marigold Catering", "MARIGOLD", "CARD PURCHASE MARIGOLD CATERING"),
    ("Northgate Rent", "NORTHGATE", "ACH DEBIT NORTHGATE RENT"),
    ("Osprey Telecom", "OSPREY", "ACH DEBIT OSPREY TELECOM"),
    ("Pinecrest Insurance", "PINECREST", "ACH DEBIT PINECREST INSURANCE"),
)
CUSTOMERS = ("Alder Studio", "Birch Labs", "Cedar Health", "Dogwood Media", "Elm Partners")
KINDS = (
    "transposed",
    "fee_unrecorded",
    "interest_unrecorded",
    "duplicate",
    "outstanding_check",
    "deposit_in_transit",
    "date_typo",
    "sign_error",
)
DISCREPANCIES_PER_MONTH = 5
VENDOR_PAYMENTS = 8
CHECKS = 4
DEPOSITS = 4
FIRST_CHECK = 1041
FIRST_SLIP = 501
FIRST_RECORDED_ROW = 80
# The world's rules: the agent learns them from refusals.
WINDOW_DAYS = 4
CARRIED_WINDOW_DAYS = 10
OUTSTANDING_DAYS = 5
TRANSIT_DAYS = 3
BANK_FIELDS = ("id", "date", "description", "amount", "status", "match")
BOOKS_FIELDS = ("id", "date", "payee", "ref", "amount", "status", "match", "flag", "note")
FLAG_REASONS = ("outstanding_check", "deposit_in_transit")
RECORD_CATEGORIES = {"fee": ("FEE", "Bank fee"), "interest": ("INTEREST", "Interest income")}
RECORDED_NOTE = "recorded from the statement"
AMOUNT = re.compile(r"^-?\d{1,7}\.\d{2}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CENT = Decimal("0.01")


def month_label(month: int) -> str:
    return f"{YEAR}-{FIRST_MONTH + month - 1:02d}"


def last_day(month: int) -> int:
    return calendar.monthrange(YEAR, FIRST_MONTH + month - 1)[1]


def day_of(month: int, day: int) -> date:
    return date(YEAR, FIRST_MONTH + month - 1, day)


def month_of_date(value: date) -> int:
    return (value.year - YEAR) * 12 + value.month - FIRST_MONTH + 1


def money(text: Decimal) -> str:
    return f"{text.quantize(CENT):.2f}"


# -- the generator --------------------------------------------------------------


@dataclass
class Event:
    """One true transaction, and how the books show it."""

    kind: str  # vendor, refund, check, deposit, fee, interest
    month: int  # the month of its books entry
    payee: str
    ref: str
    amount: Decimal
    book_date: date
    bank_date: date | None  # None: it clears after the statement ends
    description: str
    shown_amount: Decimal | None = None  # the books' amount, when it differs
    shown_date: date | None = None  # the books' date, when it differs
    in_books: bool = True
    duplicated: bool = False
    flag: str | None = None
    discrepancy: str | None = None
    bank_id: str = ""
    book_ids: list[str] = field(default_factory=list)


def _amount(rng: random.Random, low: int, high: int, taken: set[Decimal]) -> Decimal:
    while True:
        value = Decimal(rng.randint(low * 100, high * 100)) / 100
        if value not in taken:
            taken.add(value)
            return value


def _transposed(amount: Decimal, rng: random.Random) -> Decimal | None:
    digits = money(abs(amount)).replace(".", "")
    pairs = [index for index in range(len(digits) - 1) if digits[index] != digits[index + 1]]
    if not pairs:
        return None
    index = rng.choice(pairs)
    swapped = digits[:index] + digits[index + 1] + digits[index] + digits[index + 2 :]
    value = Decimal(swapped[:-2] + "." + swapped[-2:])
    return value if amount > 0 else -value


def _month_events(rng: random.Random, month: int, counters: dict[str, int]) -> list[Event]:
    end = last_day(month)
    taken: set[Decimal] = set()
    events: list[Event] = []
    for _ in range(VENDOR_PAYMENTS):
        payee, ref, description = rng.choice(VENDORS)
        bank_day = rng.randint(3, end - 6)
        book_day = max(1, bank_day - rng.randint(0, 2))
        events.append(Event("vendor", month, payee, ref, -_amount(rng, 40, 2400, taken),
                            day_of(month, book_day), day_of(month, bank_day), description))
    payee, ref, _ = rng.choice(VENDORS)
    bank_day = rng.randint(3, end - 6)
    events.append(Event("refund", month, payee, ref, _amount(rng, 20, 300, taken),
                        day_of(month, max(1, bank_day - 1)), day_of(month, bank_day), f"CARD REFUND {payee.upper()}"))
    for _ in range(CHECKS):
        number = counters["check"]
        counters["check"] += 1
        payee = rng.choice(VENDORS)[0]
        book_day = rng.randint(2, end - 7)
        events.append(Event("check", month, payee, str(number), -_amount(rng, 80, 3500, taken),
                            day_of(month, book_day), day_of(month, book_day + rng.randint(1, 4)), f"CHECK {number}"))
    for _ in range(DEPOSITS):
        slip = f"D{counters['slip']}"
        counters["slip"] += 1
        book_day = rng.randint(2, end - 4)
        events.append(Event("deposit", month, rng.choice(CUSTOMERS), slip, _amount(rng, 300, 6000, taken),
                            day_of(month, book_day), day_of(month, book_day + rng.randint(0, 1)), f"DEPOSIT {slip}"))
    fee = -Decimal(rng.choice(("12.50", "15.00", "25.00")))
    taken.add(-fee)
    events.append(Event("fee", month, "Bank fee", "FEE", fee, day_of(month, end), day_of(month, end), "MONTHLY SERVICE FEE"))
    events.append(Event("interest", month, "Interest income", "INTEREST", _amount(rng, 1, 12, taken),
                        day_of(month, end), day_of(month, end), "INTEREST CREDIT"))
    return events


def _seed_discrepancies(rng: random.Random, month: int, events: list[Event]) -> None:
    end = last_day(month)
    used: set[int] = set()
    book_amounts = {event.amount for event in events}

    def pick(kinds: tuple[str, ...]) -> Event | None:
        choices = [i for i, event in enumerate(events) if event.kind in kinds and i not in used]
        if not choices:
            return None
        index = rng.choice(choices)
        used.add(index)
        return events[index]

    for kind in sorted(rng.sample(KINDS, DISCREPANCIES_PER_MONTH), key=KINDS.index):
        if kind == "transposed":
            tried: set[int] = set()
            for _ in range(10):
                target = pick(("vendor", "check"))
                if target is None:
                    break
                shown = _transposed(target.amount, rng)
                if shown is not None and shown not in book_amounts:
                    target.shown_amount, target.discrepancy = shown, kind
                    book_amounts.add(shown)
                    break
                # Set aside rather than retried: the next pick takes another line.
                tried.add(next(i for i, event in enumerate(events) if event is target))
            used.difference_update(tried)
        elif kind in ("fee_unrecorded", "interest_unrecorded"):
            target = pick(("fee",) if kind == "fee_unrecorded" else ("interest",))
            if target is not None:
                target.in_books, target.discrepancy = False, kind
        elif kind == "duplicate":
            target = pick(("vendor",))
            if target is not None:
                target.duplicated, target.discrepancy = True, kind
        elif kind == "outstanding_check":
            target = pick(("check",))
            if target is not None:
                target.book_date = day_of(month, end - rng.randint(0, OUTSTANDING_DAYS - 2))
                target.bank_date = day_of(month + 1, rng.randint(2, 5)) if month < MONTHS else None
                target.flag, target.discrepancy = kind, kind
        elif kind == "deposit_in_transit":
            target = pick(("deposit",))
            if target is not None:
                target.book_date = day_of(month, end - rng.randint(0, TRANSIT_DAYS - 2))
                target.bank_date = day_of(month + 1, rng.randint(1, 2)) if month < MONTHS else None
                target.flag, target.discrepancy = kind, kind
        elif kind == "date_typo":
            target = pick(("vendor",))
            if target is not None and target.bank_date is not None:
                shift = rng.randint(9, 14)
                for candidate in (target.bank_date + timedelta(days=shift), target.bank_date - timedelta(days=shift)):
                    if month_of_date(candidate) == month:
                        target.shown_date, target.discrepancy = candidate, kind
                        break
        elif kind == "sign_error":
            target = pick(("refund",))
            if target is not None:
                target.shown_amount, target.discrepancy = -target.amount, kind


def generate(seed: int) -> list[Event]:
    """Every event of the three months, with its bank and books ids."""
    rng = random.Random(f"spreadsheet-reconcile-{seed}")
    counters = {"check": FIRST_CHECK, "slip": FIRST_SLIP}
    events: list[Event] = []
    for month in range(1, MONTHS + 1):
        month_events = _month_events(rng, month, counters)
        _seed_discrepancies(rng, month, month_events)
        events.extend(month_events)
    for month in range(1, MONTHS + 1):
        on_statement = sorted(
            (event for event in events if event.bank_date is not None and month_of_date(event.bank_date) == month),
            key=lambda event: (event.bank_date, event.description, event.amount),
        )
        for index, event in enumerate(on_statement, 1):
            event.bank_id = f"B{month}-{index:02d}"
        in_books = sorted(
            ((event, copy) for event in events if event.month == month and event.in_books
             for copy in range(2 if event.duplicated else 1)),
            key=lambda pair: (pair[0].shown_date or pair[0].book_date, pair[0].payee, pair[0].ref,
                              pair[0].shown_amount or pair[0].amount, pair[1]),
        )
        for index, (event, _) in enumerate(in_books, 1):
            event.book_ids.append(f"K{month}-{index:02d}")
    return events


def truth(seed: int) -> dict[str, Any]:
    """The answer key: the true pairs, the bank lines to record, the lines to
    flag, the duplicate groups, and every seeded discrepancy."""
    events = generate(seed)
    pairs = []
    records = {}
    flags = {}
    duplicates = []
    discrepancies = []
    for event in events:
        if event.bank_id:
            if event.in_books:
                pairs.append({"bank": event.bank_id, "books": list(event.book_ids)})
            else:
                records[event.bank_id] = "fee" if event.kind == "fee" else "interest"
        if event.flag:
            flags[event.book_ids[0]] = event.flag
        if event.duplicated:
            duplicates.append(list(event.book_ids))
        if event.discrepancy:
            discrepancies.append({
                "month": month_label(event.month),
                "kind": event.discrepancy,
                "bank": event.bank_id or None,
                "books": list(event.book_ids),
            })
    return {
        "seed": seed,
        "pairs": pairs,
        "records": records,
        "flags": flags,
        "duplicates": duplicates,
        "discrepancies": discrepancies,
    }


# -- the working copy -----------------------------------------------------------


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fields})
    path.write_text(buffer.getvalue())


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def setup(workdir: Path, seed: int) -> None:
    events = generate(seed)
    bank = sorted(
        ({"id": event.bank_id, "date": event.bank_date.isoformat(), "description": event.description,
          "amount": money(event.amount), "status": "open", "match": ""}
         for event in events if event.bank_id and event.bank_date is not None),
        key=lambda row: (row["id"][1], int(row["id"][3:])),
    )
    books = sorted(
        ({"id": book_id, "date": (event.shown_date or event.book_date).isoformat(), "payee": event.payee,
          "ref": event.ref, "amount": money(event.shown_amount if event.shown_amount is not None else event.amount),
          "status": "open", "match": "", "flag": "", "note": ""}
         for event in events for book_id in event.book_ids),
        key=lambda row: (row["id"][1], int(row["id"][3:])),
    )
    _write_csv(workdir / "bank.csv", BANK_FIELDS, bank)
    _write_csv(workdir / "books.csv", BOOKS_FIELDS, books)
    state = {"month": 1, "months_closed": 0, "fixes": 0, "recorded": 0, "deleted": 0,
             "next_row": {str(month): FIRST_RECORDED_ROW for month in range(1, MONTHS + 1)}}
    (workdir / "state.json").write_text(json.dumps(state, sort_keys=True) + "\n")


class Ledgers:
    """The working copy loaded: the two ledgers and the state."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.bank = _read_csv(workdir / "bank.csv")
        self.books = _read_csv(workdir / "books.csv")
        self.state = json.loads((workdir / "state.json").read_text())

    def save(self) -> None:
        _write_csv(self.workdir / "bank.csv", BANK_FIELDS, self.bank)
        _write_csv(self.workdir / "books.csv", BOOKS_FIELDS, self.books)
        (self.workdir / "state.json").write_text(json.dumps(self.state, sort_keys=True) + "\n")

    @property
    def month(self) -> int:
        return int(self.state["month"])

    def bank_row(self, row_id: str) -> dict[str, str] | None:
        return next((row for row in self.bank if row["id"] == row_id), None)

    def books_row(self, row_id: str) -> dict[str, str] | None:
        return next((row for row in self.books if row["id"] == row_id), None)

    def open_counts(self) -> tuple[int, int, int]:
        month = self.month
        if month > MONTHS:
            return 0, 0, 0
        bank_open = sum(1 for row in self.bank if row_month(row) == month and row["status"] == "open")
        books_open = sum(1 for row in self.books if row_month(row) == month and row["status"] == "open")
        carried = sum(1 for row in self.books if row_month(row) == month - 1 and row["status"] == "flagged")
        return bank_open, books_open, carried

    def close_months(self) -> list[str]:
        closed = []
        while self.month <= MONTHS and self.open_counts() == (0, 0, 0):
            closed.append(month_label(self.month))
            self.state["months_closed"] += 1
            self.state["month"] += 1
        return closed

    def difference(self) -> Decimal:
        """The month's unreconciled difference: the statement's lines less the
        books' lines that count this month (open or matched lines of the
        month, and lines carried from the month before and matched now)."""
        month = self.month
        if month > MONTHS:
            return Decimal("0")
        statement = sum((Decimal(row["amount"]) for row in self.bank if row_month(row) == month), Decimal("0"))
        bank_months = {row["id"]: row_month(row) for row in self.bank}
        books = Decimal("0")
        for row in self.books:
            if row_month(row) == month and row["status"] in ("open", "matched"):
                books += Decimal(row["amount"])
            elif row_month(row) == month - 1 and row["status"] == "matched" and bank_months.get(row["match"]) == month:
                books += Decimal(row["amount"])
        return statement - books


def row_month(row: dict[str, str]) -> int:
    return int(row["id"][1])


def observe(workdir: Path) -> dict[str, Any]:
    ledgers = Ledgers(workdir)
    bank_open, books_open, carried = ledgers.open_counts()
    month = ledgers.month
    return {
        "month": month_label(month) if month <= MONTHS else "done",
        "month_index": month,
        "months": MONTHS,
        "months_closed": ledgers.state["months_closed"],
        "bank_open": bank_open,
        "books_open": books_open,
        "carried_open": carried,
        "matched": sum(1 for row in ledgers.bank if row["status"] == "matched"),
        "flagged": sum(1 for row in ledgers.books if row["status"] == "flagged"),
        "recorded": ledgers.state["recorded"],
        "deleted": ledgers.state["deleted"],
        "fixes": ledgers.state["fixes"],
        "difference": float(ledgers.difference().quantize(CENT)),
        "files": {"bank": "bank.csv", "books": "books.csv"},
    }


# -- the actions ----------------------------------------------------------------


class Refused(Exception):
    """The world says no, with the reason the agent reads."""


def _ok(detail: str, closed: list[str]) -> dict[str, Any]:
    if closed:
        detail += "; closed " + ", ".join(closed)
    return {"status": "ok", "detail": detail}


def _current_bank(ledgers: Ledgers, row_id: str) -> dict[str, str]:
    row = ledgers.bank_row(row_id)
    if row is None:
        raise Refused(f"no bank line {row_id}")
    _check_month(ledgers, row)
    return row


def _current_books(ledgers: Ledgers, row_id: str) -> dict[str, str]:
    row = ledgers.books_row(row_id)
    if row is None:
        raise Refused(f"no books line {row_id}")
    _check_month(ledgers, row)
    return row


def _check_month(ledgers: Ledgers, row: dict[str, str]) -> None:
    month = ledgers.month
    if row_month(row) > month:
        raise Refused(f"{row['id']} belongs to {month_label(row_month(row))}, which opens when {month_label(month)} closes")
    if row_month(row) < month:
        raise Refused(f"{row['id']} belongs to {month_label(row_month(row))}, which is closed")


def _pair_problem(ledgers: Ledgers, bank: dict[str, str], book: dict[str, str]) -> str | None:
    month = ledgers.month
    carried = row_month(book) == month - 1 and book["status"] == "flagged"
    if not (carried or (row_month(book) == month and book["status"] == "open")):
        if row_month(book) == month - 1:
            return f"{book['id']} belongs to {month_label(row_month(book))}, closed, and was not flagged"
        if row_month(book) != month:
            return f"{book['id']} is not a line of {month_label(month)}"
        return f"{book['id']} is {book['status']}, not open"
    if Decimal(book["amount"]) != Decimal(bank["amount"]):
        return f"the amounts differ: bank {bank['amount']}, books {book['amount']}"
    if book["ref"].upper() not in bank["description"].upper():
        return f"the books reference {book['ref']} does not appear in the bank description {bank['description']!r}"
    window = CARRIED_WINDOW_DAYS if carried else WINDOW_DAYS
    gap = abs((date.fromisoformat(bank["date"]) - date.fromisoformat(book["date"])).days)
    if gap > window:
        return f"the dates are {gap} days apart; a pair must be within {window} days"
    return None


def _pair(bank: dict[str, str], book: dict[str, str]) -> None:
    bank["status"], bank["match"] = "matched", book["id"]
    book["status"], book["match"] = "matched", bank["id"]


def act(workdir: Path, action: str, params: dict[str, Any]) -> dict[str, Any]:
    ledgers = Ledgers(workdir)
    try:
        if ledgers.month > MONTHS:
            raise Refused("every month is closed")
        detail = _apply(ledgers, action, params)
    except Refused as refusal:
        return {"status": "refused", "detail": str(refusal)}
    closed = ledgers.close_months()
    ledgers.save()
    return _ok(detail, closed)


def _apply(ledgers: Ledgers, action: str, params: dict[str, Any]) -> str:
    month = ledgers.month
    if action == "AUTO_MATCH":
        open_bank = [row for row in ledgers.bank if row_month(row) == month and row["status"] == "open"]
        candidates = {
            bank["id"]: [book for book in ledgers.books if _pair_problem(ledgers, bank, book) is None]
            for bank in open_bank
        }
        count = 0
        for bank in open_bank:
            found = candidates[bank["id"]]
            if len(found) != 1:
                continue
            book = found[0]
            rivals = [other for other, books in candidates.items() if any(item["id"] == book["id"] for item in books)]
            if rivals == [bank["id"]] and book["status"] in ("open", "flagged"):
                _pair(bank, book)
                count += 1
        return f"matched {count} pair(s)"
    if action == "MATCH":
        bank = _current_bank(ledgers, str(params["bank"]))
        if bank["status"] != "open":
            raise Refused(f"{bank['id']} is {bank['status']}, not open")
        target = ledgers.books_row(str(params["book"]))
        if target is None:
            raise Refused(f"no books line {params['book']}")
        problem = _pair_problem(ledgers, bank, target)
        if problem is not None:
            raise Refused(problem)
        _pair(bank, target)
        return f"matched {bank['id']} with {target['id']}"
    if action == "UNMATCH":
        bank = _current_bank(ledgers, str(params["bank"]))
        if bank["status"] != "matched":
            raise Refused(f"{bank['id']} is {bank['status']}, not matched")
        partner = ledgers.books_row(bank["match"])
        bank["status"], bank["match"] = "open", ""
        if partner is not None and partner["note"] == RECORDED_NOTE:
            ledgers.books.remove(partner)
            ledgers.state["recorded"] -= 1
            return f"unmatched {bank['id']} and removed the recorded line {partner['id']}"
        if partner is not None:
            partner["status"], partner["match"] = ("flagged" if partner["flag"] else "open"), ""
        return f"unmatched {bank['id']}"
    if action == "FIX":
        book = _current_books(ledgers, str(params["book"]))
        if book["status"] != "open":
            raise Refused(f"{book['id']} is {book['status']}; only an open line can be corrected")
        field_name, value = str(params["field"]), str(params["value"]).strip()
        if field_name == "amount":
            if not AMOUNT.fullmatch(value):
                raise Refused(f"{value!r} is not an amount such as -1234.50")
            new = Decimal(value)
            if new == Decimal(book["amount"]):
                raise Refused(f"{book['id']} already reads {book['amount']}")
            book["note"] = (book["note"] + f"; amount was {book['amount']}").lstrip("; ")
            book["amount"] = money(new)
        elif field_name == "date":
            if not DAY.fullmatch(value):
                raise Refused(f"{value!r} is not a date such as {month_label(month)}-15")
            try:
                new_date = date.fromisoformat(value)
            except ValueError as error:
                raise Refused(f"{value!r} is not a calendar date") from error
            if month_of_date(new_date) != month:
                raise Refused(f"{value} is outside {month_label(month)}")
            if new_date.isoformat() == book["date"]:
                raise Refused(f"{book['id']} is already dated {book['date']}")
            book["note"] = (book["note"] + f"; date was {book['date']}").lstrip("; ")
            book["date"] = new_date.isoformat()
        else:
            raise Refused(f"{field_name!r} cannot be corrected")
        ledgers.state["fixes"] += 1
        return f"corrected the {field_name} of {book['id']}"
    if action == "RECORD":
        bank = _current_bank(ledgers, str(params["bank"]))
        if bank["status"] != "open":
            raise Refused(f"{bank['id']} is {bank['status']}, not open")
        category = str(params["category"])
        if category not in RECORD_CATEGORIES:
            raise Refused(f"{category!r} is not a category")
        ref, payee = RECORD_CATEGORIES[category]
        if ref not in bank["description"].upper():
            raise Refused(f"{bank['id']} ({bank['description']}) is not a bank {category} line")
        number = ledgers.state["next_row"][str(month)]
        ledgers.state["next_row"][str(month)] = number + 1
        book = {"id": f"K{month}-{number:02d}", "date": bank["date"], "payee": payee, "ref": ref,
                "amount": bank["amount"], "status": "open", "match": "", "flag": "", "note": RECORDED_NOTE}
        ledgers.books.append(book)
        _pair(bank, book)
        ledgers.state["recorded"] += 1
        return f"recorded {bank['id']} in the books as {book['id']}"
    if action == "FLAG":
        book = _current_books(ledgers, str(params["book"]))
        if book["status"] != "open":
            raise Refused(f"{book['id']} is {book['status']}, not open")
        reason = str(params["reason"])
        day = date.fromisoformat(book["date"]).day
        end = last_day(month)
        amount = Decimal(book["amount"])
        if reason == "outstanding_check":
            if not book["ref"].isdigit() or amount >= 0:
                raise Refused(f"{book['id']} is not a check")
            if day < end - OUTSTANDING_DAYS + 1:
                raise Refused(f"only a check dated in the last {OUTSTANDING_DAYS} days of the month can be outstanding")
        elif reason == "deposit_in_transit":
            if not (book["ref"].startswith("D") and book["ref"][1:].isdigit()) or amount <= 0:
                raise Refused(f"{book['id']} is not a deposit")
            if day < end - TRANSIT_DAYS + 1:
                raise Refused(f"only a deposit dated in the last {TRANSIT_DAYS} days of the month can be in transit")
        else:
            raise Refused(f"{reason!r} is not a reason")
        book["status"], book["flag"] = "flagged", reason
        return f"flagged {book['id']} as {reason}"
    if action == "UNFLAG":
        book = _current_books(ledgers, str(params["book"]))
        if book["status"] != "flagged":
            raise Refused(f"{book['id']} is {book['status']}, not flagged")
        book["status"], book["flag"] = "open", ""
        return f"unflagged {book['id']}"
    if action == "DELETE":
        book = _current_books(ledgers, str(params["book"]))
        if book["status"] != "open":
            raise Refused(f"{book['id']} is {book['status']}, not open")
        key = (book["date"], book["payee"], book["ref"], book["amount"])
        twins = [row for row in ledgers.books if row is not book and row["status"] != "deleted"
                 and row_month(row) == month and (row["date"], row["payee"], row["ref"], row["amount"]) == key]
        if not twins:
            raise Refused(f"only a line entered twice can be deleted, and {book['id']} has no twin")
        book["status"] = "deleted"
        ledgers.state["deleted"] += 1
        return f"deleted {book['id']}, a twin of {twins[0]['id']}"
    raise SystemExit(f"unknown action {action!r}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["setup", "observe", "act", "truth"])
    parser.add_argument("action", nargs="?")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "truth":
        print(json.dumps(truth(args.seed), indent=1, sort_keys=True))
        return 0
    if args.workdir is None:
        parser.error(f"{args.command} needs --workdir")
    if args.command == "setup":
        setup(args.workdir, args.seed)
        return 0
    if args.command == "observe":
        print(json.dumps(observe(args.workdir), sort_keys=True))
        return 0
    try:
        params = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, InvalidOperation) as error:
        raise SystemExit(f"the parameters are not a JSON object: {error}") from error
    print(json.dumps(act(args.workdir, args.action, params), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
