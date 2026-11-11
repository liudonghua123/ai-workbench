import type { Metadata } from "next";
import "./globals.css";
import { Rail } from "@/components/Rail";
import { Topbar } from "@/components/Topbar";
import { ToastProvider } from "@/components/ui";
import { themeScript } from "@/components/ThemeToggle";

export const metadata: Metadata = {
  title: "AI Workbench · 可视化应用工作台",
  description:
    "可视化创建项目、自动装配云资源与域名、在线编辑调试部署，并统一管理 MCP / Skills / Agents。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <ToastProvider>
          <div className="shell">
            <Rail />
            <div className="main">
              <Topbar />
              {children}
            </div>
          </div>
        </ToastProvider>
      </body>
    </html>
  );
}
