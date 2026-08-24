import { useRef } from "react";

// Kept in sync with document_extraction.py's _EXTENSION_KINDS (pdf/docx/md/txt route
// through platform-registry's OCR/text-extraction endpoint) plus the image types
// ChatPanel sends either straight through as a vision content block or, when the
// active model has no vision support, through that same extraction endpoint for OCR.
const ACCEPT = ".png,.jpg,.jpeg,.webp,.pdf,.docx,.md,.txt";

/**
 * The "+" attach button in ChatPanel's input row (mirrors MicButton.tsx's shape) —
 * opens a native, multi-select file picker and hands the chosen files to `onAttach`
 * in one call. ChatPanel.handleAttach caps the running total at MAX_ATTACHMENTS; the
 * actual decision of what to do with each file (vision content block vs. OCR/text
 * extraction) lives in ChatPanel.send(), not here — this component only surfaces
 * the files to attach.
 */
export function AttachButton({ onAttach, disabled }: { onAttach: (files: File[]) => void; disabled?: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <button
        type="button"
        className="chat-attach"
        title="Attach files (image, PDF, Word, Markdown, or text)"
        aria-label="Attach files"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        +
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="chat-attach-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) onAttach(files);
          e.target.value = ""; // allow re-attaching the same file(s) after clearing them
        }}
      />
    </>
  );
}
