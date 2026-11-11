"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { CopyButton, Empty, Pill, Skeleton, StatusPill, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { MCP_STATUS, relativeTime } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { McpServer } from "@/lib/types";

export default function McpPage() {
  const { push } = useToast();
  const { t, locale } = useI18n();
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [probing, setProbing] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: "",
    description: "",
    transport: "streamable_http",
    endpoint: "",
    command: "",
  });

  const load = () =>
    api
      .mcpServers()
      .then(setServers)
      .catch((err) => push(err.message, "err"));

  useEffect(() => {
    load();
  }, []);

  const probe = async (server: McpServer) => {
    setProbing(server.id);
    try {
      const result = await api.probeMcp(server.id);
      setServers((prev) => prev?.map((item) => (item.id === result.id ? result : item)) ?? null);
      push(
        result.status === "healthy"
          ? t("mcp.probeOk", { name: result.name, n: result.tool_count })
          : t("mcp.probeErr", { name: result.name, error: result.probe_error || t("common.failed") }),
        result.status === "healthy" ? "ok" : "err",
      );
    } catch (err) {
      push(err instanceof Error ? err.message : t("mcp.probeFailed"), "err");
    } finally {
      setProbing(null);
    }
  };

  const create = async () => {
    if (!form.name.trim()) return;
    try {
      await api.createMcp({
        name: form.name.trim(),
        description: form.description,
        transport: form.transport,
        endpoint: form.endpoint,
        command: form.command,
      });
      setOpen(false);
      setForm({ name: "", description: "", transport: "streamable_http", endpoint: "", command: "" });
      push(t("mcp.registered"));
      load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("mcp.registerFailed"), "err");
    }
  };

  const remove = async (server: McpServer) => {
    if (!window.confirm(t("mcp.deleteConfirm", { name: server.name }))) return;
    try {
      await api.deleteMcp(server.id);
      push(t("mcp.deleted"));
      load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("mcp.deleteFailed"), "err");
    }
  };

  const [descBefore, descAfter] = t("mcp.desc").split("{path}");

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("mcp.title")}</h1>
          <p className="page-desc">
            {descBefore}
            <code className="mono">/mcp</code>
            {descAfter}
          </p>
        </div>
        <div className="spacer" />
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          <Icon name="plus" size={13} /> {t("mcp.register")}
        </button>
      </div>

      {servers === null ? (
        <div className="stack">
          {Array.from({ length: 2 }).map((_, index) => (
            <Skeleton key={index} height={160} radius={13} />
          ))}
        </div>
      ) : servers.length === 0 ? (
        <div className="card">
          <Empty icon="plug" title={t("mcp.empty")} />
        </div>
      ) : (
        <div className="stack" style={{ gap: 14 }}>
          {servers.map((server) => (
            <section className="card" key={server.id}>
              <div className="card-head">
                <span className="project-glyph" style={{ width: 28, height: 28, borderRadius: 8 }}>
                  <Icon name="plug" size={14} />
                </span>
                <div style={{ minWidth: 0 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="card-title">{server.name}</span>
                    {server.is_system ? <Pill tone="accent">{t("common.builtin")}</Pill> : null}
                    <span className="tag">{server.transport}</span>
                  </div>
                  <div className="muted" style={{ fontSize: 11.5 }}>
                    {server.description}
                  </div>
                </div>
                <div className="spacer" />
                <StatusPill map={MCP_STATUS} value={server.status} />
                <button
                  className="btn btn-sm"
                  onClick={() => probe(server)}
                  disabled={probing === server.id}
                >
                  <Icon name={probing === server.id ? "refresh" : "pulse"} size={12} />
                  {probing === server.id ? t("mcp.probing") : t("mcp.probe")}
                </button>
                {!server.is_system ? (
                  <button
                    className="icon-btn"
                    title={t("common.delete")}
                    aria-label={t("common.delete")}
                    onClick={() => remove(server)}
                  >
                    <Icon name="trash" size={13} />
                  </button>
                ) : null}
              </div>

              <div className="card-body stack" style={{ gap: 12 }}>
                <div className="row" style={{ gap: 6 }}>
                  <span className="mono muted" style={{ fontSize: 11.5, wordBreak: "break-all" }}>
                    {server.endpoint || `${server.command} ${server.args.join(" ")}`}
                  </span>
                  {server.endpoint ? <CopyButton value={server.endpoint} /> : null}
                </div>

                <div>
                  <div className="row" style={{ gap: 6, marginBottom: 7 }}>
                    <span
                      className="muted"
                      style={{
                        fontSize: 10.5,
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                      }}
                    >
                      {t("common.tools")}
                    </span>
                    <span className="nav-count">{server.tool_count}</span>
                    {server.last_checked_at ? (
                      <span className="muted" style={{ fontSize: 10.5, marginLeft: "auto" }}>
                        {t("mcp.probedAt", {
                          time: relativeTime(server.last_checked_at, locale),
                        })}
                      </span>
                    ) : null}
                  </div>
                  <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                    {server.tools.length === 0 ? (
                      <span className="muted" style={{ fontSize: 11.5 }}>
                        {t("mcp.noTools")}
                      </span>
                    ) : (
                      server.tools.map((tool) => (
                        <span className="tag" key={tool.name} title={tool.description}>
                          {tool.name}
                        </span>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </section>
          ))}
        </div>
      )}

      <section className="card card-pad" style={{ marginTop: 16 }}>
        <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
          <span className="project-glyph" style={{ width: 30, height: 30 }}>
            <Icon name="code" size={14} />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="card-title">{t("mcp.clientTitle")}</div>
            <pre
              className="terminal"
              style={{ marginTop: 8, padding: "10px 12px", fontSize: 11, maxHeight: 200 }}
            >
              {`{
  "mcpServers": {
    "workbench": { "url": "http://127.0.0.1:8000/mcp" }
  }
}`}
            </pre>
          </div>
        </div>
      </section>

      {open ? (
        <div
          className="overlay"
          onClick={(event) => event.target === event.currentTarget && setOpen(false)}
        >
          <div className="modal" role="dialog" aria-modal="true">
            <div className="modal-head">
              <Icon name="plug" size={15} />
              <span className="card-title">{t("mcp.registerTitle")}</span>
              <div className="spacer" />
              <button
                className="icon-btn"
                onClick={() => setOpen(false)}
                aria-label={t("common.close")}
              >
                <Icon name="close" size={14} />
              </button>
            </div>
            <div className="modal-body stack" style={{ gap: 12 }}>
              <div className="grid grid-2" style={{ gap: 10 }}>
                <div className="field">
                  <label>{t("mcp.formName")}</label>
                  <input
                    className="input mono"
                    value={form.name}
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                    placeholder="docs-search"
                  />
                </div>
                <div className="field">
                  <label>{t("mcp.formTransport")}</label>
                  <select
                    className="select"
                    value={form.transport}
                    onChange={(event) => setForm({ ...form, transport: event.target.value })}
                  >
                    <option value="streamable_http">streamable_http</option>
                    <option value="sse">sse</option>
                    <option value="stdio">stdio</option>
                  </select>
                </div>
              </div>
              <div className="field">
                <label>{t("mcp.formDescription")}</label>
                <input
                  className="input"
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                  placeholder={t("mcp.formDescriptionPlaceholder")}
                />
              </div>
              {form.transport === "stdio" ? (
                <div className="field">
                  <label>{t("mcp.formCommand")}</label>
                  <input
                    className="input mono"
                    value={form.command}
                    onChange={(event) => setForm({ ...form, command: event.target.value })}
                    placeholder="npx -y @modelcontextprotocol/server-filesystem"
                  />
                </div>
              ) : (
                <div className="field">
                  <label>{t("mcp.formEndpoint")}</label>
                  <input
                    className="input mono"
                    value={form.endpoint}
                    onChange={(event) => setForm({ ...form, endpoint: event.target.value })}
                    placeholder="http://127.0.0.1:9100/mcp"
                  />
                </div>
              )}
            </div>
            <div className="modal-foot">
              <button className="btn" onClick={() => setOpen(false)}>
                {t("common.cancel")}
              </button>
              <button className="btn btn-accent" onClick={create} disabled={!form.name.trim()}>
                {t("mcp.register")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
