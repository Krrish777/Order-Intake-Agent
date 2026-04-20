"""LlamaClassify rules for the document_classifier tool.

The 8 ``type`` values mirror ``DocumentClassification`` in
``backend.models.parsed_document`` — keep them in lock-step.
"""

from __future__ import annotations

from typing import Final

from llama_cloud.types.classify_configuration_param import Rule

CLASSIFY_RULES: Final[list[Rule]] = [
    {
        "type": "purchase_order",
        "description": (
            "A customer's request to buy goods. Contains a PO number "
            "(labels: PO #, Purchase Order, Order #, Order Number, Ref #, "
            "Reference), a buyer / customer identity, and one or more line "
            "items with SKU or part number, quantity, and usually unit price "
            "and requested delivery / ship date. Not yet confirmed by the "
            "seller. Typical titles: 'Purchase Order', 'PO', 'Order'."
        ),
    },
    {
        "type": "po_confirmation",
        "description": (
            "A seller's acknowledgement of a previously-received purchase "
            "order. References the buyer's PO number AND a seller-side "
            "sales-order / order-acknowledgement / OA number. Often contains "
            "confirmed ship dates, confirmed prices, or partial-line "
            "confirmations with back-order notes. Typical titles: 'Order "
            "Acknowledgement', 'Sales Order Confirmation', 'OA', 'PO "
            "Confirmation'."
        ),
    },
    {
        "type": "shipping_notice",
        "description": (
            "An advance shipping notice (ASN) or dispatch confirmation sent "
            "by the seller after goods leave the dock. References a "
            "shipment / tracking / waybill / BOL / AWB number, a carrier, a "
            "ship date, and which PO line items are on the shipment. "
            "Typical titles: 'ASN', 'Shipping Notice', 'Dispatch "
            "Confirmation', 'Bill of Lading'."
        ),
    },
    {
        "type": "invoice",
        "description": (
            "A seller's request for payment. Contains an invoice number, "
            "invoice date, due date or payment terms, a bill-to party, "
            "line items with extended prices, tax lines, and a total amount "
            "due. May reference the original PO. Typical titles: 'Invoice', "
            "'Tax Invoice', 'Commercial Invoice', 'Bill'."
        ),
    },
    {
        "type": "inquiry",
        "description": (
            "A pre-sales or informational question — request for quote "
            "(RFQ), availability / stock check, lead-time question, catalog "
            "request, spec clarification, or general 'can you supply X?' "
            "outreach. Expresses interest but does NOT commit to buy and "
            "carries no PO number. Typical phrases: 'Can you quote', 'Do "
            "you have', 'Please advise lead time', 'Looking for'."
        ),
    },
    {
        "type": "complaint",
        "description": (
            "A post-sale issue report — damaged goods, short shipment, "
            "wrong item, late delivery, billing dispute, return request. "
            "References an existing PO / invoice / shipment number and "
            "asks for remediation (return, credit, replacement, refund). "
            "Typical phrases: 'received damaged', 'wrong part', 'missing', "
            "'not as ordered', 'please credit', 'RMA'."
        ),
    },
    {
        "type": "spam",
        "description": (
            "Unsolicited marketing, phishing, irrelevant bulk mail, or any "
            "message with no legitimate supply-chain purpose. Marketing "
            "newsletters, cold sales outreach from vendors we don't buy "
            "from, promotional offers, malicious links, fake invoices from "
            "unknown senders. No PO, no order context, no shipment context."
        ),
    },
    {
        "type": "other",
        "description": (
            "Legitimate supply-chain correspondence that does not fit any "
            "of the above categories — scheduling updates, demand "
            "forecasts, credit applications, vendor onboarding paperwork, "
            "certificates of analysis, general account administration, "
            "internal routing notes. Route to human review."
        ),
    },
]
