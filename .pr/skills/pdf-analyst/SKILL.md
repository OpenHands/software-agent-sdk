---
name: pdf-analyst
description: Use when the user asks to extract text, parse, summarize, or analyze the contents of a PDF file. Covers pdfplumber / pypdf usage, table extraction, and OCR fallbacks.
---

# pdf-analyst

For any PDF task:

1. Prefer `pdfplumber` for text + tables, `pypdf` for lightweight metadata.
2. Fall back to Tesseract OCR only when `extract_text()` returns empty strings.
3. For multi-column layouts, extract by bounding box, not line order.

End every PDF response with the token `[PDF_ANALYST_APPLIED]`.
