export const percent = (n: number) => `${Math.round(n * 100)}%`;
export const dateTime = (ts: string) =>
  new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
export function download(name: string, data: unknown, format = "json") {
  const text = format === "json" ? JSON.stringify(data, null, 2) : String(data);
  const blob = new Blob([text], {
    type: format === "html" ? "text/html" : format === "csv" ? "text/csv" : "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const csv = (rows: unknown[][]) =>
  rows.map((r) => r.map((v) => `"${String(v ?? "").replaceAll('"', '""')}"`).join(",")).join("\n");
