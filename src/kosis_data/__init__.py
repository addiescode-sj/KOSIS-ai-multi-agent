"""Data access for the agent tools: one provided table as a tidy DataFrame (T3-17, docs/data-contract.md)."""

from kosis_data.fetch import TableTooLarge, TidyTable, TidyTableMeta, fetch_table

__all__ = ["TableTooLarge", "TidyTable", "TidyTableMeta", "fetch_table"]
