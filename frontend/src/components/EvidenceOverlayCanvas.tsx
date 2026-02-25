import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';

export interface EvidenceBox {
  page_number: number;
  x: number;
  y: number;
  w: number;
  h: number;
  kind: 'question_box' | 'answer_box' | 'marks_box' | string;
  confidence: number;
}

const COLORS: Record<string, string> = {
  question_box: 'rgba(59,130,246,0.35)',
  answer_box: 'rgba(16,185,129,0.35)',
  marks_box: 'rgba(245,158,11,0.35)',
};

interface EvidenceOverlayCanvasProps {
  imageUrl: string;
  evidence: EvidenceBox[];
  visible: boolean;
  pageNumber: number;
  onImageError?: () => void;
}

export function EvidenceOverlayCanvas({
  imageUrl,
  evidence,
  visible,
  pageNumber,
  onImageError,
}: EvidenceOverlayCanvasProps) {
  const imageRef = useRef<HTMLImageElement | null>(null);
  const [renderedSize, setRenderedSize] = useState({ width: 0, height: 0 });

  const updateRenderedSize = useCallback(() => {
    if (!imageRef.current) return;
    const rect = imageRef.current.getBoundingClientRect();
    setRenderedSize({
      width: Math.max(0, rect.width),
      height: Math.max(0, rect.height),
    });
  }, []);

  useEffect(() => {
    updateRenderedSize();
    window.addEventListener('resize', updateRenderedSize);
    window.addEventListener('orientationchange', updateRenderedSize);
    return () => {
      window.removeEventListener('resize', updateRenderedSize);
      window.removeEventListener('orientationchange', updateRenderedSize);
    };
  }, [updateRenderedSize]);

  const visibleBoxes = useMemo(
    () => (visible ? evidence.filter((box) => Number(box.page_number || 1) === pageNumber) : []),
    [evidence, visible, pageNumber],
  );

  const firstDebugBox = visibleBoxes[0];
  const firstDebugPx = firstDebugBox
    ? {
      left: Math.round(firstDebugBox.x * renderedSize.width),
      top: Math.round(firstDebugBox.y * renderedSize.height),
      width: Math.round(firstDebugBox.w * renderedSize.width),
      height: Math.round(firstDebugBox.h * renderedSize.height),
    }
    : null;

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div style={{ position: 'relative', display: 'inline-block', width: '100%' }}>
        <img
          ref={imageRef}
          src={imageUrl}
          alt="Key page"
          style={{ width: '100%', borderRadius: 8, display: 'block' }}
          onLoad={updateRenderedSize}
          onError={onImageError}
        />
        {visible && renderedSize.width > 0 && renderedSize.height > 0 && (
          <div
            aria-hidden
            style={{
              position: 'absolute',
              inset: 0,
              width: renderedSize.width,
              height: renderedSize.height,
              pointerEvents: 'none',
            }}
          >
            {visibleBoxes.map((box, idx) => {
              const style: CSSProperties = {
                position: 'absolute',
                left: `${box.x * renderedSize.width}px`,
                top: `${box.y * renderedSize.height}px`,
                width: `${box.w * renderedSize.width}px`,
                height: `${box.h * renderedSize.height}px`,
                border: '2px solid rgba(15,23,42,0.6)',
                background: COLORS[box.kind] || 'rgba(99,102,241,0.35)',
                pointerEvents: 'none',
                boxSizing: 'border-box',
              };
              return <div key={`${box.kind}-${idx}`} style={style} title={`${box.kind} (${box.confidence.toFixed(2)})`} />;
            })}
          </div>
        )}
      </div>

      <p className="subtle-text" style={{ margin: 0 }}>
        Overlay debug: {Math.round(renderedSize.width)} x {Math.round(renderedSize.height)} | boxes: {visibleBoxes.length}
        {firstDebugPx ? ` | first: (${firstDebugPx.left}, ${firstDebugPx.top}, ${firstDebugPx.width}, ${firstDebugPx.height})` : ''}
      </p>
    </div>
  );
}
