"""Regression tests for source application and idempotent partial updates."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.models.entities import Claim, Document, Lot, Party, Trade
from src.models.enums import ClaimKind, PartyRole
from src.workers.ingest_worker import persist_trade_and_lot


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDb:
    def __init__(self, results):
        self.results = iter(results)
        self.added: list[object] = []

    async def execute(self, _statement):
        return FakeResult(next(self.results))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for index, value in enumerate(self.added, start=1):
            if getattr(value, "id", None) is None:
                value.id = index


@pytest.mark.asyncio
async def test_partial_card_does_not_clear_existing_values():
    trade = Trade(id=1, efrsb_url="https://source.example/lot/1")
    lot = Lot(
        id=2,
        trade_id=1,
        lot_no=1,
        title="Old title",
        description_text="Verified description",
        nominal_claimed=Decimal("1000000"),
        current_price=Decimal("10000"),
        is_receivable=True,
    )
    party = Party(id=3, role=PartyRole.DEBTOR.value, inn="7707083893")
    claim = Claim(
        id=4,
        lot_id=2,
        kind=ClaimKind.TRADE_AR.value,
        principal=Decimal("1000000"),
        debtor_party_id=3,
    )
    db = FakeDb([trade, lot, party, claim])

    result = await persist_trade_and_lot(
        {
            "efrsb_url": trade.efrsb_url,
            "lot_no": 1,
            "title": "Short list title",
            "start_price": Decimal("100000"),
            "debtor_inn": party.inn,
        },
        db,
    )

    assert result == (trade, lot)
    assert lot.description_text == "Verified description"
    assert lot.nominal_claimed == Decimal("1000000")
    assert lot.current_price == Decimal("10000")
    assert lot.is_receivable is True
    assert claim.principal == Decimal("1000000")


@pytest.mark.asyncio
async def test_new_card_persists_etp_metadata_and_document_links():
    db = FakeDb([None, None, None])

    trade, lot = await persist_trade_and_lot(
        {
            "efrsb_url": "https://source.example/lot/1",
            "lot_no": 1,
            "etp_url": "https://elektortorgi.ru/trade/42/lot/1",
            "trade_id_on_etp": "42",
            "documents": [
                {
                    "url": "https://elektortorgi.ru/files/contract.pdf",
                    "title": "Договор",
                    "kind": "договор",
                }
            ],
        },
        db,
    )

    assert trade.etp_url == "https://elektortorgi.ru/trade/42/lot/1"
    assert trade.trade_id_on_etp == "42"
    documents = [value for value in db.added if isinstance(value, Document)]
    assert len(documents) == 1
    assert documents[0].url.endswith("contract.pdf")
    assert documents[0].title == "Договор"
