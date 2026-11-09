"""Write the data tools/desktop_check.mjs renders: GET /packs, each pack's images and films, read-only.

python tools/desktop_preview_data.py <packs folder> [<cron jobs.json>] > data.json
"""
import importlib.util
import json
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


def main(argv: list) -> int:
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    spec = importlib.util.spec_from_file_location("launch_desk_dashboard_api", API)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    root = Path(argv[1])
    listing = api.list_packs(root, Path(argv[2]) if len(argv) > 2 else None)
    images = {p["name"]: {"card": api.data_url(api.pack_image(root, p["name"], "card")),
                          "preview": api.data_url(api.pack_image(root, p["name"], "preview"))}
              for p in listing["packs"]}
    films = {p["name"]: api.film_data_url(api.pack_film(root, p["name"])) for p in listing["packs"]
             if (p.get("film") or {}).get("has_mp4")}
    json.dump({"packs": listing, "images": images, "films": films}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
