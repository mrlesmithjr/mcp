"""Backend implementations for homeops.log_store (issue #58).

Each backend implements read_table/append_row/update_row/delete_row against
the canonical row shapes defined in homeops.log_schema. Do not import these
directly -- go through homeops.log_store, which selects the configured
backend.
"""
