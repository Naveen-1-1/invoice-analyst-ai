"""Companion AP data for a corpus: who we are, who we buy from, what we ordered and received, and an inbox.

Built from the generator's own document specs (nothing in the benchmark is changed). Every random choice uses its
own generator, Random(f"{seed}:{doc_id}:{purpose}"), so adding or changing one never shifts any other.

Outputs (under data/<set>/):
  companion/entities.json          our companies: every bill-to customer (the corpus has no single buyer)
  companion/vendors.json           vendor list with bank account, payment terms, ~15% with an early-pay discount
  companion/purchase_orders.json   one PO per invoice that prints a PO number, built from its lines, with its
                                   goods receipts (received in full, unless a short delivery is planted)
  companion/vendor_history.json    3-12 past invoices per vendor (records only, no documents)
  inbox/stream.json                arrival order: each document 1-7 days after its issue date
  inbox/documents/                 planted resubmissions (an existing invoice re-photographed)
  labels/ap_labels.json            what the checks should find per stream item  (bench only; never the backend)
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import random
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import specs

COMPANION_SEEDS = {"dev": 4201}
PLANT_PER_TYPE = 3
PLANT_TYPES = ("price_over_po", "short_delivery", "changed_bank", "unknown_vendor", "wrong_addressee",
               "tax_id_mismatch", "amount_above_history", "duplicate_resubmission")
# what each planted problem should make the system report
PLANT_EXPECTS = {"price_over_po": ["match.price_over_po"], "short_delivery": ["match.qty_over_received"],
                 "changed_bank": ["bank.changed"], "unknown_vendor": ["vendor.unknown"],
                 "wrong_addressee": ["pattern.wrong_addressee"], "tax_id_mismatch": ["vendor.tax_id_mismatch"],
                 "amount_above_history": ["pattern.above_history"], "duplicate_resubmission": ["dup.*"]}
DISCOUNT_SHARE = 0.15
RESUBMIT_LOOKS = (("perspective", "mild"), ("shadow", "mild"), ("rotate_small", "mild"), ("jpeg", "mild"))


def _rng(seed: int, *parts: Any) -> random.Random:
    return random.Random(":".join(str(p) for p in (seed, *parts)))


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


def parse_bank(lines: tuple[str, ...] | None) -> dict[str, Any] | None:
    """The printed remittance lines as a structured account (IBAN, sort code + account, or account + branch)."""
    if not lines:
        return None
    fields = {}
    for line in lines:
        key, _, value = line.partition(":")
        fields[key.strip().casefold()] = value.strip()
    if "iban" in fields:
        return {"kind": "iban", "account_norm": fields["iban"].replace(" ", "").upper(),
                "bank_name": fields.get("bank"), "bic": fields.get("bic"), "branch": None}
    if "sort code" in fields:
        return {"kind": "sort_code", "account_norm": re.sub(r"\D", "", fields["sort code"]) +
                re.sub(r"\D", "", fields.get("account", "")), "bank_name": fields.get("bank"), "bic": None,
                "branch": fields["sort code"]}
    return {"kind": "account", "account_norm": re.sub(r"\D", "", fields.get("account no", "")),
            "bank_name": fields.get("bank"), "bic": fields.get("swift"), "branch": fields.get("branch")}


def _new_bank(rng: random.Random, country: str, like: dict[str, Any] | None) -> dict[str, Any]:
    """A different account at a plausible bank (used when none is printed, and for planted bank changes)."""
    if (like or {}).get("kind") == "iban" or country in ("DE", "NL", "FR"):
        digits = "".join(str(rng.randint(0, 9)) for _ in range(16))
        return {"kind": "iban", "account_norm": f"{country}{rng.randint(10, 99)}{digits}",
                "bank_name": (like or {}).get("bank_name") or "Commercial Bank", "bic": (like or {}).get("bic"),
                "branch": None}
    return {"kind": "account", "account_norm": str(rng.randint(10**9, 10**10 - 1)),
            "bank_name": (like or {}).get("bank_name") or "Commercial Bank", "bic": (like or {}).get("bic"),
            "branch": str(rng.randint(100000, 999999))}


def _terms_days(terms: str | None) -> int | None:
    m = re.search(r"(\d+)", terms or "")
    return int(m.group(1)) if m else None


def billables(spec) -> list:
    """The invoices/credit notes a document contains (a multi-invoice file holds several)."""
    if spec.kind == "multi_invoice":
        return list(spec.children)
    if spec.kind == "statement":
        return []
    return [spec]


def _gl(spec) -> str:
    return "5100" if spec.category == "freight" else "5200"


def _inputs(set_name: str) -> tuple[int, dict[str, Any], Path]:
    """(companion seed, specs, documents folder) for a set."""
    if set_name == "dev":
        return COMPANION_SEEDS["dev"], specs.load(specs.DEV_SEED), specs.DEV_CORPUS / "documents"
    if set_name == "heldout":
        from . import heldout  # noqa: PLC0415
        if heldout.companion_sealed():
            raise SystemExit("the held-out companion data is already generated and sealed (decision 44)")
        if problems := heldout.verify():
            raise SystemExit("the held-out set doesn't check out: " + "; ".join(problems))
        os.environ["INVOICE_BENCH_HAND_FONT"] = heldout.HAND_FONT  # re-rendered photos use the sealed set's font
        return heldout.companion_seed(), specs.load(heldout._seed()), heldout.CORPUS_DIR / "documents"
    raise SystemExit(f"unknown set {set_name!r}: dev or heldout")


def build(set_name: str = "dev") -> dict[str, Any]:
    """Write the set's companion data. For the held-out set (decision 44) also its extended answer keys, then the
    whole lot is added to the seal; only counts are returned, never contents."""
    seed, corpus, docs_dir = _inputs(set_name)
    root = specs.ROOT / "data" / set_name
    corpus_v1, gen, layout = specs.generator_modules()
    files = {p.stem: p for p in docs_dir.iterdir() if p.is_file() and not p.name.startswith(".")}

    # --- master data from the specs ------------------------------------------------------------------------
    vendors: dict[str, dict] = {}
    entities: dict[str, dict] = {}
    for doc_id, spec in corpus.items():
        for inv in billables(spec) or [spec]:
            v = inv.vendor
            if v.name not in vendors:
                r = _rng(seed, v.name, "vendor")
                printed = parse_bank(v.bank)
                discount = r.random() < DISCOUNT_SHARE
                vendors[v.name] = {
                    "code": "", "name": v.name, "country": v.country, "address": ", ".join(v.address),
                    "tax_id": v.tax_id, "tax_label": v.tax_label, "email": v.email, "phone": v.phone,
                    "terms_days": _terms_days(inv.terms) or r.choice([30, 30, 14, 60]),
                    "discount_pct": 2.0 if discount else None, "discount_days": 10 if discount else None,
                    "default_gl": _gl(inv), "bank": printed or _new_bank(r, v.country, None),
                    "bank_printed": printed is not None, "_docs": []}
            vendors[v.name]["_docs"].append(doc_id)
            c = inv.customer
            entities.setdefault(c.name, {"code": "", "name": c.name, "country": c.country,
                                         "address": ", ".join(c.address), "tax_id": c.tax_id, "_docs": []})
            entities[c.name]["_docs"].append(doc_id)
    for i, name in enumerate(sorted(vendors), 1):
        vendors[name]["code"] = f"V{i:04d}"
    for i, name in enumerate(sorted(entities), 1):
        entities[name]["code"] = f"E{i:04d}"

    # --- POs and goods receipts: only where the invoice prints a PO number ------------------------------------
    pos: dict[tuple[str, str], dict] = {}
    for doc_id, spec in corpus.items():
        for inv in billables(spec):
            if not inv.po_number or inv.kind == "credit_note":
                continue
            key = (inv.vendor.name, inv.po_number)
            if key in pos:  # photo/scan copies of one invoice share its PO
                pos[key]["_docs"].append(doc_id)
                continue
            r = _rng(seed, doc_id, "po")
            order_date = inv.issue_date - dt.timedelta(days=r.randint(7, 30))
            lines = [{"line_no": n, "item_code": ln.code, "description": ln.description,
                      "qty": ln.qty if ln.pct is None else None, "unit_price": ln.unit_price if ln.pct is None else None,
                      "amount": ln.amount, "vat_rate": ln.vat_rate, "gl_account": _gl(inv)}
                     for n, ln in enumerate(inv.lines, 1)]
            received = order_date + dt.timedelta(days=r.randint(1, max(1, (inv.issue_date - order_date).days)))
            pos[key] = {"po_number": inv.po_number, "vendor_code": vendors[inv.vendor.name]["code"],
                        "entity_code": entities[inv.customer.name]["code"], "currency": inv.currency,
                        "order_date": order_date.isoformat(), "status": "open", "lines": lines,
                        "receipts": [{"grn_number": f"GRN-{r.randint(10000, 99999)}",
                                      "received_date": received.isoformat(),
                                      "lines": [{"po_line_no": ln["line_no"], "qty": ln["qty"]}
                                                for ln in lines if ln["qty"] is not None]}],
                        "_docs": [doc_id]}

    # --- vendor history: past invoices around each vendor's usual amount --------------------------------------
    history: dict[str, list[dict]] = {}
    for name, v in vendors.items():
        r = _rng(seed, name, "history")
        totals = [b.total for d in v["_docs"] for b in billables(corpus[d]) if b.vendor.name == name]
        typical = statistics.median(totals) if totals else 1000.0
        first = min(b.issue_date for d in v["_docs"] for b in (billables(corpus[d]) or [corpus[d]]))
        currency = next((b.currency for d in v["_docs"] for b in billables(corpus[d])), "USD")
        history[v["code"]] = [{"invoice_number": f"H{v['code'][1:]}-{k:03d}",
                               "invoice_date": (first - dt.timedelta(days=r.randint(20, 365))).isoformat(),
                               "amount": _money(typical * r.uniform(0.6, 1.4)), "currency": currency}
                              for k in range(r.randint(3, 12))]

    # --- the inbox: each document arrives 1-7 days after its issue date -----------------------------------
    stream = []
    for doc_id, spec in corpus.items():
        r = _rng(seed, doc_id, "arrival")
        issue = (billables(spec) or [spec])[0].issue_date
        stream.append({"ref": doc_id, "path": str(files[doc_id].relative_to(specs.ROOT)),
                       "received_date": (issue + dt.timedelta(days=r.randint(1, 7))).isoformat()})

    # --- planted problems: three of each type, one per invoice, only where they fit ----------------------------
    flagged = {d for d, s in corpus.items() if s.should_flag}
    fixtures = {n for n, v in vendors.items() if len(v["_docs"]) > 1}  # shared vendors: fixtures and photo copies
    po_of = {d: key for key, po in pos.items() for d in po["_docs"]}
    def plain(doc_id: str) -> bool:
        s = corpus[doc_id]
        return (doc_id not in flagged and s.kind == "invoice" and s.category not in ("photo", "scan")
                and s.render.mode == "pdf")
    def priced(ln: dict) -> bool:  # big enough that a 4% overcharge exceeds the 0.50-per-unit tolerance
        return bool(ln["qty"]) and (ln["unit_price"] or 0) >= 20
    eligible = {
        "price_over_po": lambda d: plain(d) and d in po_of and any(priced(ln) for ln in pos[po_of[d]]["lines"]),
        "short_delivery": lambda d: plain(d) and d in po_of and any((ln["qty"] or 0) >= 2 for ln in pos[po_of[d]]["lines"]),
        "changed_bank": lambda d: plain(d) and vendors[corpus[d].vendor.name]["bank_printed"]
                                  and corpus[d].vendor.name not in fixtures,
        "unknown_vendor": lambda d: plain(d) and corpus[d].vendor.name not in fixtures and d not in po_of,
        "wrong_addressee": lambda d: plain(d) and len(entities[corpus[d].customer.name]["_docs"]) == 1,
        "tax_id_mismatch": lambda d: plain(d) and bool(corpus[d].vendor.tax_id) and corpus[d].vendor.name not in fixtures,
        "amount_above_history": lambda d: plain(d) and corpus[d].vendor.name not in fixtures,
        "duplicate_resubmission": lambda d: plain(d) and len(layout.build_pages(corpus[d])) == 1,
    }
    planted: dict[str, str] = {}  # doc_id -> type
    r = _rng(seed, "plant")
    order = sorted(corpus)
    for kind in PLANT_TYPES:
        candidates = [d for d in order if d not in planted and eligible[kind](d)]
        for d in r.sample(candidates, min(PLANT_PER_TYPE, len(candidates))):
            planted[d] = kind

    resubmissions = []
    for doc_id, kind in sorted(planted.items()):
        spec = corpus[doc_id]
        vr = _rng(seed, doc_id, "plant", kind)
        v = vendors[spec.vendor.name]
        if kind == "price_over_po":
            po = pos[po_of[doc_id]]
            line = vr.choice([ln for ln in po["lines"] if priced(ln)])
            line["unit_price"] = _money(line["unit_price"] * (1 - vr.uniform(0.04, 0.15)))
            line["amount"] = _money(line["unit_price"] * line["qty"])
        elif kind == "short_delivery":
            po = pos[po_of[doc_id]]
            line = vr.choice([ln for ln in po["lines"] if (ln["qty"] or 0) >= 2])
            rec = next(x for x in po["receipts"][0]["lines"] if x["po_line_no"] == line["line_no"])
            rec["qty"] = line["qty"] - vr.randint(1, int(line["qty"]) - 1)
        elif kind == "changed_bank":
            v["bank"] = _new_bank(vr, v["country"], v["bank"])
        elif kind == "unknown_vendor":
            v["_remove"] = True
        elif kind == "wrong_addressee":
            entities[spec.customer.name]["_remove"] = True
        elif kind == "tax_id_mismatch":
            digits = [i for i, ch in enumerate(v["tax_id"]) if ch.isdigit()]
            i = vr.choice(digits)
            v["tax_id"] = v["tax_id"][:i] + str((int(v["tax_id"][i]) + vr.randint(1, 8)) % 10) + v["tax_id"][i + 1:]
        elif kind == "amount_above_history":
            scale = spec.total / vr.uniform(4.0, 8.0)
            for h in history[v["code"]]:
                h["amount"] = _money(scale * vr.uniform(0.8, 1.2))
        elif kind == "duplicate_resubmission":
            look, sev = vr.choice(RESUBMIT_LOOKS)
            new_id = f"resubmit-{doc_id}"
            photo = corpus_v1._degraded(spec, new_id, "photo", look, sev, "jpg")
            data, ext, _pages, _info = gen.render_document(photo, seed)
            path = root / "inbox" / "documents" / f"{new_id}.{ext}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            original = next(s for s in stream if s["ref"] == doc_id)
            resubmissions.append({"ref": new_id, "path": str(path.relative_to(specs.ROOT)), "of": doc_id,
                                  "gap": vr.randint(5, 40), "after": original})

    # place resubmissions 5-40 positions after their original, received in that slot
    stream.sort(key=lambda s: (s["received_date"], hashlib.sha256(s["ref"].encode()).hexdigest()))
    for sub in resubmissions:
        idx = next(i for i, s in enumerate(stream) if s["ref"] == sub["of"])
        pos_ = min(len(stream), idx + sub["gap"])
        received = stream[pos_ - 1]["received_date"] if pos_ > 0 else sub["after"]["received_date"]
        stream.insert(pos_, {"ref": sub["ref"], "path": sub["path"], "received_date": received})
    for seq, s in enumerate(stream, 1):
        s["seq"] = seq

    # --- labels: what each stream item should trigger ------------------------------------------------------
    first_of_invoice: dict[tuple[str, str], str] = {}
    labels = []
    for s in stream:
        ref = s["ref"]
        original = next((x["of"] for x in resubmissions if x["ref"] == ref), None)
        spec = corpus[original or ref]
        items = []
        for k, inv in enumerate(billables(spec)):
            v = vendors[inv.vendor.name]
            ent = entities[inv.customer.name]
            expect, natural = [], []
            if spec.should_flag and spec.kind != "multi_invoice":
                natural.append("arith")
            if inv.kind == "credit_note":
                natural.append("doctype.credit_note")
            if not inv.po_number and inv.kind == "invoice":
                natural.append("match.no_po")
                expect.append("match.no_po")
            kind_planted = planted.get(ref)
            if kind_planted == "duplicate_resubmission":
                kind_planted = None  # this is the original; only its later resubmission is the duplicate
            if kind_planted:
                expect += PLANT_EXPECTS[kind_planted]
            if original:
                expect += PLANT_EXPECTS["duplicate_resubmission"]
            key = (inv.vendor.name, inv.number or f"?{ref}")
            dup_of = first_of_invoice.get(key)
            if dup_of and not original:
                natural.append("duplicate_copy")
                expect.append("dup.*")
            first_of_invoice.setdefault(key, ref)
            items.append({"index": k, "invoice_number": inv.number, "vendor_code": None if v.get("_remove") else v["code"],
                          "entity_code": None if ent.get("_remove") else ent["code"], "po_number": inv.po_number,
                          "total": inv.payable_total if inv.payable else inv.total,  # dual currency: the payable box
                          "currency": inv.payable.currency if inv.payable else inv.currency, "planted": kind_planted or ("duplicate_resubmission" if original else None),
                          "natural": natural, "expect": sorted(set(expect)),
                          "duplicate_of": original or dup_of,
                          # true dates and terms, for judging payment timing
                          "invoice_date": inv.issue_date.isoformat(),
                          "due_date": inv.due_date.isoformat() if inv.due_date else None, "terms": inv.terms})
        doc_expect = (["doctype.multiple_invoices"] if spec.kind == "multi_invoice" else
                      ["doctype.not_invoice"] if spec.kind == "statement" else [])
        labels.append({"seq": s["seq"], "ref": ref, "kind": spec.kind, "expect": doc_expect, "invoices": items})

    # --- write ---------------------------------------------------------------------------------------------
    def clean(d: dict) -> dict:
        return {k: v for k, v in d.items() if not k.startswith("_")}
    out = {
        "companion/entities.json": [clean(e) for e in sorted(entities.values(), key=lambda e: e["code"]) if not e.get("_remove")],
        "companion/vendors.json": [clean(v) for v in sorted(vendors.values(), key=lambda v: v["code"]) if not v.get("_remove")],
        "companion/purchase_orders.json": [clean(p) for p in sorted(pos.values(), key=lambda p: p["po_number"])
                                           if not vendors_by_code(vendors).get(p["vendor_code"], {}).get("_remove")],
        "companion/vendor_history.json": {code: h for code, h in sorted(history.items())
                                          if not vendors_by_code(vendors).get(code, {}).get("_remove")},
        "inbox/stream.json": [{"seq": s["seq"], "ref": s["ref"], "path": s["path"], "received_date": s["received_date"]}
                              for s in stream],
        "labels/ap_labels.json": {"set": set_name, "companion_seed": seed if set_name == "dev" else
                                  "derived from HELDOUT_SEED", "planted": dict(sorted(planted.items())),
                                  "resubmissions": {x["ref"]: x["of"] for x in resubmissions}, "items": labels},
    }
    for rel, payload in out.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    counts = defaultdict(int)
    for kind in planted.values():
        counts[kind] += 1
    sealed = None
    if set_name == "heldout":
        from . import extended, heldout  # noqa: PLC0415
        extended.write_keys("heldout", corpus)
        sealed = heldout.seal_companion()
    return {"vendors": len(out["companion/vendors.json"]), "entities": len(out["companion/entities.json"]),
            "purchase_orders": len(out["companion/purchase_orders.json"]), "stream": len(stream),
            "planted": dict(counts), "resubmissions": len(resubmissions),
            **({"sealed_files": sealed} if sealed is not None else {})}


def vendors_by_code(vendors: dict[str, dict]) -> dict[str, dict]:
    return {v["code"]: v for v in vendors.values()}
