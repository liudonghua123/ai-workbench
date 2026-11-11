"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { RailNav } from "./NavLinks";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export function Rail() {
  const { t } = useI18n();
  const [suffix, setSuffix] = useState("localtest.me");
  const [mcpPath, setMcpPath] = useState("/mcp");

  useEffect(() => {
    let alive = true;
    api
      .overview()
      .then((data) => {
        if (!alive) return;
        setSuffix(data.domains.suffix);
        setMcpPath(data.mcp_endpoint);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  return (
    <aside className="rail">
      <Link href="/" className="brand">
        <span className="brand-mark">
          <Icon name="route" size={16} />
        </span>
        <span className="brand-text">
          <span className="brand-name">AI Workbench</span>
          <span className="brand-sub">{t("nav.brandSub")}</span>
        </span>
      </Link>

      <RailNav />

      <div className="rail-foot">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>{t("nav.convention")}</span>
          <b>&lt;slug&gt;.{suffix}</b>
        </div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>MCP</span>
          <b>{mcpPath}</b>
        </div>
      </div>
    </aside>
  );
}
