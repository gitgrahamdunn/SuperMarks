import { useEffect, useMemo } from 'react';

type FileUploaderProps = {
  files: File[];
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (index: number) => void;
  disabled?: boolean;
};

function formatBytes(size: number): string {
  return `${(size / (1024 * 1024)).toFixed(2)} MB`;
}

export function FileUploader({ files, onAddFiles, onRemoveFile, disabled = false }: FileUploaderProps) {
  const previews = useMemo(
    () => files.map((file) => ({
      file,
      url: file.type.startsWith('image/') ? URL.createObjectURL(file) : '',
    })),
    [files],
  );

  useEffect(() => () => previews.forEach((item) => item.url && URL.revokeObjectURL(item.url)), [previews]);

  return (
    <div className="stack">
      <label htmlFor="exam-key-files" className="field-label">Key files (PDF or images)</label>
      <div
        className={`uploader-dropzone ${disabled ? 'is-disabled' : ''}`}
        onDragOver={(event) => {
          event.preventDefault();
        }}
        onDrop={(event) => {
          event.preventDefault();
          if (disabled) return;
          onAddFiles(Array.from(event.dataTransfer.files || []));
        }}
      >
        <p>Drag and drop files here</p>
        <p className="subtle-text">or</p>
        <label htmlFor="exam-key-files" className="button-secondary inline-button">Browse files</label>
        <input
          id="exam-key-files"
          type="file"
          accept="application/pdf,image/png,image/jpeg,image/jpg"
          onChange={(event) => {
            onAddFiles(Array.from(event.target.files || []));
            event.currentTarget.value = '';
          }}
          multiple
          disabled={disabled}
        />
      </div>

      {files.length > 0 && (
        <ul className="uploader-file-list" aria-label="Selected files">
          {previews.map((item, index) => (
            <li key={`${item.file.name}-${item.file.size}-${item.file.lastModified}`} className="uploader-file-item">
              <div className="uploader-file-preview">
                {item.url ? <img src={item.url} alt="Selected upload preview" className="uploader-thumb" /> : <span className="pdf-chip">PDF</span>}
              </div>
              <div>
                <strong>{item.file.name}</strong>
                <p className="subtle-text">{formatBytes(item.file.size)}</p>
              </div>
              <button
                type="button"
                className="button-danger"
                onClick={() => onRemoveFile(index)}
                disabled={disabled}
                aria-label={`Remove ${item.file.name}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
