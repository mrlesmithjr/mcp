---
description: Sync YNAB data to local database
---

Sync the latest YNAB data:

1. Call `sync_data()` for a delta sync (fast, only changes since last sync)
2. Call `sync_status()` to confirm data freshness and show row counts

Report when the data was last synced and how many records were updated. If the user asks for a full sync, use `sync_data(full=true)`.
