import { getCsrfToken } from "../../api";

export type TablePrefsDto = {
  table_id: string;
  version: number;
  column_order: string[];
  column_widths: Record<string, number>;
  hidden_columns: string[];
};

async function parseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: string | unknown };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // ignore
  }
  return "Gagal menyimpan/memuat preferensi tabel.";
}

export async function fetchTablePreferences(
  tableId: string,
): Promise<TablePrefsDto> {
  const response = await fetch(
    `/api/me/table-preferences/${encodeURIComponent(tableId)}`,
    { method: "GET", credentials: "include" },
  );
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as TablePrefsDto;
}

export async function saveTablePreferences(
  tableId: string,
  body: Omit<TablePrefsDto, "table_id">,
): Promise<TablePrefsDto> {
  const csrf = getCsrfToken();
  const response = await fetch(
    `/api/me/table-preferences/${encodeURIComponent(tableId)}`,
    {
      method: "PUT",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as TablePrefsDto;
}

export async function deleteTablePreferences(tableId: string): Promise<void> {
  const csrf = getCsrfToken();
  const response = await fetch(
    `/api/me/table-preferences/${encodeURIComponent(tableId)}`,
    {
      method: "DELETE",
      credentials: "include",
      headers: csrf ? { "X-CSRF-Token": csrf } : {},
    },
  );
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
}
