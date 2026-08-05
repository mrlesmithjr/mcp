"""Backend implementations for lawnops.log_store (issue #57).

Each backend implements read_table/append_row/update_row/delete_row against
the canonical row shapes defined in lawnops.log_schema. Do not import these
directly -- go through lawnops.log_store, which selects the configured
backend.
"""
