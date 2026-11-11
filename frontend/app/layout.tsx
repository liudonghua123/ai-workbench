import type { Metadata } from "next";
import "./globals.css";
import { Shell } from "@/components/Shell";
import { ToastProvider } from "@/components/ui";
import { I18nProvider } from "@/lib/i18n";
import { PrefsProvider, themeScript } from "@/lib/prefs";

export const metadata: Metadata = {
  title: "AI Workbench",
  description:
    "Visual control plane: scaffold projects, provision cloud storage, databases and domains, edit and deploy code, and manage MCP / Skills / Agents in one place.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <I18nProvider>
          <PrefsProvider>
            <ToastProvider>
              <Shell>{children}</Shell>
            </ToastProvider>
          </PrefsProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
