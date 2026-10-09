"""Defect rules (ADR-006): static checks of a SELECT against the catalogued legacy defects.

``check`` analyses a query's AST and reports which defect rules it breaks. Rules warn, they never
block: static analysis of SQL is heuristic, so a false positive must cost a warning, not an answer.

The analysis follows every column back to the base table it comes from, through CTEs and
subqueries:
- ``base``  is the table.column a name *directly* projects (no function in between);
- ``feeds`` is every table.column that contributes to it, however transformed.
Cleaning a text amount inside a CTE is therefore seen where it happens, and the outer ``SUM`` of
the cleaned value is not mistaken for a fresh use of the raw column.

Each rule in the ontology names a ``check`` (one of the functions registered below) and its
parameters, so most defects are data; only a new *kind* of check needs Python here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from legacybridge import policy
from legacybridge.mcp_server.schema import Schema
from legacybridge.semantic.loader import Ontology, Rule

Base = tuple[str, str]  # (table, column)
COMPARISONS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.Like, exp.ILike, exp.In,
                exp.Between, exp.Is, exp.NullSafeEQ, exp.NullSafeNEQ, exp.RegexpLike)  # fmt: skip
DATE_TYPES = (exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ,
              exp.DataType.Type.DATETIME)  # fmt: skip
GUARDS = (exp.Coalesce, exp.Nullif, exp.Case, exp.Greatest, exp.If)


@dataclass
class Violation:
    defect: str  # FAC-xx / SIC-xx, or LINT-xx for general checks
    rule: str
    severity: str
    message: str
    fix: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "defect": self.defect, "rule": self.rule, "severity": self.severity,
            "message": self.message, "fix": self.fix, "detail": self.detail,
        }  # fmt: skip


@dataclass
class RuleReport:
    violations: list[Violation] = field(default_factory=list)
    defects_touched: list[str] = field(default_factory=list)

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "warning"]


@dataclass
class Occ:
    """One use of a column in the query."""

    col: exp.Column
    scope: Scope
    base: Base | None
    feeds: frozenset[Base]


@dataclass
class Pred:
    """A comparison that has the column (possibly wrapped by functions) as an operand."""

    node: exp.Expression
    occ: Occ
    wrappers: tuple[exp.Expression, ...]
    negated: bool


def ancestors(node: exp.Expression) -> Iterator[exp.Expression]:
    """Parents of ``node`` up to (not including) the enclosing SELECT."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        yield parent
        parent = parent.parent


def lit(node: exp.Expression | None) -> str | None:
    return node.name if isinstance(node, exp.Literal) and node.is_string else None


def number(node: exp.Expression | None) -> float | None:
    if isinstance(node, exp.Neg):
        inner = number(node.this)
        return -inner if inner is not None else None
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return float(node.name)
        except ValueError:
            return None
    return None


def qualified(base: Base) -> str:
    return f"{base[0]}.{base[1]}"


class Analysis:
    def __init__(self, sql: str, schema: Schema, ontology: Ontology):
        self.ontology = ontology
        self.schema = schema
        tree = sqlglot.parse_one(sql, read="postgres")
        self.tree = qualify(
            tree.copy(),
            schema={t: dict.fromkeys(cols, "text") for t, cols in schema.items()},
            dialect="postgres",
            quote_identifiers=True,
            identify=False,
        )
        self.scopes: list[Scope] = traverse_scope(self.tree)
        self._select_scope = {
            id(s.expression): s for s in self.scopes if isinstance(s.expression, exp.Select)
        }
        seen: set[int] = set()
        self.occ: list[Occ] = []
        for scope in self.scopes:
            for col in scope.columns:
                if id(col) in seen:
                    continue
                seen.add(id(col))
                self.occ.append(
                    Occ(col, scope, self.base(col, scope), frozenset(self.feeds(col, scope)))
                )
        self.tables_used = {
            src.name
            for s in self.scopes
            for src in s.sources.values()
            if isinstance(src, exp.Table)
        }
        self._pred_ids = self._predicate_ids()

    # ------------------------------------------------------------------ lineage
    @staticmethod
    def _source(col: exp.Column, scope: Scope) -> Any:
        s: Scope | None = scope
        while s is not None:
            if col.table in s.sources:
                return s.sources[col.table]
            s = s.parent
        return None

    def base(self, col: exp.Column, scope: Scope, depth: int = 0) -> Base | None:
        src = self._source(col, scope)
        if isinstance(src, exp.Table):
            return (src.name, col.name)
        if isinstance(src, Scope) and depth < 10:
            for proj in src.expression.selects:
                if proj.alias_or_name == col.name:
                    inner = proj.unalias()
                    return (
                        self.base(inner, src, depth + 1) if isinstance(inner, exp.Column) else None
                    )
        return None

    def feeds(self, col: exp.Column, scope: Scope, depth: int = 0) -> set[Base]:
        src = self._source(col, scope)
        if isinstance(src, exp.Table):
            return {(src.name, col.name)}
        out: set[Base] = set()
        if isinstance(src, Scope) and depth < 10:
            for proj in src.expression.selects:
                if proj.alias_or_name == col.name:
                    for inner in proj.unalias().find_all(exp.Column):
                        out |= self.feeds(inner, src, depth + 1)
        return out

    def expr_feeds(self, node: exp.Expression, scope: Scope) -> set[Base]:
        out: set[Base] = set()
        for col in node.find_all(exp.Column):
            out |= self.feeds(col, scope)
        return out

    def scope_for(self, node: exp.Expression) -> Scope | None:
        select = node if isinstance(node, exp.Select) else node.find_ancestor(exp.Select)
        return self._select_scope.get(id(select)) if select is not None else None

    # ------------------------------------------------------------------ occurrences
    def occs_of(self, column: str, pure: bool = True) -> list[Occ]:
        table, name = column.split(".", 1)
        wanted = (table, name)
        return [o for o in self.occ if (o.base == wanted if pure else wanted in o.feeds)]

    def used(self, column: str, pure: bool = False) -> bool:
        return bool(self.occs_of(column, pure))

    def _predicate_ids(self) -> set[int]:
        ids: set[int] = set()
        self._filter_ids: set[int] = set()  # WHERE / HAVING only, not join conditions
        for select in self.tree.find_all(exp.Select):
            filters = [select.args.get("where"), select.args.get("having")]
            roots = filters + [j.args.get("on") for j in select.args.get("joins") or []]
            for root in filters:
                if root is not None:
                    self._filter_ids |= {id(n) for n in root.walk()}
            for root in roots:
                if root is not None:
                    ids |= {id(n) for n in root.walk()}
        return ids

    def dimension_only(self, table: str) -> bool:
        """True when ``table`` is only joined to label or group (never filtered, never the FROM)."""
        nodes = [t for t in self.tree.find_all(exp.Table) if t.name == table]
        if not nodes or any(not isinstance(t.parent, exp.Join) for t in nodes):
            return False
        return not any(
            id(o.col) in self._filter_ids for o in self.occ if any(b[0] == table for b in o.feeds)
        )

    def in_predicate(self, node: exp.Expression) -> bool:
        return id(node) in self._pred_ids

    def has_predicate(self, column: str, pure: bool = False) -> bool:
        return any(self.in_predicate(o.col) for o in self.occs_of(column, pure))

    def preds_on(self, column: str, pure: bool = True) -> list[Pred]:
        out = []
        for o in self.occs_of(column, pure):
            if not self.in_predicate(o.col):
                continue
            path: list[exp.Expression] = []
            for n in ancestors(o.col):
                if isinstance(n, COMPARISONS):
                    parent = n.parent
                    negated = isinstance(parent, exp.Not) or isinstance(n, (exp.NEQ,))
                    out.append(Pred(n, o, tuple(path), negated))
                    break
                path.append(n)
        return out

    # ------------------------------------------------------------------ aggregates and joins
    @staticmethod
    def agg_of(col: exp.Expression) -> exp.AggFunc | None:
        for n in ancestors(col):
            if isinstance(n, exp.AggFunc):
                return n
        return None

    @staticmethod
    def robust_agg(agg: exp.AggFunc) -> bool:
        """An aggregate that gives the same answer when rows are duplicated."""
        return isinstance(agg, (exp.Min, exp.Max)) or (
            isinstance(agg, exp.Count) and isinstance(agg.this, exp.Distinct)
        )

    def aggs_in(self, scope: Scope) -> list[exp.AggFunc]:
        return [
            a
            for a in scope.expression.find_all(exp.AggFunc)
            if a.find_ancestor(exp.Select) is scope.expression
        ]

    def aggregated(self, column: str) -> bool:
        return any(self.agg_of(o.col) is not None for o in self.occs_of(column, pure=False))

    def join_equalities(self) -> Iterator[tuple[exp.EQ, Scope, bool]]:
        """(equality, scope, is_inner) for every equality inside a JOIN ... ON."""
        for select in self.tree.find_all(exp.Select):
            scope = self._select_scope.get(id(select))
            for join in select.args.get("joins") or []:
                on = join.args.get("on")
                if on is None or scope is None:
                    continue
                inner = not join.args.get("side") and (join.args.get("kind") or "") != "CROSS"
                for eq in on.find_all(exp.EQ):
                    yield eq, scope, inner

    def under_exists_or_in(self, scope: Scope) -> bool:
        node = scope.expression.parent
        for _ in range(2):
            if isinstance(node, (exp.Exists, exp.In)):
                return True
            node = node.parent if node is not None else None
        return False

    def is_key_lookup(self, table: str) -> bool:
        """An equality of a key column to a literal, also when the column is normalised first.

        The correct way to look up an invoice line by folio (FAC-01) is
        ``LPAD(TRIM(numfac), 10, '0') = '...'``; treating that as "not a key lookup" would make the
        fix for one defect raise another.
        """
        for key in self.ontology.key_columns(table):
            for p in self.preds_on(f"{table}.{key}", pure=True):
                if isinstance(p.node, exp.EQ) and (lit(p.node.expression) or lit(p.node.this)):
                    return True
        return False

    def year_bounds(self, column: str) -> tuple[int, int]:
        """Years (inclusive) that the predicates on a YYYYMMDD text column allow."""
        lo, hi = 0, 9999

        def year(node: exp.Expression | None) -> int | None:
            s = lit(node)
            return int(s[:4]) if s and s[:4].isdigit() else None

        for p in self.preds_on(column, pure=False):
            n = p.node
            if isinstance(n, exp.Between):
                a, b = year(n.args.get("low")), year(n.args.get("high"))
                lo, hi = max(lo, a if a is not None else lo), min(hi, b if b is not None else hi)
            elif isinstance(n, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like)):
                col_left = n.this is p.occ.col or p.occ.col in list(n.this.find_all(exp.Column))
                y = year(n.expression if col_left else n.this)
                if y is None:
                    continue
                kind = type(n)
                if not col_left:  # literal on the left: mirror the operator
                    kind = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE}.get(
                        kind, kind
                    )
                if kind in (exp.EQ, exp.Like):
                    lo, hi = max(lo, y), min(hi, y)
                elif kind in (exp.GT, exp.GTE):
                    lo = max(lo, y)
                else:
                    hi = min(hi, y)
        return lo, hi

    def columns_touched(self) -> set[Base]:
        return {b for o in self.occ for b in o.feeds}


# ---------------------------------------------------------------------------------- checks
Check = Callable[[Analysis, Rule], list[str]]
CHECKS: dict[str, Check] = {}


def check_type(fn: Check) -> Check:
    CHECKS[fn.__name__.removeprefix("check_")] = fn
    return fn


@check_type
def check_predicate_required(a: Analysis, rule: Rule) -> list[str]:
    """A table is used but the query has no condition on the columns that carry its state."""
    found = []
    for target in rule.params["targets"]:
        table = target["table"]
        if table not in a.tables_used:
            continue
        if target.get("when_aggregating") and not a.aggregated(target["when_aggregating"]):
            continue
        if target.get("key_lookup_exempt", True) and a.is_key_lookup(table):
            continue
        if target.get("dimension_exempt") and a.dimension_only(table):
            continue
        if any(a.has_predicate(q) for q in target["requires"]):
            continue
        found.append(f"{table}: no condition on {', '.join(target['requires'])}")
    return found


@check_type
def check_numeric_cleaning(a: Analysis, rule: Rule) -> list[str]:
    """A text amount is cast or summed without stripping the characters that break the cast."""
    strip = rule.params.get("strip", [])
    replace = [tuple(p) for p in rule.params.get("replace", [])]
    found = []
    for column in rule.params["columns"]:
        for o in a.occs_of(column, pure=True):
            seen: set[tuple[str | None, str | None]] = set()
            numeric = cleaned_all = False
            for n in ancestors(o.col):
                if isinstance(n, exp.Replace):
                    seen.add((lit(n.args.get("expression")), lit(n.args.get("replacement"))))
                elif isinstance(n, (exp.RegexpReplace, exp.Translate)):
                    cleaned_all = True
                if _numeric_context(n):
                    numeric = True
                    break
            ok = cleaned_all or (
                all(any(s == p[0] for p in seen) for s in strip)
                and all(pair in seen for pair in replace)
            )
            if numeric and not ok:
                found.append(column)
    return sorted(set(found))


def _numeric_context(n: exp.Expression) -> bool:
    if isinstance(n, exp.Cast):
        return n.to.is_type(*exp.DataType.NUMERIC_TYPES)
    if isinstance(n, (exp.Sum, exp.Avg)):
        return True
    if isinstance(n, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
        return True
    if isinstance(n, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
        return number(n.expression) is not None or number(n.this) is not None
    if isinstance(n, exp.Between):
        return number(n.args.get("low")) is not None
    return False


@check_type
def check_date_text(a: Analysis, rule: Rule) -> list[str]:
    """A text date is used in a way that its bad values (or second format) will break or skew."""
    mode = rule.params.get("mode", "sentinel")
    found = []
    for column in rule.params["columns"]:
        for o in a.occs_of(column, pure=True):
            path = list(ancestors(o.col))
            trigger = next((n for n in path if _date_cast(n)), None)
            # a NULLIF between the column and the cast, or any CASE around either, guards it
            guarded = trigger is not None and (
                any(isinstance(n, exp.Nullif) for n in path[: path.index(trigger)])
                or any(isinstance(n, exp.Case) for n in path)
            )
            if mode == "sentinel":
                if trigger is not None and not guarded:
                    regex_guard = any(
                        isinstance(p.node, exp.RegexpLike) for p in a.preds_on(column, pure=True)
                    ) and not a.in_predicate(o.col)
                    if not regex_guard:
                        found.append(f"{column} cast to a date without guarding '00000000'/blank")
            elif trigger is not None and not any(isinstance(n, exp.Case) for n in path):
                found.append(f"{column} cast to a date without branching on its two formats")
        if mode == "mixed_format":
            for p in a.preds_on(column, pure=True):
                if not any(isinstance(n, exp.Case) for n in ancestors(p.occ.col)):
                    found.append(f"{column} compared as text although it has two formats")
        else:
            preds = a.preds_on(column, pure=True)
            for p in preds:
                n = p.node
                if not isinstance(n, (exp.LT, exp.LTE)) or n.this is not p.occ.col:
                    continue
                siblings = [q for q in preds if q.occ.scope is p.occ.scope and q.node is not n]
                lower = (exp.GT, exp.GTE, exp.Between, exp.EQ, exp.Like, exp.RegexpLike, exp.In)
                if not any(isinstance(q.node, lower) for q in siblings):
                    found.append(f"{column} has an upper bound only; '00000000' and blanks match")
    return sorted(set(found))


def _date_cast(n: exp.Expression) -> bool:
    if isinstance(n, exp.Cast):
        return n.to.is_type(*DATE_TYPES)
    if isinstance(n, (exp.TsOrDsToDate, exp.StrToDate, exp.TsOrDsToTimestamp)):
        return True
    return isinstance(n, exp.Anonymous) and n.name.lower() in ("to_date", "to_timestamp")


@check_type
def check_join_requires_transform(a: Analysis, rule: Rule) -> list[str]:
    """Two columns that do not match as stored are joined with a plain equality."""
    wanted = {tuple(p.split(".", 1)) for p in rule.params["pair"]}
    found = []
    for eq, scope, _ in a.join_equalities():
        sides = []
        for side in (eq.this, eq.expression):
            sides.append(a.base(side, scope) if isinstance(side, exp.Column) else None)
        if None not in sides and set(sides) == wanted and sides[0] != sides[1]:
            found.append(" = ".join(qualified(s) for s in sides))
    if column := rule.params.get("literal_column"):  # the same defect, compared to a literal
        for p in a.preds_on(column, pure=True):
            if isinstance(p.node, (exp.EQ, exp.In)) and not p.wrappers and not p.negated:
                found.append(f"{column} compared to a literal as stored")
    return sorted(set(found))


@check_type
def check_dedupe_required(a: Analysis, rule: Rule) -> list[str]:
    """A table that can hold the same record several times is read without picking one capture."""
    table, marker = rule.params["table"], rule.params["marker"]
    if table not in a.tables_used or any(o.col.name == marker for o in a.occ):
        return []
    found = []
    for scope in a.scopes:
        direct = any(isinstance(s, exp.Table) and s.name == table for s in scope.sources.values())
        if not direct or a.under_exists_or_in(scope):
            continue
        aggs = a.aggs_in(scope)
        if aggs and all(a.robust_agg(g) for g in aggs):
            continue
        found.append(f"{table} read without keeping the latest {marker}")
    return sorted(set(found))


@check_type
def check_orphan_join(a: Analysis, rule: Rule) -> list[str]:
    """An inner join from a fact to its catalogue silently drops the rows that have no match."""
    pairs = [tuple(tuple(c.split(".", 1)) for c in pair) for pair in rule.params["pairs"]]
    found = []
    for eq, scope, inner in a.join_equalities():
        if not inner:
            continue
        left, right = a.expr_feeds(eq.this, scope), a.expr_feeds(eq.expression, scope)
        for p1, p2 in pairs:
            if (p1 in left and p2 in right) or (p2 in left and p1 in right):
                found.append(f"{qualified(p1)} -> {qualified(p2)}")
    return sorted(set(found))


@check_type
def check_column_misuse(a: Analysis, rule: Rule) -> list[str]:
    """A column is filtered on although another column of a joined table is the right one."""
    column, when_table, prefer = (rule.params[k] for k in ("column", "when_table", "prefer"))
    if when_table not in a.tables_used or not a.has_predicate(column):
        return []
    return (
        []
        if a.has_predicate(prefer)
        else [f"filters on {column}; {prefer} is the one on the invoice"]
    )


@check_type
def check_reused_column_context(a: Analysis, rule: Rule) -> list[str]:
    """A column whose meaning depends on context is grouped or filtered without that context."""
    column = rule.params["column"]
    context = rule.params.get("context_columns", [])
    periods = rule.params.get("periods")
    risky = [
        o for o in a.occs_of(column, pure=True)
        if a.in_predicate(o.col) or any(isinstance(n, exp.Group) for n in ancestors(o.col))
    ]  # fmt: skip
    if not risky:
        return []
    names = {c.split(".", 1)[1] for c in context}
    for o in risky:  # CASE WHEN <context> THEN column ...
        case = next((n for n in ancestors(o.col) if isinstance(n, exp.Case)), None)
        if case is not None and any(c.name in names for c in case.find_all(exp.Column)):
            return []
    for p in a.preds_on(column, pure=True):  # a literal identifies the meaning by itself
        if isinstance(p.node, (exp.Like, exp.EQ, exp.In)) and any(
            lit(x) for x in p.node.find_all(exp.Literal)
        ):
            return []
    for c in context:
        if periods:
            lo, hi = a.year_bounds(c)
            if any(lo >= p[0] and hi <= p[1] for p in periods):
                return []
        elif a.has_predicate(c):
            return []
    return [f"{column} used without the context that gives it a meaning"]


@check_type
def check_currency_mixing(a: Analysis, rule: Rule) -> list[str]:
    """Amounts in two currencies are added although the database has no exchange rate."""
    amounts, currency = rule.params["amount_columns"], rule.params["currency_column"]
    summed = [
        o for q in amounts for o in a.occs_of(q, pure=False)
        if isinstance(a.agg_of(o.col), (exp.Sum, exp.Avg))
    ]  # fmt: skip
    if not summed or a.used(currency):
        return []
    date_column = rule.params["date_column"]
    lo, hi = a.year_bounds(date_column)
    first, last = rule.params["mixed_period"]
    if hi < first or lo > last:
        return []
    return ["amounts are summed with no currency handling in a period that mixes MXN and USD"]


@check_type
def check_needs_function_on_column(a: Analysis, rule: Rule) -> list[str]:
    """A column that is stored with inconsistent spelling is compared to a literal as-is."""
    classes = tuple(getattr(exp, name) for name in rule.params["functions"])
    found = []
    for p in a.preds_on(rule.params["column"], pure=True):
        if isinstance(p.node, (exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike)) and not any(
            isinstance(w, classes) for w in p.wrappers
        ):
            found.append(
                f"{rule.params['column']} compared without {'/'.join(rule.params['functions'])}"
            )
    return sorted(set(found))


@check_type
def check_value_set_complete(a: Analysis, rule: Rule) -> list[str]:
    """A code that changed over time is filtered by only one of its spellings."""
    groups = [set(g) for g in rule.params["groups"]]
    found = []
    for p in a.preds_on(rule.params["column"], pure=True):
        if p.negated or not isinstance(p.node, (exp.EQ, exp.In)):
            continue
        values = {lit(x) for x in p.node.find_all(exp.Literal)} - {None}
        for g in groups:
            if values & g and not g <= values:
                found.append(f"filters {sorted(values & g)} but {sorted(g - values)} mean the same")
    return sorted(set(found))


@check_type
def check_null_flag(a: Analysis, rule: Rule) -> list[str]:
    """A flag where NULL means 'no' is tested in a way that skips the NULLs."""
    column = rule.params["column"]
    null_aware = any(
        isinstance(p.node, (exp.Is, exp.NullSafeEQ, exp.NullSafeNEQ))
        or any(isinstance(w, exp.Coalesce) for w in p.wrappers)
        for p in a.preds_on(column, pure=True)
    )
    if null_aware:
        return []
    found = []
    for p in a.preds_on(column, pure=True):
        values = {lit(x) for x in p.node.find_all(exp.Literal)} - {None}
        positive_no = (
            isinstance(p.node, (exp.EQ, exp.In))
            and not p.negated
            and values == {rule.params["no_value"]}
        )
        negative_yes = p.negated and values == {rule.params["yes_value"]}
        if positive_no or negative_yes:
            found.append(f"{column} tested without counting NULL as '{rule.params['no_value']}'")
    return sorted(set(found))


@check_type
def check_unsafe_divisor(a: Analysis, rule: Rule) -> list[str]:
    """A column that is sometimes 0 or NULL is used in arithmetic without a guard."""
    found = []
    for o in a.occs_of(rule.params["column"], pure=True):
        guarded = False
        for n in ancestors(o.col):
            if isinstance(n, GUARDS):
                guarded = True
            if isinstance(n, (exp.Mul, exp.Div, exp.Mod)) and not guarded:
                found.append(f"{rule.params['column']} used in arithmetic without COALESCE/NULLIF")
                break
    return sorted(set(found))


@check_type
def check_aggregate_needs(a: Analysis, rule: Rule) -> list[str]:
    """An aggregate over a quantity ignores a convention that changes how it must be read."""
    column = rule.params["column"]
    sums = [
        o for o in a.occs_of(column, pure=True) if isinstance(a.agg_of(o.col), (exp.Sum, exp.Avg))
    ]
    if not sums:
        return []
    if reference := rule.params.get("needs_reference"):
        return (
            [] if a.used(reference) else [f"{column} aggregated with no reference to {reference}"]
        )
    trigger = rule.params["when_literal_on"]
    values = set(rule.params["when_values"])
    selected = {
        lit(x) for p in a.preds_on(trigger, pure=True) for x in p.node.find_all(exp.Literal)
    }
    if not (selected & values):
        return []
    wrapper = getattr(exp, rule.params["needs_wrapper"])
    for o in sums:
        if not any(isinstance(n, wrapper) for n in ancestors(o.col)):
            return [
                f"{column} summed for {sorted(selected & values)} without "
                f"{rule.params['needs_wrapper']}"
            ]
    return []


@check_type
def check_count_distinct_required(a: Analysis, rule: Rule) -> list[str]:
    """Rows are counted in a table where one real thing can appear under several keys."""
    table, distinct_on = rule.params["table"], rule.params["distinct_on"]
    if table not in a.tables_used:
        return []
    grouped = any(
        any(isinstance(n, exp.Group) for n in ancestors(o.col))
        for o in a.occs_of(distinct_on, False)
    )
    found = []
    for scope in a.scopes:
        if not any(isinstance(s, exp.Table) and s.name == table for s in scope.sources.values()):
            continue
        counts = [g for g in a.aggs_in(scope) if isinstance(g, exp.Count)]
        plain = [g for g in counts if not isinstance(g.this, exp.Distinct)]
        distinct_rfc = any(
            isinstance(g.this, exp.Distinct)
            and any(f"{b[0]}.{b[1]}" == distinct_on for b in a.expr_feeds(g.this, scope))
            for g in counts
        )
        if plain and not distinct_rfc and not grouped:
            found.append(f"counts {table} rows, not distinct {distinct_on}")
    return sorted(set(found))


def graph_pairs(ontology: Ontology, schema: Schema) -> frozenset[frozenset[Base]]:
    """Unordered pairs of base columns that the ontology's joins connect."""
    pairs: set[frozenset[Base]] = set()
    for j in ontology.joins:
        sql = (
            f"SELECT 1 FROM {j.left.table} AS {j.left.alias} "
            f"JOIN {j.right.table} AS {j.right.alias} ON {j.condition}"
        )
        probe = Analysis(sql, schema, ontology)
        for eq, scope, _ in probe.join_equalities():
            for left in probe.expr_feeds(eq.this, scope):
                for right in probe.expr_feeds(eq.expression, scope):
                    pairs.add(frozenset({left, right}))
    return frozenset(pairs)


_GRAPH_CACHE: dict[int, frozenset] = {}


@check_type
def check_join_not_in_graph(a: Analysis, rule: Rule) -> list[str]:
    """A join between columns that the ontology does not list as related."""
    key = id(a.ontology)
    if key not in _GRAPH_CACHE:
        _GRAPH_CACHE[key] = graph_pairs(a.ontology, a.schema)
    graph = _GRAPH_CACHE[key]
    found = []
    for eq, scope, _ in a.join_equalities():
        left, right = a.expr_feeds(eq.this, scope), a.expr_feeds(eq.expression, scope)
        combos = [(x, y) for x in left for y in right if x[0] != y[0]]
        if combos and not any(frozenset(c) in graph for c in combos):
            x, y = combos[0]
            found.append(f"{qualified(x)} = {qualified(y)}")
    return sorted(set(found))


# --------------------------------------------------------------------------- data checks
def _cells(rule: Rule, a: Analysis, rows: list[list]) -> Iterator[tuple[str, str]]:
    """(column, value) for every result cell that comes from one of the rule's columns."""
    wanted = {tuple(c.split(".", 1)) for c in rule.params["columns"]}
    selects = a.tree.selects if isinstance(a.tree, exp.Query) else []
    top = a.scopes[-1] if a.scopes else None
    for i, proj in enumerate(selects):
        if top is None:
            break
        bases = a.expr_feeds(proj, top)
        for b in bases & wanted:
            for row in rows:
                if i < len(row) and isinstance(row[i], str):
                    yield qualified(b), row[i]


def data_injection_text(a: Analysis, rule: Rule, rows: list[list]) -> list[str]:
    return sorted(
        {col for col, value in _cells(rule, a, rows) if policy.looks_like_instructions(value)}
    )


def data_mojibake(a: Analysis, rule: Rule, rows: list[list]) -> list[str]:
    return sorted({col for col, value in _cells(rule, a, rows) if "Ã" in value})


DATA_CHECKS: dict[str, Callable[[Analysis, Rule, list[list]], list[str]]] = {
    "injection_text": data_injection_text,
    "mojibake": data_mojibake,
}


# --------------------------------------------------------------------------- entry points
class Checker:
    """Analyse a query once; run its static rules now and its data rules when rows exist."""

    def __init__(self, sql: str, schema: Schema, ontology: Ontology):
        self.analysis = Analysis(sql, schema, ontology)
        self.ontology = ontology

    def _rules(self) -> Iterator[tuple[str, str, Rule]]:
        for d in self.ontology.defects:
            yield d.id, d.title, d.rule
        for lint in self.ontology.lints:
            yield lint.id, lint.title, lint.rule

    @staticmethod
    def _violation(ident: str, rule: Rule, detail: str) -> Violation:
        message = rule.message.replace("{detail}", detail)
        return Violation(ident, rule.check, rule.severity, message, rule.fix, detail)

    def static(self) -> list[Violation]:
        out = []
        for ident, _, rule in self._rules():
            if rule.kind != "sql":
                continue
            for detail in CHECKS[rule.check](self.analysis, rule):
                out.append(self._violation(ident, rule, detail))
        return out

    def data(self, rows: list[list]) -> list[Violation]:
        out = []
        for ident, _, rule in self._rules():
            if rule.kind != "data":
                continue
            for detail in DATA_CHECKS[rule.check](self.analysis, rule, rows):
                out.append(self._violation(ident, rule, detail))
        return out

    def touched(self) -> list[str]:
        columns = {qualified(b) for b in self.analysis.columns_touched()}
        return sorted(d.id for d in self.ontology.defects if columns & {c for c in d.columns})


def check(sql: str, schema: Schema, ontology: Ontology) -> RuleReport:
    """Static rules for ``sql``. A query that cannot be analysed yields an empty report."""
    try:
        checker = Checker(sql, schema, ontology)
        return RuleReport(checker.static(), checker.touched())
    except (SqlglotError, KeyError, RecursionError):
        return RuleReport()
