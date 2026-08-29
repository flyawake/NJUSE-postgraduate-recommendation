import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, TriangleAlert, X } from "lucide-react";
import { api } from "@/api/client";
import type { FileChange, FilePreview } from "@/api/client";
import { DiffViewer } from "./DiffViewer";
import { useI18n } from "@/lib/i18n";

export interface ArtifactPreviewPaneProps {
  conversationId: string;
  turnId: string;
  file?: FileChange | null;
  onClose?: () => void;
  onSelectFile?: (file: FileChange) => void;
  files?: FileChange[];
}

type PreviewMode = "diff" | "before" | "after" | "current";

function defaultMode(file: FileChange | null | undefined): PreviewMode {
  if (!file) return "diff";
  return "diff";
}

function previewErrorText(code: string | undefined, t: (key: string) => string): string {
  switch (code) {
    case "artifact_corrupt": return t("preview.error.corrupt");
    case "too_large":
    case "turn_budget_exceeded": return t("preview.error.tooLarge");
    case "current_preview_unavailable": return t("preview.error.current");
    case "capture_failed": return t("preview.error.capture");
    default: return t("preview.error.unavailable");
  }
}

/**
 * Right-hand artifact preview. It is mounted only after the user opens a
 * change; it never lists workspace paths or reads arbitrary filesystem paths.
 */
export function ArtifactPreviewPane({
  conversationId,
  turnId,
  file,
  onClose,
  onSelectFile,
  files = [],
}: ArtifactPreviewPaneProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<PreviewMode>(() => defaultMode(file));
  const fileId = file?.id;
  const fileChangeType = file?.change_type;
  useEffect(() => {
    setMode("diff");
  }, [fileId, fileChangeType]);
  const previewQuery = useQuery({
    queryKey: ["preview", conversationId, turnId, file?.id, mode],
    queryFn: () =>
      api.getFilePreview(conversationId, turnId, file?.id ?? "", mode),
    enabled: Boolean(file?.id),
    staleTime: 30_000,
  });
  const fullTextMode = file?.change_type === "deleted" ? "before" : "after";
  const fullTextQuery = useQuery({
    queryKey: ["preview", conversationId, turnId, file?.id, fullTextMode, "diff-context"],
    queryFn: () => api.getFilePreview(conversationId, turnId, file?.id ?? "", fullTextMode),
    enabled: Boolean(file?.id) && mode === "diff" && file?.change_type !== "created",
    staleTime: 30_000,
  });

  useEffect(() => {
    if (!file) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose?.();
      if (!event.altKey) return;
      const index = files.findIndex((item) => item.id === file.id);
      if (event.key === "ArrowLeft" && index > 0) onSelectFile?.(files[index - 1]);
      if (event.key === "ArrowRight" && index >= 0 && index < files.length - 1) onSelectFile?.(files[index + 1]);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [file, files, onClose, onSelectFile]);

  const orderedFiles = useMemo(() => files, [files]);
  const selectedIndex = file ? orderedFiles.findIndex((item) => item.id === file.id) : -1;

  return (
    <aside
      className="flex h-full min-h-0 w-full flex-col border-l border-border bg-surface"
      data-testid="artifact-preview-pane"
      aria-label={t("preview.title")}
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold" data-testid="preview-title">
            {file ? file.relative_path : t("preview.empty")}
          </h2>
          <p className="truncate text-xs text-muted">
            {file ? `${file.change_type} · +${file.additions}/-${file.deletions}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button type="button" className="btn-icon" disabled={selectedIndex <= 0} onClick={() => onSelectFile?.(orderedFiles[selectedIndex - 1])} aria-label={t("preview.previous")}>
            <ChevronLeft aria-hidden size={16} />
          </button>
          <button type="button" className="btn-icon" disabled={selectedIndex < 0 || selectedIndex >= orderedFiles.length - 1} onClick={() => onSelectFile?.(orderedFiles[selectedIndex + 1])} aria-label={t("preview.next")}>
            <ChevronRight aria-hidden size={16} />
          </button>
          {onClose ? (
            <button type="button" className="btn-icon" onClick={onClose} aria-label={t("common.close")}>
              <X aria-hidden size={16} />
            </button>
          ) : null}
        </div>
      </div>
      {file ? (
        <>
          <div className="flex flex-wrap gap-1 border-b border-border px-3 py-2" role="tablist" aria-label={t("preview.modeLabel")}>
            {(["diff", "before", "after", "current"] as const).map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={mode === item}
                className={mode === item ? "btn-secondary px-2 py-1 text-xs" : "btn-ghost px-2 py-1 text-xs"}
                onClick={() => setMode(item)}
              >
                {t(`preview.mode.${item}`)}
              </button>
            ))}
          </div>
          {previewQuery.data?.diverged ? (
            <div className="flex items-start gap-2 border-b border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning" role="status">
              <TriangleAlert aria-hidden size={15} className="mt-0.5 shrink-0" />
              <span>{t("preview.diverged")}</span>
            </div>
          ) : null}
          <div className="min-h-0 flex-1 overflow-auto p-2">
          {previewQuery.isLoading ? (
            <p className="p-3 text-xs text-faint">{t("common.loading")}</p>
          ) : previewQuery.isError || !previewQuery.data ? (
            <div className="p-3 text-xs text-danger">
              <p>{previewErrorText((previewQuery.data as FilePreview | undefined)?.error?.code, t)}</p>
              <button type="button" className="btn-secondary mt-2 text-xs" onClick={() => void previewQuery.refetch()}>{t("common.retry")}</button>
            </div>
          ) : previewQuery.data.binary ? (
            <p className="p-3 text-xs text-muted">{t("preview.binary")}</p>
          ) : previewQuery.data.error ? (
            <div className="p-3 text-xs text-danger">
              <p>{previewErrorText(previewQuery.data.error.code, t)}</p>
              <button type="button" className="btn-secondary mt-2 text-xs" onClick={() => void previewQuery.refetch()}>{t("common.retry")}</button>
            </div>
          ) : (
            <>
              {previewQuery.data.truncated ? <p className="mb-2 text-xs text-warning">{t("preview.truncated")}</p> : null}
              <DiffViewer
                lines={previewQuery.data.lines ?? []}
                mode={mode === "diff" ? "diff" : "text"}
                filePath={file.relative_path}
                fullLines={mode === "diff" ? fullTextQuery.data?.lines : undefined}
              />
            </>
          )}
          </div>
        </>
      ) : (
        <p className="p-4 text-xs text-faint">{t("preview.emptyHint")}</p>
      )}
      {orderedFiles.length > 1 ? (
        <div className="border-t border-border p-2">
          <div className="flex flex-wrap gap-1">
            {orderedFiles.map((item) => (
              <button
                key={item.id}
                type="button"
                className="btn-ghost max-w-[12rem] truncate px-2 py-1 text-xs"
                onClick={() => onSelectFile?.(item)}
                aria-pressed={item.id === file?.id}
              >
                {item.relative_path}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
