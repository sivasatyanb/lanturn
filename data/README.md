# Data in this repository: sources and licences

The code in this repository is under the MIT License (see `LICENSE`). The data in this folder is licensed separately, as listed here.

| File | What it is | Licence and attribution |
|---|---|---|
| `processed/graph.json.gz` | The walking graph Lanturn routes on: OpenStreetMap ways in the demo area (South Kensington to Paddington), split at junctions, with the tags used for routing. Built by `scripts/setup_data.py`; the extract time is stored in the file (`meta.osm_base`). | **Derived from OpenStreetMap. © OpenStreetMap contributors.** Made available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). If you redistribute it or a database built from it, keep this notice and offer your version under the ODbL. See https://www.openstreetmap.org/copyright. |
| `fixtures/tfl_road_disruptions_capture.json` | One unmodified response from TfL's Unified API (`/Road/all/Disruption`), fetched 14 Nov 2026, used for the replay and the evaluation. | **Powered by TfL Open Data.** Contains OS data © Crown copyright and database rights 2016 and Geomni UK Map data © and database rights [2019]. Used under TfL's [Transport Data Service terms](https://tfl.gov.uk/corporate/terms-and-conditions/transport-data-service). Lanturn is not endorsed by TfL. |
| `fixtures/replay_scenario.json` | Eight **synthetic** records written by this project in TfL's style (ids `REPLAY-*`). They are not real disruptions. | Part of this project. |
| `fixtures/eval_labels.json` | This project's hand labels for the evaluation. | Part of this project. |

The raw OpenStreetMap extract is not stored here. `python scripts/setup_data.py` downloads it from the Overpass
API into `data/raw/` (git-ignored) and rebuilds the graph. Sources, coverage and what was deliberately not used
are in [docs/DATA.md](../docs/DATA.md).
