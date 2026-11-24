# -*- coding: utf-8 -*-
"""
build_light_grid.py - בונה את רשת עמודי התאורה הקומפקטית שהאפליקציה משתמשת בה (context_risk.py)
מקובצי העמודים שהופקו ב-israel_layer (תל אביב 52K, באר שבע 30K).

python -m research_tools.build_light_grid
-> models/street_lights_grid.json.gz (נשמר במחשב; לא במאגר עד שיאומת רישיון שכבת ה-GIS של תל אביב)
"""

import gzip
import json
import os

from research_tools.lighting_validation import DERIVED, LIGHT_FILES

import context_risk as CR


def main():
    import csv
    pts = []
    for f in LIGHT_FILES:
        p = os.path.join(DERIVED, f)
        if not os.path.exists(p):
            print("חסר:", p)
            continue
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                try:
                    pts.append((float(r["lat"]), float(r["lon"])))
                except (TypeError, ValueError, KeyError):
                    pass
    g = CR.LightGrid.build(pts)
    os.makedirs(os.path.dirname(CR.GRID_PATH), exist_ok=True)
    with gzip.open(CR.GRID_PATH, "wt", encoding="utf-8") as fh:
        json.dump(g.to_json(), fh, separators=(",", ":"))
    print(f"{len(pts)} עמודים -> {len(g.cells)} תאים, {len(g.cover)} אזורי כיסוי -> {CR.GRID_PATH} "
          f"({os.path.getsize(CR.GRID_PATH) // 1024}KB)")


if __name__ == "__main__":
    main()
