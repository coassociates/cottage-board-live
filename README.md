# cottage-board-live

One file, `live.json`, rewritten every five minutes by the GitHub Actions job in this repo
and read by The Cottage's two counter boards (https://www.coassociates.co.uk/board/1/ and /2/).

It carries two things and nothing else:

- **Counter wait times** from the Square kitchen display: the median time from a ticket appearing
  on the **Counter Expeditor** screen to the expeditor bumping it, for the 30 minutes up to the
  latest bump seen (`as_of`) and for today so far, plus the same per half hour. Read from Square's
  Reporting API (the `KDS` view over the `KitchenPerformanceTickets` cube), which runs up to
  about 15 minutes behind the screen. No order counts, no sales, no portions. `sample` lets the
  board hide the panel when there are too few tickets to say anything, and the "now" figure is
  withheld once the latest bump is over 45 minutes old.
- **Blackpool weather**, chosen from forecast codes by `live_poller.py`.

Only this repo's owners and the Actions job can write here. The boards only read the file,
and format every figure themselves.

Configuration lives in the repo's Actions settings:

| Secret / variable | Purpose |
|---|---|
| `SQUARE_ACCESS_TOKEN` (secret) | Square production token with `REPORTING_READ` |
| `MET_OFFICE_API_KEY` (secret) | Met Office DataHub site-specific key |
| `SQUARE_LOCATION_ID` (variable, optional) | Defaults to the takeaway |
| `WEATHER_PROVIDER` (variable, optional) | `metoffice` (default) or `open-meteo` |
| `COUNTER_EXCLUDE_SOURCES` (variable, optional) | Comma-separated order sources to ignore |
| `KDS_STATION_NAME` (variable, optional) | KDS station whose bump is the handover. Defaults to `Counter Expeditor` |

The source of `live_poller.py` is maintained in
`OneDrive/Cottage F&C/Menu Display Boards/2026 October/build/`; copy it here when it changes.
