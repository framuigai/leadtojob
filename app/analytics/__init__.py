"""
Scoring, association analysis, charts and report generation.

ONE FILE RULE: scoring.py, charts.py, reports.py. Nothing in this package
imports Flask — it stays unit-testable without an app context, which is how the
suppression and N/A arithmetic get pinned by fixtures rather than by rendering a
page and reading it back.
"""
