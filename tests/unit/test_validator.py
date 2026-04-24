"""End-to-end unit tests for :class:`OrderValidator` against the seeded
fake repo. These are the scenarios the demo script will exercise."""

from __future__ import annotations
from unittest.mock import AsyncMock, patch

import pytest

from backend.models.parsed_document import ExtractedOrder, OrderLineItem
from backend.tools.order_validator import (
    MasterDataRepo,
    OrderValidator,
    RoutingDecision,
)


@pytest.fixture
def validator(seeded_repo: MasterDataRepo) -> OrderValidator:
    return OrderValidator(seeded_repo)


@pytest.mark.asyncio
async def test_clean_single_line_auto_approves(validator: OrderValidator) -> None:
    order = ExtractedOrder(
        customer_name="Patterson Industrial",
        line_items=[
            OrderLineItem(sku="FST-HCS-050-13-200-G5Z", quantity=50, unit_price=0.34),
        ],
    )
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert r.decision is RoutingDecision.AUTO_APPROVE
    assert r.aggregate_confidence == 1.0
    assert r.customer is not None
    assert r.customer.customer_id == "CUST-00042"
    assert len(r.lines) == 1
    assert r.lines[0].match_tier == "exact"


@pytest.mark.asyncio
async def test_alias_translation_still_auto_approves(validator: OrderValidator) -> None:
    order = ExtractedOrder(
        customer_name="Patterson Industrial",
        line_items=[OrderLineItem(sku="887712", quantity=50, unit_price=0.34)],
    )
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert r.decision is RoutingDecision.AUTO_APPROVE
    assert r.lines[0].matched_sku == "FST-HCS-050-13-200-G5Z"
    # Alias note captured for the dashboard.
    assert any("alias" in n for n in r.lines[0].notes)


@pytest.mark.asyncio
async def test_price_failure_routes_to_clarify(validator: OrderValidator) -> None:
    order = ExtractedOrder(
        customer_name="Patterson Industrial",
        line_items=[
            OrderLineItem(sku="FST-HCS-050-13-200-G5Z", quantity=50, unit_price=0.50),
        ],
    )
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert r.decision is RoutingDecision.CLARIFY
    assert r.lines[0].price_ok is False
    assert abs(r.aggregate_confidence - 0.85) < 1e-9


@pytest.mark.asyncio
async def test_unknown_customer_forces_escalate(validator: OrderValidator) -> None:
    order = ExtractedOrder(
        customer_name="Unknown Widget Corp",
        line_items=[
            OrderLineItem(sku="FST-HCS-050-13-200-G5Z", quantity=50, unit_price=0.34),
        ],
    )
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert r.decision is RoutingDecision.ESCALATE
    assert r.customer is None
    # Lines themselves could be clean, but the customer override wins.
    assert r.lines[0].match_tier == "exact"


@pytest.mark.asyncio
async def test_unmatched_line_escalates(validator: OrderValidator) -> None:
    order = ExtractedOrder(
        customer_name="Patterson Industrial",
        line_items=[
            OrderLineItem(sku="bogus-9999", description="nonsense", quantity=50),
        ],
    )
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert r.decision is RoutingDecision.ESCALATE
    assert r.aggregate_confidence == 0.0
    assert r.lines[0].matched_sku is None
    assert r.lines[0].match_tier == "none"


@pytest.mark.asyncio
async def test_mixed_lines_one_price_failure(validator: OrderValidator) -> None:
    """Three clean matches, one price failure → mean 1.0 – 0.15 = 0.85 → CLARIFY."""
    order = ExtractedOrder(
        customer_name="Patterson Industrial",
        line_items=[
            OrderLineItem(sku="FST-HCS-050-13-200-G5Z", quantity=50, unit_price=0.34),
            OrderLineItem(sku="FST-HCS-038-16-100-G8YZ", quantity=100, unit_price=0.41),
            OrderLineItem(sku="FST-HCS-050-13-150-S18", quantity=25, unit_price=2.00),
        ],
    )
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert r.decision is RoutingDecision.CLARIFY
    assert abs(r.aggregate_confidence - 0.85) < 1e-9
    # The third line is the one with the bad price.
    assert r.lines[2].price_ok is False


@pytest.mark.asyncio
async def test_empty_line_items_escalates(validator: OrderValidator) -> None:
    order = ExtractedOrder(customer_name="Patterson Industrial")
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert r.decision is RoutingDecision.ESCALATE
    assert r.aggregate_confidence == 0.0
    assert len(r.lines) == 0


@pytest.mark.asyncio
async def test_rationale_mentions_customer_and_decision(validator: OrderValidator) -> None:
    order = ExtractedOrder(
        customer_name="Patterson Industrial",
        line_items=[OrderLineItem(sku="FST-HCS-050-13-200-G5Z", quantity=50)],
    )
    r = await validator.validate(order, source_message_id="test-msg-1")
    assert "Patterson" in r.rationale
    assert r.decision.value in r.rationale


@pytest.mark.asyncio
class TestValidatorDuplicatePreflight:
    async def test_short_circuits_on_duplicate_hit(self, validator: OrderValidator) -> None:
        """When find_duplicate returns an id, validator returns ESCALATE
        with confidence=1.0 and rationale='duplicate of <id>', and does
        NOT call sku_matcher / price_check / qty_check."""
        order = ExtractedOrder(
            customer_name="Patterson Industrial",
            po_number="PO-123",
            line_items=[OrderLineItem(sku="FST-HCS-050-13-200-G5Z", quantity=5.0)],
        )

        with (
            patch(
                "backend.tools.order_validator.validator.find_duplicate",
                new_callable=AsyncMock,
                return_value="ORD-existing-xyz",
            ) as mock_find_dup,
            patch(
                "backend.tools.order_validator.validator.match_sku",
                new_callable=AsyncMock,
            ) as mock_sku,
        ):
            result = await validator.validate(
                order, source_message_id="msg-current"
            )

            assert result.decision == RoutingDecision.ESCALATE
            assert result.aggregate_confidence == 1.0
            assert "ORD-existing-xyz" in result.rationale
            assert "duplicate" in result.rationale.lower()
            assert result.lines == []
            mock_find_dup.assert_awaited_once()
            mock_sku.assert_not_awaited()

    async def test_proceeds_past_preflight_when_no_dup(self, validator: OrderValidator) -> None:
        """When find_duplicate returns None, validator runs full ladder."""
        order = ExtractedOrder(
            customer_name="Patterson Industrial",
            po_number="PO-fresh",
            line_items=[OrderLineItem(sku="FST-HCS-050-13-200-G5Z", quantity=5.0)],
        )

        with patch(
            "backend.tools.order_validator.validator.find_duplicate",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_find_dup:
            result = await validator.validate(
                order, source_message_id="msg-current"
            )

            # decision is whatever the ladder produces — the important thing
            # is that dup check did NOT short-circuit
            mock_find_dup.assert_awaited_once()
            assert result.decision in (
                RoutingDecision.AUTO_APPROVE,
                RoutingDecision.CLARIFY,
                RoutingDecision.ESCALATE,
            )
            # Lines were populated (proof the ladder ran)
            assert len(result.lines) == 1
