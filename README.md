# cad2print

Pick a part in Onshape, slice it with OrcaSlicer for a Bambu Lab H2S, and hand the sliced
plate to Bambu Connect, where the Print click stays yours.

```
browser ─► cad2print (FastAPI + htmx) ─► Onshape API (STL export)
                 │                       └► orca-slicer CLI (system H2S profiles)
                 └► handoff dir ══ SMB ══► bambu-win: Watch-Prints.ps1 ─► Bambu Connect
```

## Run locally

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # add your Onshape API keys
pytest                          # fast suite
mkdir -p data/handoff
uvicorn cad2print.app:factory --factory --env-file .env --reload --port 8080
```

Without an `orca-slicer` binary the app still browses Onshape; slicing jobs fail at the
"Slicing" stage with the launch error shown. Use the Docker image for real slices:

```sh
docker compose up --build       # http://localhost:8080, plates in ./data/handoff
docker compose exec cad2print /opt/venv/bin/pytest -m slow tests/integration
```

## Presets

`profiles.yaml` maps each preset to OrcaSlicer machine/process/filament JSONs under the image's
`/opt/orcaslicer/resources/profiles/BBL`. Add a preset by adding a key; the UI lists them in
file order. Per-job overrides are infill %, supports on/off and plate copies.

## Production

Deployed from the `infra` repo (`stacks/apps/cad2print`, `deploy-cad2print.yml`) on the
`orcaslicer` LXC, with the prints share bind-mounted at `/data/handoff`. See
`docs/spike-cli.md` for the CLI findings the slicer step is built on.
