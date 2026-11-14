import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { TripStop } from "../api";

/** Карта командировки: точки адресов с номерами в порядке объезда, линия
 * между ещё не отработанными и моё место. Отдельный чанк (leaflet ~150 КБ) —
 * грузится, только когда карту открыли.
 *
 * Тайлы — OpenStreetMap; service worker складывает просмотренные в кэш
 * (vite.config.ts), так что район, который смотрели на связи, виден и без неё. */

const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const KIROV: L.LatLngTuple = [58.6035, 49.668];

export type MapPoint = { latitude: number; longitude: number };

const pin = (stop: TripStop, selected: boolean) =>
  L.divIcon({
    className: "",
    html: `<span class="trip-pin ${stop.status} ${selected ? "selected" : ""}">${stop.position}</span>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
  });

export default function TripMap({
  stops, selectedId, me, onSelect, onPlace,
}: {
  stops: TripStop[];
  selectedId?: number | null;
  me?: MapPoint | null;
  onSelect: (stop: TripStop) => void;
  /** Задано — карта ждёт касания, чтобы поставить точку адресу без координат. */
  onPlace?: ((point: MapPoint) => void) | null;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const meMarker = useRef<L.CircleMarker | null>(null);
  const fitted = useRef<string | null>(null);
  const handlers = useRef({ onSelect, onPlace });
  handlers.current = { onSelect, onPlace };

  useEffect(() => {
    if (!holder.current) return;
    const instance = L.map(holder.current, { zoomControl: true, attributionControl: true }).setView(KIROV, 10);
    instance.attributionControl.setPrefix(false);
    L.tileLayer(TILES, { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(instance);
    layer.current = L.layerGroup().addTo(instance);
    instance.on("click", (event: L.LeafletMouseEvent) => {
      handlers.current.onPlace?.({ latitude: event.latlng.lat, longitude: event.latlng.lng });
    });
    map.current = instance;
    // Карта живёт во вкладке, которая только что стала видимой, — размер надо пересчитать.
    const observer = new ResizeObserver(() => instance.invalidateSize());
    observer.observe(holder.current);
    return () => {
      observer.disconnect();
      instance.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    const group = layer.current;
    if (!instance || !group) return;
    group.clearLayers();
    const located = stops.filter((stop) => stop.latitude != null && stop.longitude != null);
    const route = located
      .filter((stop) => stop.status === "planned")
      .map((stop) => [stop.latitude, stop.longitude] as L.LatLngTuple);
    if (route.length > 1) {
      L.polyline(route, { color: "#007aff", weight: 3, opacity: 0.55, dashArray: "2 8", lineCap: "round" }).addTo(group);
    }
    for (const stop of located) {
      L.marker([stop.latitude!, stop.longitude!], {
        icon: pin(stop, stop.id === selectedId),
        title: stop.address,
        zIndexOffset: stop.id === selectedId ? 1000 : stop.status === "planned" ? 500 : 0,
      })
        .on("click", () => handlers.current.onSelect(stop))
        .addTo(group);
    }
    // Подгоняем вид, только когда набор точек изменился, — иначе карта прыгала бы при каждой отметке.
    const key = located.map((stop) => `${stop.id}:${stop.latitude}:${stop.longitude}`).join("|") || (me ? "me" : "");
    if (key !== fitted.current) {
      fitted.current = key;
      const points = located.map((stop) => [stop.latitude, stop.longitude] as L.LatLngTuple);
      if (points.length === 1) instance.setView(points[0], 15);
      else if (points.length > 1) instance.fitBounds(L.latLngBounds(points), { padding: [36, 36], maxZoom: 16 });
      else if (me) instance.setView([me.latitude, me.longitude], 13);
    }
  }, [stops, selectedId, me]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    if (!me) {
      meMarker.current?.remove();
      meMarker.current = null;
      return;
    }
    const at: L.LatLngTuple = [me.latitude, me.longitude];
    if (meMarker.current) meMarker.current.setLatLng(at);
    else {
      meMarker.current = L.circleMarker(at, {
        radius: 7, color: "#ffffff", weight: 3, fillColor: "#007aff", fillOpacity: 1, interactive: false,
      }).addTo(instance);
    }
  }, [me]);

  return <div ref={holder} className={`trip-map ${onPlace ? "placing" : ""}`} />;
}
