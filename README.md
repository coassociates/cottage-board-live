# cottage-board-live

One file, `live.json`, rewritten every five minutes by the GitHub Actions job in this repo
and read by The Cottage's two counter boards (https://www.coassociates.co.uk/board/1/ and /2/).

It carries two things and nothing else:

- **Counter wait times** from Square: the median time from ordering to handover for counter
  orders in the last 30 minutes and for today so far, plus the same per half hour. No order
  counts, no sales, no portions. `sample` lets the board hide the panel when there are too few
  orders to say anything.
- **Blackpool weather**, chosen from forecast codes by `live_poller.py`.

Only this repo's owners and the Actions job can write here. The boards only read the file,
and format every figure themselves.

Configuration lives in the repo's Actions settings:

| Secret / variable | Purpose |
|---|---|
| `SQUARE_ACCESS_TOKEN` (secret) | Square production token with `ORDERS_READ` |
| `MET_OFFICE_API_KEY` (secret) | Met Office DataHub site-specific key |
| `SQUARE_LOCATION_ID` (variable, optional) | Defaults to the takeaway |
| `WEATHER_PROVIDER` (variable, optional) | `metoffice` (default) or `open-meteo` |
| `COUNTER_EXCLUDE_SOURCES` (variable, optional) | Comma-separated order sources to ignore |

The source of `live_poller.py` is maintained in
`OneDrive/Cottage F&C/Menu Display Boards/2026 October/build/`; copy it here when it changes.
