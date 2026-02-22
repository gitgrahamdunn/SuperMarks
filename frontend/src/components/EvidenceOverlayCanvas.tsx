import type { CSSProperties } from 'react';

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

export function EvidenceOverlayCanvas({
  imageUrl,
  evidence,
  visible,
  onImageError,
}: {
  imageUrl: string;
  evidence: EvidenceBox[];
  visible: boolean;
  onImageError?: () => void;
}) {
  return (
    <div style={{ position: 'relative' }}>
      <img src={imageUrl} alt="Key page" style={{ width: '100%', borderRadius: 8 }} onError={onImageError} />
      {visible && evidence.map((box, idx) => {
        const style: CSSProperties = {
          position: 'absolute',
          left: `${box.x * 100}%`,
          top: `${box.y * 100}%`,
          width: `${box.w * 100}%`,
          height: `${box.h * 100}%`,
          border: '2px solid rgba(15,23,42,0.6)',
          background: COLORS[box.kind] || 'rgba(99,102,241,0.35)',
          pointerEvents: 'none',
        };
        return <div key={`${box.kind}-${idx}`} style={style} title={`${box.kind} (${box.confidence.toFixed(2)})`} />;
      })}
    </div>
  );
}
