"use client";

import type { ReactNode } from "react";

interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
}

interface Props<T> {
  columns: Column<T>[];
  data: T[];
  keyField: keyof T;
}

export default function DataTable<T>({ columns, data, keyField }: Props<T>) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-on-surface-variant/60 uppercase tracking-wider">
            {columns.map((col) => (
              <th key={col.key} className={`pb-3 pr-4 font-medium ${col.className ?? ""}`}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr
              key={String(row[keyField])}
              className={`${
                i % 2 === 0 ? "bg-transparent" : "bg-surface-container-low/30"
              } hover:bg-surface-container-high/40 transition-colors`}
            >
              {columns.map((col) => (
                <td key={col.key} className={`py-2.5 pr-4 ${col.className ?? ""}`}>
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
