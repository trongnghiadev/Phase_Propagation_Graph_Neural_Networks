"use client";

/**
 * Bản đồ Leaflet nạp phía client.
 *
 * Leaflet phụ thuộc `window`, nên không thể render trong SSR của Next.js —
 * bọc bằng `dynamic(..., { ssr: false })`.
 */

import dynamic from "next/dynamic";

import type { MapCircle, MapLine, MapPoint } from "./LeafletMap";

const LeafletMapInner = dynamic(() => import("./LeafletMap"), {
  ssr: false,
  loading: () => (
    <div
      style={{
        height: 460,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#eceff1",
        color: "#607d8b",
        borderRadius: 6,
      }}
    >
      Đang nạp bản đồ…
    </div>
  ),
});

export type { MapCircle, MapLine, MapPoint };
export { DEFAULT_CENTER, DEFAULT_ZOOM, Attribution } from "./LeafletMap";

export default function LeafletMap(props: {
  points?: MapPoint[];
  lines?: MapLine[];
  circles?: MapCircle[];
  center?: [number, number];
  zoom?: number;
  onPointClick?: (point: MapPoint) => void;
  fitToPoints?: boolean;
  height?: number;
}) {
  return <LeafletMapInner {...props} />;
}
