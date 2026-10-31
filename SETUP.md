# Setting up cottage-board-live (one-off, about five minutes)

The boards read `live.json` from this repo. It must be **public** so the screens can fetch it
without a token. Only you and the Actions job can write to it.

1. Create the repo and push these files. From this folder, in a terminal:

       cd "OneDrive - Chris O'Connor & Associates Ltd\Cottage F&C\Menu Display Boards\2026 October\live-repo"
       git init -b main
       git add -A
       git commit -m "Counter wait times and Blackpool weather for the boards"
       gh repo create coassociates/cottage-board-live --public --source . --push

2. Add the two secrets (GitHub → repo → Settings → Secrets and variables → Actions → New repository secret),
   or from the same terminal:

       gh secret set SQUARE_ACCESS_TOKEN -R coassociates/cottage-board-live
       gh secret set MET_OFFICE_API_KEY  -R coassociates/cottage-board-live

   - Square: developer.squareup.com → your application → Production → Access token.
     It needs the `REPORTING_READ` permission (a personal access token has it). Nothing else.
   - Met Office: datahub.metoffice.gov.uk → sign up (free) → Site Specific forecast → API key.

3. Run it once by hand and check the file:

       gh workflow run update.yml -R coassociates/cottage-board-live
       # a minute later:
       curl https://raw.githubusercontent.com/coassociates/cottage-board-live/main/live.json

   From then on it runs every five minutes, 09:00–23:00 UTC, on GitHub's machines.

4. Check the station tickets the poller sees, without publishing anything: GitHub → Actions →
   "Inspect KDS tickets" → Run workflow. The log lists today's Counter Expeditor tickets
   (shown and bumped times only) and the counter block it would write. If the station is
   renamed in Square, set the repo variable `KDS_STATION_NAME` to match.
