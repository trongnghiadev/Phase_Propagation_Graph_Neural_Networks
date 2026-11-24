"use client";

/**
 * Component bản đồ Leaflet.
 *
 * - Nạp phía client (`dynamic(..., { ssr: false })`) vì Leaflet cần `window`.
 * - Tile nền: **OpenStreetMap** — dữ liệu OSM được cấp phép theo
 *   **Open Database License (ODbL)**. Bắt buộc ghi nguồn khi phát hành:
 *   "© tác giả OpenStreetMap (https://www.openstreetmap.org/copyright), ODbL".
 *   Xem `apps/web/README.md` mục "Giấy phép nguồn dữ liệu".
 */

import React, { useEffect, useMemo } from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";

import "leaflet/dist/leaflet.css";

export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  label?: string;
  color?: string;
  radius?: number;
  popup?: React.ReactNode;
  tooltip?: string;
}

export interface MapLine {
  id: string;
  points: Array<[number, number]>;
  color?: string;
  weight?: number;
  dashArray?: string;
  opacity?: number;
}

export interface MapCircle {
  id: string;
  lat: number;
  lon: number;
  radius: number;
  color?: string;
  fillOpacity?: number;
  weight?: number;
}

/** Tâm mặc định: vùng sông Đồng Nai – Sài Gòn – Vàm Cỏ. */
export const DEFAULT_CENTER: [number, number] = [10.78, 106.7];
export const DEFAULT_ZOOM = 9;

/** Ghi nguồn dữ liệu bản đồ nền (bắt buộc theo ODbL). */
export function Attribution() {
  return (
    <span>
      ©{" "}
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
        tác giả OpenStreetMap
      </a>{" "}
      (ODbL)
    </span>
  );
}

export default function LeafletMap({
  points = [],
  lines = [],
  circles = [],
  center = DEFAULT_CENTER,
  zoom = DEFAULT_ZOOM,
  onPointClick,
  fitToPoints = false,
  height = 460,
}: {
  points?: MapPoint[];
  lines?: MapLine[];
  circles?: MapCircle[];
  center?: [number, number];
  zoom?: number;
  onPointClick?: (point: MapPoint) => void;
  fitToPoints?: boolean;
  height?: number;
}) {
  return (
    <div style={{ height, width: "100%" }}>
      <MapContainer
        center={center}
        zoom={zoom}
        style={{ height: "100%", width: "100%" }}
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">tác giả OpenStreetMap</a> (ODbL)'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
        />
        {fitToPoints && <FitBounds points={points} lines={lines} />}
        {lines.map((l) => (
          <Polyline
            key={l.id}
            positions={l.points}
            pathOptions={{
              color: l.color ?? "#1565c0",
              weight: l.weight ?? 3,
              dashArray: l.dashArray,
              opacity: l.opacity ?? 0.8,
            }}
          />
        ))}
        {circles.map((c) => (
          <CircleMarker
            key={c.id}
            center={[c.lat, c.lon]}
            radius={c.radius}
            pathOptions={{
              color: c.color ?? "#1565c0",
              fillOpacity: c.fillOpacity ?? 0.2,
              weight: c.weight ?? 1,
            }}
          />
        ))}
        {points.map((p) => (
          <CircleMarker
            key={p.id}
            center={[p.lat, p.lon]}
            radius={p.radius ?? 7}
            eventHandlers={onPointClick ? { click: () => onPointClick(p) } : undefined}
            pathOptions={{
              color: "#ffffff",
              weight: 2,
              fillColor: p.color ?? "#1565c0",
              fillOpacity: 0.95,
            }}
          >
            {p.tooltip && <Tooltip>{p.tooltip}</Tooltip>}
            {p.popup && <Popup>{p.popup}</Popup>}
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}

/** Tự căn khung bản đồ theo các điểm/đường đang hiển thị. */
function FitBounds({ points, lines }: { points: MapPoint[]; lines: MapLine[] }) {
  const map = useMap();

  const bounds = useMemo(() => {
    const latlngs: Array<[number, number]> = [];
    points.forEach((p) => latlngs.push([p.lat, p.lon]));
    lines.forEach((l) => l.points.forEach((pt) => latlngs.push(pt)));
    return latlngs;
  }, [points, lines]);

  useEffect(() => {
    if (!bounds.length) return;
    map.fitBounds(bounds, { padding: [40, 40] });
  }, [map, bounds]);

  return null;
}
