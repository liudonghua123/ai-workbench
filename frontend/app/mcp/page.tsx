"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { CopyButton, Empty, Pill, Skeleton, StatusPill, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { MCP_STATUS, relativeTime } from "@/lib/format";
import type { McpServer } from "@/lib/types";

export default function McpPage() {
  const { push } = useToast();
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
          ? `${result.name} 探测成功 · ${result.tool_count} 个工具`
          : `${result.name} 不可达：${result.probe_error || "连接失败"}`,
        result.status === "healthy" ? "ok" : "err",
      );
    } catch (err) {
      push(err instanceof Error ? err.message : "探测失败", "err");
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
      push("MCP 服务已注册");
      load();
    } catch (err) {
      push(err instanceof Error ? err.message : "注册失败", "err");
    }
  };

  const remove = async (server: McpServer) => {
    if (!window.confirm(`删除 MCP 服务「${server.name}」？`)) return;
    try {
      await api.deleteMcp(server.id);
      push("已删除");
      load();
    } catch (err) {
      push(err instanceof Error ? err.message : "删除失败", "err");
    }
  };

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">MCP 服务</h1>
          <p className="page-desc">
            系统自身的完整 API 已通过 streamable HTTP 暴露在 <code className="mono">/mcp</code>
            ，Agent 与外部客户端使用同一套工具；这里还能注册并探测第三方 MCP。
          </p>
        </div>
        <div className="spacer" />
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          <Icon name="plus" size={13} /> 注册服务
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
          <Empty icon="plug" title="没有 MCP 服务" />
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
                    {server.is_system ? <Pill tone="accent">内置</Pill> : null}
                    <span className="tag">{server.transport}</span>
                  </div>
                  <div className="muted" style={{ fontSize: 11.5 }}>
                    {server.description}
                  </div>
                </div>
                <div className="spacer" />
                <StatusPill map={MCP_STATUS} value={server.status} />
                <button className="btn btn-sm" onClick={() => probe(server)} disabled={probing === server.id}>
                  <Icon name={probing === server.id ? "refresh" : "pulse"} size={12} />
                  {probing === server.id ? "探测中…" : "探测"}
                </button>
                {!server.is_system ? (
                  <button className="icon-btn" title="删除" onClick={() => remove(server)}>
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
                    <span className="muted" style={{ fontSize: 10.5, letterSpacing: "0.1em", textTransform: "uppercase" }}>
                      工具
                    </span>
                    <span className="nav-count">{server.tool_count}</span>
                    {server.last_checked_at ? (
                      <span className="muted" style={{ fontSize: 10.5, marginLeft: "auto" }}>
                        探测于 {relativeTime(server.last_checked_at)}
                      </span>
                    ) : null}
                  </div>
                  <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                    {server.tools.length === 0 ? (
                      <span className="muted" style={{ fontSize: 11.5 }}>
                        尚未同步工具列表，点击「探测」获取
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
            <div className="card-title">接入客户端</div>
            <pre className="terminal" style={{ marginTop: 8, padding: "10px 12px", fontSize: 11, maxHeight: 200 }}>
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
        <div className="overlay" onClick={(event) => event.target === event.currentTarget && setOpen(false)}>
          <div className="modal" role="dialog" aria-modal="true">
            <div className="modal-head">
              <Icon name="plug" size={15} />
              <span className="card-title">注册 MCP 服务</span>
              <div className="spacer" />
              <button className="icon-btn" onClick={() => setOpen(false)}>
                <Icon name="close" size={14} />
              </button>
            </div>
            <div className="modal-body stack" style={{ gap: 12 }}>
              <div className="grid grid-2" style={{ gap: 10 }}>
                <div className="field">
                  <label>名称</label>
                  <input className="input mono" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="docs-search" />
                </div>
                <div className="field">
                  <label>传输方式</label>
                  <select className="select" value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value })}>
                    <option value="streamable_http">streamable_http</option>
                    <option value="sse">sse</option>
                    <option value="stdio">stdio</option>
                  </select>
                </div>
              </div>
              <div className="field">
                <label>描述</label>
                <input className="input" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="一句话说明这个服务提供什么能力" />
              </div>
              {form.transport === "stdio" ? (
                <div className="field">
                  <label>启动命令</label>
                  <input className="input mono" value={form.command} onChange={(event) => setForm({ ...form, command: event.target.value })} placeholder="npx -y @modelcontextprotocol/server-filesystem" />
                </div>
              ) : (
                <div className="field">
                  <label>Endpoint</label>
                  <input className="input mono" value={form.endpoint} onChange={(event) => setForm({ ...form, endpoint: event.target.value })} placeholder="http://127.0.0.1:9100/mcp" />
                </div>
              )}
            </div>
            <div className="modal-foot">
              <button className="btn" onClick={() => setOpen(false)}>
                取消
              </button>
              <button className="btn btn-accent" onClick={create} disabled={!form.name.trim()}>
                注册
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}
