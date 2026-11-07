import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import type { MapConfig } from "../api";

export type MapMarker = { lat: number; lon: number; label: string; color: string; text?: string };
export type MapLine = { coords: [number, number][]; color: string };

/**
 * Карта на MapLibre с растровой подложкой из настроек (MAP_TILE_URL).
 * Отдельный чанк: поверителю на телефоне без вкладки «Выезды» он не нужен.
 */
export default function TripMap({ config, markers, lines = [], height = 420 }: {
  config: MapConfig;
  markers: MapMarker[];
  lines?: MapLine[];
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const placed = useRef<maplibregl.Marker[]>([]);

  useEffect(() => {
    if (!box.current) return;
    const instance = new maplibregl.Map({
      container: box.current,
      style: {
        version: 8,
        sources: {
          base: { type: "raster", tiles: [config.tile_url], tileSize: 256, attribution: config.attribution },
        },
        layers: [{ id: "base", type: "raster", source: "base" }],
      },
      center: config.depot ? [config.depot.lon, config.depot.lat] : [49.668, 58.6036],
      zoom: 9,
    });
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.current = instance;
    return () => {
      instance.remove();
      map.current = null;
    };
  }, [config]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    placed.current.forEach((marker) => marker.remove());
    placed.current = markers.map((item) => {
      const el = document.createElement("div");
      el.className = "map-pin";
      el.style.background = item.color;
      el.textContent = item.text ?? "";
      return new maplibregl.Marker({ element: el })
        .setLngLat([item.lon, item.lat])
        .setPopup(new maplibregl.Popup({ offset: 12 }).setText(item.label))
        .addTo(instance);
    });

    const draw = () => {
      // Убрать линии прошлого рендера, сколько бы их ни было.
      for (let index = 0; instance.getLayer(`line-${index}`); index += 1) {
        instance.removeLayer(`line-${index}`);
        instance.removeSource(`line-${index}`);
      }
      lines.forEach((line, index) => {
        instance.addSource(`line-${index}`, {
          type: "geojson",
          data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: line.coords } },
        });
        instance.addLayer({
          id: `line-${index}`, type: "line", source: `line-${index}`,
          paint: { "line-color": line.color, "line-width": 3, "line-opacity": 0.8 },
        });
      });
    };
    if (instance.isStyleLoaded()) draw();
    else instance.once("load", draw);

    const all: [number, number][] = [...markers.map((m) => [m.lon, m.lat] as [number, number]), ...lines.flatMap((l) => l.coords)];
    if (all.length > 1) {
      const bounds = all.reduce((acc, point) => acc.extend(point), new maplibregl.LngLatBounds(all[0], all[0]));
      instance.fitBounds(bounds, { padding: 40, maxZoom: 14, duration: 0 });
    } else if (all.length === 1) {
      instance.jumpTo({ center: all[0], zoom: 13 });
    }
  }, [markers, lines]);

  return <div ref={box} className="trip-map" style={{ height }} />;
}
