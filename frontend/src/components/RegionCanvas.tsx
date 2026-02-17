import { useMemo, useRef, useState } from 'react';
import type { Region } from '../types/api';

interface DraftRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface RegionCanvasProps {
  imageUrl: string;
  regions: Region[];
  onChange: (regions: Region[]) => void;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function RegionCanvas({ imageUrl, regions, onChange }: RegionCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [draft, setDraft] = useState<DraftRect | null>(null);

  const rects = useMemo(() => regions, [regions]);

  const getNormalizedPoint = (clientX: number, clientY: number) => {
    const bounds = containerRef.current?.getBoundingClientRect();
    if (!bounds) return null;
    const x = clamp((clientX - bounds.left) / bounds.width, 0, 1);
    const y = clamp((clientY - bounds.top) / bounds.height, 0, 1);
    return { x, y };
  };

  const onMouseDown = (event: React.MouseEvent) => {
    const point = getNormalizedPoint(event.clientX, event.clientY);
    if (!point) return;
    setStart(point);
    setDraft({ x: point.x, y: point.y, w: 0, h: 0 });
  };

  const onMouseMove = (event: React.MouseEvent) => {
    if (!start) return;
    const point = getNormalizedPoint(event.clientX, event.clientY);
    if (!point) return;
    const x = Math.min(start.x, point.x);
    const y = Math.min(start.y, point.y);
    const w = Math.abs(point.x - start.x);
    const h = Math.abs(point.y - start.y);
    setDraft({ x, y, w, h });
  };

  const onMouseUp = () => {
    if (draft && draft.w > 0.01 && draft.h > 0.01) {
      onChange([...regions, { page_number: 1, ...draft }]);
    }
    setStart(null);
    setDraft(null);
  };

  return (
    <div>
      <div
        ref={containerRef}
        className="region-canvas"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <img src={imageUrl} alt="Submission page" className="canvas-image" draggable={false} />
        {rects.map((region, idx) => (
          <div
            key={region.id ?? idx}
            className="region-rect"
            style={{
              left: `${region.x * 100}%`,
              top: `${region.y * 100}%`,
              width: `${region.w * 100}%`,
              height: `${region.h * 100}%`,
            }}
          >
            <button
              type="button"
              className="region-delete"
              onClick={(event) => {
                event.stopPropagation();
                onChange(regions.filter((_, regionIndex) => regionIndex !== idx));
              }}
            >
              ×
            </button>
          </div>
        ))}
        {draft && (
          <div
            className="region-rect draft"
            style={{
              left: `${draft.x * 100}%`,
              top: `${draft.y * 100}%`,
              width: `${draft.w * 100}%`,
              height: `${draft.h * 100}%`,
            }}
          />
        )}
      </div>
    </div>
  );
}
