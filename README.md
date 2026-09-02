# SITS Basemap WMS

This repository builds Sentinel-2 basemap layers with the [FORCE Time Series Framework](https://force-eo.readthedocs.io/en/latest/index.html).

The workflow has two goals:
- extract cloud-robust Sentinel-2 composites with FORCE
- convert those raw outputs into RGB and CIR GeoTIFFs that are easier to use as WMS basemap layers

The repository is focused on batch production. Instead of running one script to prepare FORCE and another script to clean and export the images tile by tile, the main workflow now runs from a single entry point.

## What The Repository Does

The processing chain is:

1. prepare a FORCE project for one or more AOIs
2. create the required mask, tile extent, and parameter files
3. run a UDF or standard TSA workflow in FORCE
4. collect the raw FORCE output tiles
5. clip each intersecting FORCE tile to the AOI and write compressed raw tile outputs
6. apply a shared radiometric stretch across the clipped tiles
7. export WMS-ready products such as RGB or CIR
8. build a GDAL VRT mosaic and optionally a final GeoTIFF mosaic

For the default UDF workflow, the repository uses `utils/skel/udf_rgb_p25_least_cloudy_block.py`. That UDF keeps the least cloudy observations and writes percentile-based RED, GREEN, BLUE, NIR, and valid-scene outputs that are later converted to display-ready imagery. Production uses the pinned FORCE 3.9.02 container image.

## Main Files

- `force_wms.py`: main entry point for prepare, run, render, and mosaic steps
- `run_production.py`: resumable one-command runner for versioned production presets
- `configs/germany_dop10_2025.json`: exact Germany 2025 FORCE, RGB, CIR, and storage settings
- `create_stretch_tests.py` and `create_cir_stretch_tests.py`: representative-tile color calibration utilities
- `build_wms_rgb.py`: compatibility wrapper that forwards to `force_wms.py`
- `utils/force_class_utils.py`: creates FORCE project structure and parameter files
- `utils/force_resume.py`: validates FORCE tiles and creates safe missing-tile resume jobs
- `utils/wms_rgb.py`: renders FORCE output tiles into WMS-ready RGB/CIR GeoTIFFs and mosaics
- `utils/skel/udf_rgb_p25_least_cloudy_block.py`: default UDF for cloud-robust basemap composites

## Requirements

The code is designed for Ubuntu-style environments and expects:

- Python 3.9
- FORCE 3.9.02-dev or a compatible Docker image
- Docker access for running FORCE commands
- `gdal-bin` for clipping, VRT build, and final GeoTIFF export
- `xterm` for the current execution flow

Setup example:

```bash
conda create --name SITSclass python==3.9
conda activate SITSclass
cd /path/to/SITS_basemap_wms
pip install -r requirements.txt
sudo apt-get install xterm gdal-bin
```

If FORCE is not installed locally, use the official Docker workflow:
- https://force-eo.readthedocs.io/en/latest/setup/docker.html#docker

## Repository Structure

The workflow uses the FORCE-style process directory layout under the chosen `base_path`:

- `process/temp/<project_name>/FORCE/<aoi>/`
- `process/temp/_mask/<project_name>/<aoi>/`
- `process/results/`
- `process/data/`

The helper image below illustrates the expected structure:

<img src="img/folderstructure.png">

## Running The Workflow

### Germany 2025 production preset

The finalized Germany RGB and CIR products can be reproduced or resumed with one command from the
repository root. This form does not require activating the Conda environment first:

```bash
conda run --no-capture-output -n SITSclass python run_production.py
```

The runner reads `configs/germany_dop10_2025.json`, which records the project name, AOI, 2025 date
range, FORCE 3.9.02 image, 3000 m chunks, RGB winner settings, CIR winner settings, ZSTD compression,
BigTIFF mode, and overview settings. Keep this config under Git version control with the code.

Repeating the same command is safe. The runner:

1. checks that an existing FORCE job matches the versioned preset
2. fully validates and resumes missing or corrupt FORCE tiles
3. recognizes completed RGB or CIR products from their reports and source timestamps
4. rebuilds only stale or incomplete products
5. writes tiles, VRTs, reports, and final mosaics through temporary files before replacing targets
6. records runtime output under the ignored `logs/` directory

Preview what would run without changing data:

```bash
conda run --no-capture-output -n SITSclass python run_production.py --dry-run
```

Force both display products to be rendered again while retaining the resumable FORCE data:

```bash
conda run --no-capture-output -n SITSclass python run_production.py --rebuild-products
```

Use `--metadata-validation` only when a faster FORCE startup check is more important than decoding
every raster block. The default full validation is safer after a crash.

To create a new year or AOI, copy the JSON preset, change `project_name`, `aoi`, and `date_range`, then
pass it with `--config`. Never reuse a project name after changing FORCE/UDF settings because that
would mix incompatible raw outputs.

### General command-line workflow

The main script is `force_wms.py`.

Example:

```bash
python force_wms.py \
  --project-name zGermany_full_tiles \
  --aoi "/rvt_mount/3DTests/data/harm_data/shp_germany_border.shp" \
  --date-range 2024-01-01 2025-12-31 \
  --workflow udf \
  --udf-source utils/skel/udf_rgb_p25_least_cloudy_block.py \
  --product rgb \
  --product cir
```

This command:
- resolves the AOI path or glob
- prepares FORCE parameter files
- runs FORCE for each AOI
- finds all raw output tiles in `tiles_tss`
- clips intersecting raw tiles to the AOI
- exports WMS-ready `rgb` and `cir` tile products
- writes VRT mosaics and can optionally materialize final GeoTIFF mosaics

## Useful Options

- `--skip-prepare`: reuse existing FORCE parameter files
- `--skip-force`: skip FORCE execution and only render from existing raw tiles
- `--resume-force`: validate and reuse complete FORCE tiles, processing only missing/corrupt tiles (enabled by default)
- `--force-validation full|metadata`: select full block decoding (default) or faster metadata-only validation
- `--force-resume-batch-size TILES`: set tiles per durable checkpoint; default `1` gives the strongest recovery
- `--no-resume-force`: bypass recovery checks and execute the original FORCE parameter file
- `--skip-render`: stop after FORCE preparation and execution
- `--skip-clip`: reuse already clipped raw FORCE tiles from a previous run
- `--render-raw`: render directly from FORCE's already-masked output and avoid writing a second raw-tile dataset
- `--skip-vrt`: export tile products without building the VRT mosaic
- `--skip-final-raster`: keep the VRT and tile products but do not materialize the final GeoTIFF mosaic
- `--overwrite-tiles`: rebuild clipped and rendered tile outputs
- `--overwrite-clipped-tiles`: rebuild only the clipped raw tiles
- `--overwrite-rendered-tiles`: rebuild only the display-product tiles
- `--no-overviews`: disable overview creation on rendered outputs
- `--workflow tsa`: use the standard TSA parameter generation instead of the UDF workflow
- `--min-valid-scenes`: require a minimum number of valid scenes per pixel
- `--low-pct` and `--high-pct`: control the global stretch used for WMS export
- `--gamma`: brighten shadows and midtones after stretching; values above `1` are brighter
- `--saturation`: control color intensity; `0` is grayscale and `1` preserves the original saturation
- `--rgb-gains RED GREEN BLUE`: apply per-channel multipliers for color or white-balance correction
- `--neutral-protection`: fade RGB gains on low-chroma pixels to prevent gray urban surfaces from acquiring a color cast
- `--green-suppression`: selectively reduce overly bright green-dominant pixels without changing neutral surfaces
- `--product rgb` or `--product cir`: choose the rendered display products
- `--compression-method`, `--bigtiff`, `--zlevel`, `--blocksize`: control GeoTIFF storage for large runs
- `--num-threads` and `--cachemax-mb`: tune GDAL clipping performance
- `--chunk-size X Y`: set the FORCE chunk size in CRS units (metres for EPSG:3035), not pixels; default `1000 1000`
- `--date-range START END`: explicitly select the input period; required when preparing a job
- `--sensors` and `--target-sensor`: explicitly select the FORCE sensors; defaults to Sentinel-2 and `SEN2L`
- `--force-image`: select one FORCE container image; default `davidfrantz/force:3.9.02`
- `--above-noise` and `--below-noise`: opt into FORCE temporal noise filtering; both default to `0`
- `--no-tile-allowlist`: disable the exact AOI tile allow-list and process the rectangular tile range
- `--tile-overviews`: build per-tile overviews in addition to final-mosaic overviews

## Resuming An Interrupted FORCE Run

Tile-level resume is enabled by default. On every FORCE execution the workflow:

1. reads the original `tile_extent.txt` allow-list
2. fingerprints the FORCE parameters, container image, copied UDF, data-cube definition, tile list, and raw suffix
3. opens every existing output and, by default, decodes every raster block
4. preserves corrupt outputs under `resume_quarantine/`
5. writes `tile_extent.resume.txt` containing only the current checkpoint batch
6. records the active batch durably and runs FORCE with a generated `*.resume.prm`
7. validates and commits each successful batch before starting the next one
8. validates all required outputs before starting rendering

The original parameter and tile-extent files are never changed. Progress is recorded atomically in
`force_resume_state.json`. With the default one-tile batch, a crash can invalidate at most the active
tile: any output from that tile is preserved under `resume_quarantine/` and regenerated on the next run.
When adopting an older job that has no checkpoint state yet, the newest existing tile is also retried
once because it is the tile most likely to have been active when the old run stopped.

To resume FORCE and then continue rendering, repeat the original command. Preparation can be skipped:

```bash
python force_wms.py \
  --project-name zGermany_full_tiles \
  --aoi "/path/to/germany.shp" \
  --skip-prepare \
  --render-raw \
  --product rgb \
  --product cir
```

For a faster but less thorough startup scan, add `--force-validation metadata`. If parameters, UDF
content, container image, or related inputs changed after outputs were created, resume stops rather
than mixing incompatible tiles. Use a new project name (recommended) or archive the old job first.

Increasing `--force-resume-batch-size` reduces Docker/FORCE startup overhead, but all tiles in the
active batch are conservatively retried after an interruption. Keep the default `1` for long production
runs where minimizing repeated work is more important than process startup overhead.

`--skip-force` also validates that every required FORCE tile exists and is readable before rendering.
If a tile is incomplete, remove `--skip-force` and rerun so that only unfinished tiles are processed.
Automatic resume requires the normal tile allow-list and is intentionally incompatible with
`--no-tile-allowlist`; use `--no-resume-force` only when the legacy full-extent behavior is required.

## Germany-Scale Performance

`CHUNK_SIZE` is measured in the data cube CRS, not in pixels. At 10 m resolution:

| FORCE chunk | Pixel dimensions | Calls per 30 km tile |
|---|---:|---:|
| `100 100` | 10 x 10 | 90,000 |
| `1000 1000` | 100 x 100 | 900 |
| `3000 3000` | 300 x 300 | 100 |

The default is `1000 1000`, which preserves the intended 100 x 100-pixel processing unit while reducing the number of Python UDF invocations by a factor of 100 compared with `100 100`.

The generated `tile_extent.txt` is used as FORCE's `FILE_TILE` allow-list, so tiles outside the AOI but inside its rectangular X/Y extent are not processed. The parameter writer validates that every requested tag exists in the pinned FORCE skeleton and stops instead of silently retaining a default.

Larger chunks can improve throughput further but require more RAM and change the spatial area used to rank the least-cloudy scenes. Validate a representative tile before changing the production default. Older FORCE data cubes may benefit from stripe-shaped chunks such as `30000 100` or `30000 1000`; benchmark these with separate output directories.

To inspect an existing generated job before running it:

```bash
grep -E '^(CHUNK_SIZE|RESOLUTION|FILE_TILE|NTHREAD|DATE_RANGE|SENSORS|TARGET_SENSOR|STREAMING)' \
  /rvt_mount/process/temp/PROJECT/FORCE/AOI/tsa_UDF.prm
```

Do not use `--skip-prepare` with an older job until confirming it does not still contain `CHUNK_SIZE = 100 100`.

Profile the UDF inside the pinned FORCE runtime rather than profiling the parent process that waits for Docker:

```bash
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$PWD:/workspace" \
  -w /workspace \
  davidfrantz/force:3.9.02 \
  python3 profile_udf.py --rows 100 --cols 100 --dates 100 --repeats 10

snakeviz udf_profile.prof
```

## Outputs

The rendered products are written under `process/results/<project_name>/` and can include:

- clipped raw FORCE tiles for resumable postprocessing
- WMS-ready RGB GeoTIFF tiles
- WMS-ready CIR GeoTIFF tiles
- project-wide VRT mosaics
- optional project-wide GeoTIFF mosaics
- JSON reports for clipping and rendering stages

The rendering step preserves an alpha band and valid-scene information so the outputs are easier to use in map services and downstream quality control.

## Notes

- AOI shapefiles are reprojected to EPSG:3035 when needed.
- FORCE paths and mount points are currently configured through CLI arguments such as `--base-path`, `--local-dir`, and `--force-dir`.
- Repository and AOI directories outside `--local-dir` are mounted read-only into preparation containers automatically.
- For Germany-scale production, the workflow writes both the VRT and the final GeoTIFF mosaic by default. Per-tile overviews are skipped when a final mosaic is built unless `--tile-overviews` is requested.
- Always inspect generated `.prm` files before launching large production runs.

## Versioning

This repository pins the `davidfrantz/force:3.9.02` container image by default.

## Authors

- [Sebastian Valencia](https://github.com/Azarozo19)

## License

This project is licensed under the GNU General Public License v3. See `LICENSE`.

## Acknowledgments

- FORCE Framework by David Frantz: https://force-eo.readthedocs.io/en/latest/index.html
- Time Series Classification work by Marc Russwurm: https://github.com/MarcCoru
