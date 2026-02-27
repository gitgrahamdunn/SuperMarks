import { DragEvent, useEffect, useMemo, useRef, useState } from 'react';

const ACCEPTED_TYPES = ['application/pdf', 'image/png', 'image/jpeg', 'image/jpg'];

interface FileUploaderProps {
  files: File[];
  disabled?: boolean;
  onChange: (files: File[]) => void;
  maxBytesPerFile: number;
  onReject: (message: string) => void;
}

function formatBytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function FileUploader({ files, disabled, onChange, maxBytesPerFile, onReject }: FileUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  const imagePreviews = useMemo(
    () =>
      files
        .filter((file) => file.type.startsWith('image/'))
        .map((file) => ({
          key: `${file.name}-${file.size}`,
          file,
          url: URL.createObjectURL(file),
        })),
    [files],
  );

  useEffect(
    () => () => {
      imagePreviews.forEach((preview) => URL.revokeObjectURL(preview.url));
    },
    [imagePreviews],
  );

  const addFiles = (incoming: FileList | File[]) => {
    const next = Array.from(incoming);
    const validFiles: File[] = [];

    for (const file of next) {
      if (!ACCEPTED_TYPES.includes(file.type)) {
        onReject(`${file.name} is not a supported file type.`);
        continue;
      }

      if (file.size > maxBytesPerFile) {
        onReject(`${file.name} exceeds the 8 MB per-file limit.`);
        continue;
      }

      validFiles.push(file);
    }

    if (validFiles.length > 0) {
      onChange([...files, ...validFiles]);
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    if (!disabled) {
      addFiles(event.dataTransfer.files);
    }
  };

  return (
    <div className="stack">
      <div
        className={`dropzone ${isDragging ? 'dropzone-active' : ''}`}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) {
            setIsDragging(true);
          }
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if ((event.key === 'Enter' || event.key === ' ') && !disabled) {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        onClick={() => !disabled && inputRef.current?.click()}
      >
        <p><strong>Drag & drop files</strong> or click to browse.</p>
        <p className="subtle-text">Accepted: PDF, PNG, JPG, JPEG</p>
        <input
          ref={inputRef}
          id="exam-key-files"
          name="exam-key-files"
          type="file"
          accept="application/pdf,image/png,image/jpeg,image/jpg"
          multiple
          disabled={disabled}
          onChange={(event) => addFiles(event.target.files || [])}
          className="visually-hidden"
        />
      </div>

      {files.length > 0 && (
        <div className="file-list-block">
          <strong>Selected files</strong>
          <ul className="file-list">
            {files.map((file) => {
              const isImage = file.type.startsWith('image/');
              const imagePreview = imagePreviews.find((preview) => preview.file === file);
              return (
                <li key={`${file.name}-${file.size}`} className="file-row">
                  <div className="file-row-meta">
                    {isImage && imagePreview ? (
                      <img src={imagePreview.url} alt={`${file.name} preview`} className="file-thumb" />
                    ) : (
                      <span className="file-chip">PDF</span>
                    )}
                    <span>{file.name} ({formatBytes(file.size)})</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={() => onChange(files.filter((existing) => existing !== file))}
                    disabled={disabled}
                    aria-label={`Remove ${file.name}`}
                  >
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
