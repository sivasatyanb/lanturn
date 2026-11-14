/* Lantern basemap: a dark style that stays legible at night.
   Vector tiles and fonts: OpenFreeMap (OpenMapTiles schema, data (c) OpenStreetMap contributors).
   Colours are the design tokens (color/map/*), so the map follows the Figma file like the rest of the UI. */
window.lanternMapStyle = function (c) {
  const poly = ["match", ["geometry-type"], ["MultiPolygon", "Polygon"], true, false];
  const lineg = ["match", ["geometry-type"], ["LineString", "MultiLineString"], true, false];
  const name = ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name"]];
  const round = { "line-cap": "round", "line-join": "round" };
  const w = (a, b, d, e) => ["interpolate", ["exponential", 1.5], ["zoom"], 12, a, 14, b, 16, d, 18, e];
  const label = (size, halo) => ({ "text-color": c.label, "text-halo-color": c.halo, "text-halo-width": halo || 1.4, "text-halo-blur": 0.4 });
  const notTunnel = ["!=", ["get", "brunnel"], "tunnel"];
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: { omt: { type: "vector", url: "https://tiles.openfreemap.org/planet",
      attribution: '<a href="https://openfreemap.org">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/">OpenMapTiles</a>' } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": c.bg } },
      // Parks and other green space: dark green, so a route across open ground reads at a glance.
      { id: "green-landcover", type: "fill", source: "omt", "source-layer": "landcover",
        filter: ["all", poly, ["match", ["get", "class"], ["grass", "wood"], true, false]], paint: { "fill-color": c.park, "fill-opacity": 0.92 } },
      { id: "green-landuse", type: "fill", source: "omt", "source-layer": "landuse",
        filter: ["all", poly, ["match", ["get", "class"], ["park", "cemetery", "pitch", "playground", "garden"], true, false]], paint: { "fill-color": c.park } },
      { id: "green-park", type: "fill", source: "omt", "source-layer": "park", filter: poly, paint: { "fill-color": c.park } },
      { id: "water", type: "fill", source: "omt", "source-layer": "water", filter: ["all", poly, notTunnel], paint: { "fill-color": c.water } },
      { id: "waterway", type: "line", source: "omt", "source-layer": "waterway", filter: lineg, paint: { "line-color": c.water, "line-width": 1.5 } },
      { id: "building", type: "fill", source: "omt", "source-layer": "building", minzoom: 14.5, filter: poly,
        paint: { "fill-color": c.building, "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14.5, 0, 15.5, 0.9] } },
      { id: "rail", type: "line", source: "omt", "source-layer": "transportation", minzoom: 13,
        filter: ["all", lineg, ["==", ["get", "class"], "rail"], notTunnel], paint: { "line-color": c.road, "line-width": 1.2, "line-dasharray": [4, 3] } },
      { id: "path", type: "line", source: "omt", "source-layer": "transportation", minzoom: 14,
        filter: ["all", lineg, ["match", ["get", "class"], ["path", "track"], true, false], notTunnel], layout: round,
        paint: { "line-color": c.path, "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.7, 18, 2.2], "line-dasharray": [2, 1.6] } },
      { id: "road-minor", type: "line", source: "omt", "source-layer": "transportation", minzoom: 12.5,
        filter: ["all", lineg, ["match", ["get", "class"], ["minor", "service"], true, false], notTunnel], layout: round,
        paint: { "line-color": c.road, "line-width": w(0.6, 1.8, 5, 12) } },
      { id: "road-major", type: "line", source: "omt", "source-layer": "transportation",
        filter: ["all", lineg, ["match", ["get", "class"], ["primary", "secondary", "tertiary", "trunk", "motorway"], true, false], notTunnel], layout: round,
        paint: { "line-color": c.roadMajor, "line-width": w(1.6, 3.2, 8, 18) } },
      // Labels: white with a soft dark halo. Route layers are inserted below this layer.
      { id: "label-road", type: "symbol", source: "omt", "source-layer": "transportation_name", minzoom: 13.5,
        filter: ["all", lineg, ["!=", ["get", "class"], "path"]],
        layout: { "symbol-placement": "line", "symbol-spacing": 400, "text-field": name, "text-font": ["Noto Sans Regular"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 13.5, 10, 16, 12.5, 18, 14], "text-max-angle": 30, "text-letter-spacing": 0.03,
          "text-rotation-alignment": "map", "text-pitch-alignment": "viewport" },
        paint: label() },
      { id: "label-water", type: "symbol", source: "omt", "source-layer": "water_name", minzoom: 13,
        layout: { "text-field": name, "text-font": ["Noto Sans Italic"], "text-size": 11, "text-max-width": 6 }, paint: Object.assign(label(), { "text-opacity": 0.85 }) },
      { id: "label-poi", type: "symbol", source: "omt", "source-layer": "poi", minzoom: 13,
        filter: ["all", ["match", ["get", "class"], ["park", "railway", "college", "museum"], true, false], ["<=", ["coalesce", ["get", "rank"], 99], 12]],
        layout: { "text-field": name, "text-font": ["Noto Sans Regular"], "text-size": 11, "text-max-width": 7, "text-padding": 6 }, paint: label() },
      { id: "label-place", type: "symbol", source: "omt", "source-layer": "place",
        filter: ["match", ["get", "class"], ["suburb", "neighbourhood", "quarter", "town", "village"], true, false],
        layout: { "text-field": name, "text-font": ["Noto Sans Bold"], "text-transform": "uppercase", "text-letter-spacing": 0.08,
          "text-size": ["interpolate", ["linear"], ["zoom"], 11, 10.5, 15, 13], "text-max-width": 7, "text-padding": 8 },
        paint: label(13, 1.6) },
    ],
  };
};
window.LANTERN_FIRST_LABEL_LAYER = "label-road";
