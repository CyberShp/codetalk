"use client";

import { useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Download,
  FileText,
  FileType,
  FileCode,
  Check,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ExportFormat } from "@/lib/types";

interface FormatOption {
  id: ExportFormat;
  label: string;
  desc: string;
  icon: typeof FileText;
}

const FORMATS: FormatOption[] = [
  {
    id: "md",
    label: "Markdown",
    desc: "适用于 GitHub / 知识库",
    icon: FileText,
  },
  {
    id: "docx",
    label: "Word 文档",
    desc: "适用于正式报告提交",
    icon: FileType,
  },
  {
    id: "xml",
    label: "XML",
    desc: "适用于系统集成 / 数据交换",
    icon: FileCode,
  },
];

export default function ExportPage() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;
  const [selectedFormat, setSelectedFormat] = useState<ExportFormat>("md");
  const [downloading, setDownloading] = useState(false);
  const downloadingRef = useRef(false);

  const handleDownload = () => {
    if (downloadingRef.current) return;
    downloadingRef.current = true;
    setDownloading(true);
    const url = api.tasks.exportUrl(taskId, selectedFormat);
    window.open(url, "_blank");
    window.setTimeout(() => {
      downloadingRef.current = false;
      setDownloading(false);
    }, 1000);
  };

  return (
    <div className="max-w-2xl">
      <div className="flex items-center gap-3 mb-6">
        <Link
          href={`/tasks/${taskId}`}
          className="p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors"
        >
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">
            导出结果
          </h1>
          <p className="text-sm text-on-surface-variant mt-0.5">
            选择导出格式并下载分析报告
          </p>
        </div>
      </div>

      <div className="space-y-3 mb-8">
        <label className="block text-sm font-medium text-on-surface mb-2">
          选择导出格式
        </label>
        {FORMATS.map((fmt) => {
          const Icon = fmt.icon;
          const isSelected = selectedFormat === fmt.id;
          return (
            <button
              key={fmt.id}
              type="button"
              onClick={() => setSelectedFormat(fmt.id)}
              className={`w-full flex items-center gap-4 px-5 py-4 rounded-xl border text-left transition-colors ${
                isSelected
                  ? "bg-primary/10 border-primary/30"
                  : "bg-surface-container border-outline-variant/20 hover:border-outline-variant/40"
              }`}
            >
              <Icon
                size={20}
                className={isSelected ? "text-primary" : "text-on-surface-variant"}
              />
              <div className="flex-1">
                <p
                  className={`text-sm font-medium ${
                    isSelected ? "text-primary" : "text-on-surface"
                  }`}
                >
                  {fmt.label}
                </p>
                <p className="text-xs text-on-surface-variant mt-0.5">
                  {fmt.desc}
                </p>
              </div>
              {isSelected && (
                <Check size={18} className="text-primary" />
              )}
            </button>
          );
        })}
      </div>

      <button
        onClick={handleDownload}
        disabled={downloading}
        className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary text-on-primary font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50"
      >
        <Download size={16} />
        下载 {FORMATS.find((f) => f.id === selectedFormat)?.label} 文件
      </button>
    </div>
  );
}
