import { FormEvent, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { FileUploader } from '../components/FileUploader';
import { useToast } from '../components/ToastProvider';
import { compareStudentNamesByLastName, formatStudentName } from '../lib/nameFormat';
import type { ClassListRead } from '../types/api';

const CLASS_LIST_ACCEPTED_TYPES = [
  'application/pdf',
  'image/png',
  'image/jpeg',
  'image/jpg',
  'text/csv',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
];

const formatDate = (value?: string | null) => {
  if (!value) return 'Unknown date';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Unknown date';
  return parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
};

export function ClassListsPage() {
  const [classLists, setClassLists] = useState<ClassListRead[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [classListName, setClassListName] = useState('');
  const [activeClassListId, setActiveClassListId] = useState<number | null>(null);
  const [editorName, setEditorName] = useState('');
  const [editorNamesText, setEditorNamesText] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const { showError, showSuccess, showWarning } = useToast();

  const normalizeNamesForUi = (names: string[]) => names
    .map((name) => formatStudentName(name))
    .filter(Boolean)
    .sort(compareStudentNamesByLastName);

  const normalizeClassListForUi = (classList: ClassListRead): ClassListRead => {
    const names = normalizeNamesForUi(classList.names);
    return {
      ...classList,
      names,
      entry_count: names.length,
    };
  };

  const loadClassLists = async () => {
    try {
      setIsLoading(true);
      setClassLists((await api.getClassLists()).map(normalizeClassListForUi));
    } catch (error) {
      showError(error instanceof Error ? error.message : 'We couldn’t load your class lists.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadClassLists();
  }, []);

  const sortedClassLists = useMemo(
    () => [...classLists].sort((a, b) => new Date(b.created_at || '').getTime() - new Date(a.created_at || '').getTime()),
    [classLists],
  );

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (files.length === 0) {
      showWarning('Add at least one file to create a class list.');
      return;
    }
    try {
      setIsSaving(true);
      const created = normalizeClassListForUi(await api.createClassListFromUploads(files, classListName));
      setClassLists((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setFiles([]);
      setClassListName('');
      showSuccess(`${created.name || 'Class list'} is ready to use.`);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'We couldn’t save this class list.');
    } finally {
      setIsSaving(false);
    }
  };

  const onDelete = async (classList: ClassListRead) => {
    if (!classList.id) return;
    const confirmed = window.confirm(`Delete "${classList.name || 'this class list'}"? This action cannot be undone.`);
    if (!confirmed) return;
    try {
      setDeletingId(classList.id);
      await api.deleteClassList(classList.id);
      setClassLists((prev) => prev.filter((item) => item.id !== classList.id));
      setActiveClassListId((current) => (current === classList.id ? null : current));
      showSuccess(`${classList.name || 'Class list'} was deleted.`);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'We couldn’t delete this class list.');
    } finally {
      setDeletingId((current) => (current === classList.id ? null : current));
    }
  };

  const activeClassList = useMemo(
    () => sortedClassLists.find((classList) => classList.id === activeClassListId) ?? null,
    [activeClassListId, sortedClassLists],
  );

  const openClassList = (classList: ClassListRead) => {
    setActiveClassListId(classList.id ?? null);
    setEditorName(classList.name || '');
    setEditorNamesText(classList.names.join('\n'));
  };

  const closeEditor = () => {
    setActiveClassListId(null);
    setEditorName('');
    setEditorNamesText('');
  };

  const onSaveClassList = async () => {
    if (!activeClassList?.id) return;
    const normalizedNames = normalizeNamesForUi(
      editorNamesText
        .split('\n')
        .map((name) => name.trim())
        .filter(Boolean),
    );
    if (normalizedNames.length === 0) {
      showWarning('Add at least one student name before saving.');
      return;
    }
    try {
      setIsUpdating(true);
      const updated = normalizeClassListForUi(await api.updateClassList(activeClassList.id, {
        name: editorName.trim(),
        names: normalizedNames,
      }));
      setClassLists((prev) => prev.map((classList) => (classList.id === updated.id ? updated : classList)));
      setEditorName(updated.name || '');
      setEditorNamesText(updated.names.join('\n'));
      showSuccess(`${updated.name || 'Class list'} was updated.`);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'We couldn’t save this class list.');
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <div className="page-stack">
      <section className="card card--hero stack">
        <div className="page-header">
          <div>
            <h1 className="page-title">Class lists</h1>
            <p className="page-subtitle">Create reusable class lists for better name reads during test entry.</p>
          </div>
        </div>
      </section>

      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">New class list</h2>
            <p className="subtle-text">Drop in a photo (even a photo of a screen), PDF, or Excel file.</p>
          </div>
        </div>
        <form className="stack" onSubmit={onCreate}>
          <div className="stack" style={{ gap: '.6rem' }}>
            <label htmlFor="class-list-name">Class list name</label>
            <input
              id="class-list-name"
              value={classListName}
              onChange={(event) => setClassListName(event.target.value)}
              placeholder="Optional"
              disabled={isSaving}
            />
          </div>
          <FileUploader
            files={files}
            disabled={isSaving}
            onChange={setFiles}
            maxBytesPerFile={8 * 1024 * 1024}
            onReject={(message) => showWarning(message)}
            multiple
            singularLabel="class list file"
            acceptedTypes={CLASS_LIST_ACCEPTED_TYPES}
            acceptedLabel="PDF, PNG, JPG, JPEG, CSV, XLSX"
          />
          <div className="actions-row" style={{ marginTop: 0 }}>
            <button type="submit" className="btn btn-primary" disabled={isSaving || files.length === 0}>
              {isSaving ? 'Saving…' : 'Create class list'}
            </button>
          </div>
        </form>
      </section>

      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">Saved class lists</h2>
            <p className="subtle-text">Choose one from Home when you start a new exam.</p>
          </div>
          <span className="status-pill status-neutral">{sortedClassLists.length} list{sortedClassLists.length === 1 ? '' : 's'}</span>
        </div>

        {isLoading && <p className="subtle-text">Loading class lists…</p>}

        {!isLoading && sortedClassLists.length === 0 && (
          <div className="review-readonly-block">
            <strong>No class lists yet</strong>
            <p className="subtle-text" style={{ marginTop: '.35rem' }}>Create one here, or generate one later from confirmed student names.</p>
          </div>
        )}

        {!isLoading && sortedClassLists.length > 0 && (
          <div className="workspace-card-grid">
            {sortedClassLists.map((classList) => (
              <article key={classList.id ?? `${classList.name}-${classList.created_at}`} className="workspace-card">
                <div className="workspace-card-header">
                  <div>
                    <p className="workspace-card-kicker">Class list</p>
                    <strong className="workspace-card-title" style={{ display: 'inline-block' }}>{classList.name || 'Untitled class list'}</strong>
                  </div>
                  <span className="status-pill status-neutral">
                    {classList.entry_count} name{classList.entry_count === 1 ? '' : 's'}
                  </span>
                </div>
                <div className="workspace-card-meta">
                  <span>Created {formatDate(classList.created_at)}</span>
                  <span>{classList.source === 'confirmed_names' ? 'Built from checked test' : 'Uploaded file(s)'}</span>
                </div>
                {classList.filenames.length > 0 && (
                  <p className="subtle-text" style={{ margin: 0 }}>{classList.filenames.join(', ')}</p>
                )}
                <p className="subtle-text" style={{ margin: 0 }}>
                  {classList.names.slice(0, 3).join(', ')}{classList.entry_count > 3 ? ` +${classList.entry_count - 3} more` : ''}
                </p>
                <div className="actions-row" style={{ marginTop: 0 }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => openClassList(classList)}
                  >
                    {activeClassListId === classList.id ? 'Editing' : 'Open'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={() => void onDelete(classList)}
                    disabled={deletingId === classList.id}
                  >
                    {deletingId === classList.id ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {activeClassList && (
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Edit class list</h2>
              <p className="subtle-text">Names are shown alphabetically and saved in that order.</p>
            </div>
            <span className="status-pill status-neutral">
              {activeClassList.entry_count} name{activeClassList.entry_count === 1 ? '' : 's'}
            </span>
          </div>
          <div className="stack" style={{ gap: '.6rem' }}>
            <label htmlFor="edit-class-list-name">Class list name</label>
            <input
              id="edit-class-list-name"
              value={editorName}
              onChange={(event) => setEditorName(event.target.value)}
              placeholder="Optional"
              disabled={isUpdating}
            />
          </div>
          <div className="stack" style={{ gap: '.6rem' }}>
            <label htmlFor="edit-class-list-names">Student names</label>
            <textarea
              id="edit-class-list-names"
              value={editorNamesText}
              onChange={(event) => setEditorNamesText(event.target.value)}
              rows={Math.min(Math.max(activeClassList.entry_count, 8), 20)}
              disabled={isUpdating}
            />
          </div>
          <div className="actions-row" style={{ marginTop: 0 }}>
            <button type="button" className="btn btn-primary" onClick={() => void onSaveClassList()} disabled={isUpdating}>
              {isUpdating ? 'Saving…' : 'Save changes'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={closeEditor} disabled={isUpdating}>
              Close
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
