"""Legacy LlamaExtract-based document parser.

Retained during the rebuild of the document pipeline (classifier → parser →
extractor → …). The public surface is still re-exported from
``backend.tools.document_parser`` so existing callers (scripts, tests) keep
working without changes.

New code should prefer the composable tools (``document_classifier`` first).
"""
