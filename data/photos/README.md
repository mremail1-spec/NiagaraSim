# Photos for the 3D city

Your own photos turn the drawn walls of the 3D city (`web/world`) into the real buildings. Each photo covers one wall.

## Adding a photo in the game (easiest)
1. Open the game, walk up to the wall and face it. The card shows the building id and the wall number.
2. Press **P** (or **Add photo of this wall**), choose the photo, and drag the four yellow corners onto the
   wall's corners: top-left, top-right, bottom-right, bottom-left.
3. If the photo shows only part of the wall's height (a shop front), set **Bottom** and **Top** in metres.
4. **Preview on wall**, then **Save photo**. The photo is shrunk to 2048 px and re-encoded in your browser first,
   which drops its GPS location and camera details. Saved photos show for everyone who opens the game.

Saving needs edit access to the game page. To bake saved photos into the repository, ask Claude to
"pull the game's wall photos": it lists the page's `facades` records, downloads each photo into
`data/photos/`, adds entries to `data/building_photos.json`, and runs `python3 -m nfprep.facades` (from `prep/`).

## Adding a photo by hand
Put the file in `data/photos/` and add an entry to `data/building_photos.json`:

```json
{"building": "1a2b3c4d5e", "wall": 3, "wall_mid": [43.08812, -79.13001], "file": "westlane_tims.jpg",
 "corners": [[0.12, 0.20], [0.88, 0.18], [0.90, 0.93], [0.10, 0.95]], "bottom_m": 0, "top_m": 4.5,
 "source": "owner", "taken": "2026-11-26"}
```

`wall_mid` (the wall's middle, latitude and longitude) lets the build find the wall again if the city outlines
are rebuilt and the wall numbers shift.

## Which photos may be used
- **Your own photos.** Yes.
- **Mapillary** street photos (CC BY-SA 4.0). Yes, with `"source": "mapillary"`, `"credit"` and `"image_id"`.
  The credit is shown in the game.
- **Google Street View.** No. Google's Maps Platform terms forbid making textures, 3D models or other content
  from Street View images, and this project uses Google data only to check the simulation (CLAUDE.md).
  Use Street View to see a building, then take your own photo.

## Full 3D models
A photogrammetry model (for example from Polycam, RealityScan or Meshroom, made from your own photos) can replace a
building completely: export glTF binary (`.glb`), put it in `data/models/`, and add an entry to
`data/building_models.json` with the building id, scale, rotation and offset.
