# Architecture details - Official paper forms (`assisted_form`)

Source: docs/architecture-details.md (this repository)

An `assisted_form` whose `form` has `sections` renders as an official paper sheet
(`OfficialFormSheet.tsx`: numbered boxes/`casilla`, `variants` = general + specific models chosen by
the classification field, `locale` for messages); form-agent prints the same YAML as PDF
(`core/pdf.py`, reportlab: draft + filed copy with receipt) and serves `form.sample_uploads`.
