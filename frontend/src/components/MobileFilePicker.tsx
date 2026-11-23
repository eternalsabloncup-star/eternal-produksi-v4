/**
 * Styled mobile file trigger around native `<input type="file">`.
 * Does not change accept rules or upload handlers.
 */

import {
  useId,
  useState,
  type ChangeEvent,
  type Ref,
} from "react";

type Props = {
  inputRef?: Ref<HTMLInputElement>;
  accept?: string;
  multiple?: boolean;
  ariaLabel: string;
  disabled?: boolean;
  testId?: string;
  buttonLabel?: string;
  onFilesChange?: (files: FileList | null) => void;
};

export function MobileFilePicker({
  inputRef,
  accept,
  multiple,
  ariaLabel,
  disabled,
  testId,
  buttonLabel = "Pilih File",
  onFilesChange,
}: Props) {
  const autoId = useId();
  const inputId = `ep-ws-file-${autoId}`;
  const [names, setNames] = useState<string>("");

  function onChange(e: ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) {
      setNames("");
    } else if (files.length === 1) {
      setNames(files[0].name);
    } else {
      setNames(`${files.length} file dipilih`);
    }
    onFilesChange?.(files);
  }

  return (
    <div className="ep-ws-file-picker" data-testid={testId}>
      <input
        id={inputId}
        ref={inputRef}
        type="file"
        className="ep-ws-file-input"
        aria-label={ariaLabel}
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={onChange}
      />
      <label
        htmlFor={inputId}
        className={`ep-ws-file-trigger${disabled ? " is-disabled" : ""}`}
      >
        {buttonLabel}
      </label>
      <span className="ep-ws-file-name" title={names || undefined}>
        {names || "Belum ada file dipilih"}
      </span>
    </div>
  );
}
