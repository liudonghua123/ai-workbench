"use client";

/**
 * Dependency-free code editor.
 *
 * A transparent <textarea> sits on top of a highlighted <pre>. Same font metrics,
 * same padding, same `white-space: pre` — so the caret lands on the glyph you see.
 * The textarea owns scrolling and input; the <pre> and the gutter are scroll-synced
 * to it, which is why selection, IME and undo all behave natively.
 *
 * Highlighting is a tiny sticky-regex scanner per language. It is not a parser and
 * does not try to be: it colours comments, strings, numbers, keywords and calls, which
 * is 90% of the readability win for a 300-line project file.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Lang = "js" | "py" | "json" | "css" | "html" | "md" | "sh" | "text";

const EXT_LANG: Record<string, Lang> = {
  js: "js", jsx: "js", ts: "js", tsx: "js", mjs: "js", cjs: "js", vue: "js", svelte: "js",
  py: "py", pyi: "py",
  json: "json", jsonc: "json",
  css: "css", scss: "css", less: "css",
  html: "html", htm: "html", xml: "html", svg: "html",
  md: "md", mdx: "md", markdown: "md",
  sh: "sh", bash: "sh", zsh: "sh", yml: "sh", yaml: "sh", toml: "sh", env: "sh",
};

const JS_KEYWORDS = new Set([
  "import", "from", "export", "default", "const", "let", "var", "function", "return",
  "if", "else", "for", "while", "do", "switch", "case", "break", "continue", "new",
  "class", "extends", "super", "this", "typeof", "instanceof", "in", "of", "await",
  "async", "try", "catch", "finally", "throw", "interface", "type", "enum", "implements",
  "public", "private", "protected", "readonly", "as", "satisfies", "null", "undefined",
  "true", "false", "void", "never", "any", "string", "number", "boolean",
]);

const PY_KEYWORDS = new Set([
  "import", "from", "as", "def", "class", "return", "if", "elif", "else", "for", "while",
  "break", "continue", "with", "try", "except", "finally", "raise", "yield", "lambda",
  "pass", "global", "nonlocal", "assert", "del", "in", "is", "not", "and", "or", "None",
  "True", "False", "async", "await", "self", "match", "case",
]);

type Rule = { re: RegExp; classes: (string | null)[]; keywords?: Set<string> };

function rule(re: RegExp, classes: (string | null)[], keywords?: Set<string>): Rule {
  return { re, classes, keywords };
}

const RULES: Record<Lang, Rule | null> = {
  js: rule(
    /(\s+)|(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b0[xX][0-9a-fA-F]+\b|\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)|([^\s\w])/y,
    [null, "tok-c", "tok-s", "tok-n", "kw", "tok-p"],
    JS_KEYWORDS,
  ),
  py: rule(
    /(\s+)|(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)|([^\s\w])/y,
    [null, "tok-c", "tok-s", "tok-n", "kw", "tok-p"],
    PY_KEYWORDS,
  ),
  json: rule(
    /(\s+)|("(?:[^"\\\n]|\\.)*")|(-?\b\d+(?:\.\d+)?\b)|(\btrue\b|\bfalse\b|\bnull\b)|([^\s\w])/y,
    [null, "tok-s", "tok-n", "tok-k", "tok-p"],
  ),
  css: rule(
    /(\s+)|(\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(#[0-9a-fA-F]{3,8}\b|\b\d+(?:\.\d+)?(?:px|rem|em|%|vh|vw|s|ms)?\b)|([A-Za-z-][\w-]*(?=\s*:))|([^\s\w])/y,
    [null, "tok-c", "tok-s", "tok-n", "tok-k", "tok-p"],
  ),
  html: rule(
    /(\s+)|(<!--[\s\S]*?-->)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(<\/?[A-Za-z][\w-]*|\/?>)|([A-Za-z-][\w-]*(?==))|([^\s\w])/y,
    [null, "tok-c", "tok-s", "tok-k", "tok-t", "tok-p"],
  ),
  md: rule(
    /(\s+)|(#{1,6}[^\n]*)|(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|([^\s\w`*])/y,
    [null, "tok-k", "tok-s", "tok-t", "tok-p"],
  ),
  sh: rule(
    /(\s+)|(#[^\n]*)|("(?:[^"\\\n]|\\.)*"|'[^'\n]*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][\w-]*)|([^\s\w])/y,
    [null, "tok-c", "tok-s", "tok-n", "tok-k", "tok-p"],
  ),
  text: null,
};

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };

function esc(text: string): string {
  return text.replace(/[&<>]/g, (ch) => ESCAPES[ch]);
}

const MAX_HIGHLIGHT = 160_000;

function highlight(code: string, lang: Lang): string {
  const spec = RULES[lang];
  if (!spec || code.length > MAX_HIGHLIGHT) return esc(code);

  const { re, classes, keywords } = spec;
  const parts: string[] = [];
  let index = 0;

  while (index < code.length) {
    re.lastIndex = index;
    const match = re.exec(code);
    if (!match) {
      parts.push(esc(code.slice(index)));
      break;
    }
    const text = match[0];
    if (!text) {
      parts.push(esc(code[index]));
      index += 1;
      continue;
    }

    let cls: string | null = null;
    for (let group = 1; group < classes.length; group += 1) {
      if (match[group] !== undefined) {
        cls = classes[group];
        if (cls === "kw") {
          if (keywords?.has(text)) {
            cls = "tok-k";
          } else if (code[re.lastIndex] === "(") {
            cls = "tok-f";
          } else if (/^[A-Z]/.test(text)) {
            cls = "tok-t";
          } else {
            cls = null;
          }
        }
        break;
      }
    }
    parts.push(cls ? `<span class="${cls}">${esc(text)}</span>` : esc(text));
    index = re.lastIndex;
  }
  // trailing newline would collapse the last visual line
  return parts.join("") + "\n";
}

export function languageOf(path: string): Lang {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANG[ext] ?? "text";
}

export interface CodeEditorProps {
  value: string;
  path: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  readOnly?: boolean;
}

export function CodeEditor({ value, path, onChange, onSave, readOnly }: CodeEditorProps) {
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLPreElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const [cursor, setCursor] = useState({ line: 1, column: 1 });

  const lang = useMemo(() => languageOf(path), [path]);
  const html = useMemo(() => highlight(value, lang), [value, lang]);
  const lineCount = useMemo(() => value.split("\n").length, [value]);

  const syncScroll = useCallback(() => {
    const area = areaRef.current;
    if (!area) return;
    if (preRef.current) {
      preRef.current.scrollTop = area.scrollTop;
      preRef.current.scrollLeft = area.scrollLeft;
    }
    if (gutterRef.current) gutterRef.current.scrollTop = area.scrollTop;
  }, []);

  const reportCursor = useCallback(() => {
    const area = areaRef.current;
    if (!area) return;
    const upto = value.slice(0, area.selectionStart);
    const lines = upto.split("\n");
    setCursor({ line: lines.length, column: (lines[lines.length - 1]?.length ?? 0) + 1 });
  }, [value]);

  useEffect(() => {
    syncScroll();
    reportCursor();
  }, [value, syncScroll, reportCursor]);

  const replace = (next: string, caret: number) => {
    onChange(next);
    requestAnimationFrame(() => {
      const area = areaRef.current;
      if (!area) return;
      area.selectionStart = caret;
      area.selectionEnd = caret;
    });
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const area = event.currentTarget;
    const start = area.selectionStart;
    const end = area.selectionEnd;

    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      onSave?.();
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      const head = value.slice(0, start);
      const tail = value.slice(end);
      if (event.shiftKey) {
        const lineStart = head.lastIndexOf("\n") + 1;
        const prefix = value.slice(lineStart, start);
        const strip = prefix.endsWith("  ") ? 2 : prefix.endsWith(" ") ? 1 : 0;
        if (strip > 0) {
          replace(value.slice(0, start - strip) + tail, start - strip);
        }
        return;
      }
      replace(`${head}  ${tail}`, start + 2);
      return;
    }
    if (event.key === "Enter") {
      const lineStart = value.lastIndexOf("\n", start - 1) + 1;
      const line = value.slice(lineStart, start);
      const indent = (line.match(/^\s*/) ?? [""])[0];
      const opens = /[{([]\s*$/.test(line) || /:\s*$/.test(line);
      const insert = `\n${indent}${opens ? "  " : ""}`;
      event.preventDefault();
      replace(value.slice(0, start) + insert + value.slice(end), start + insert.length);
    }
  };

  return (
    <div className="ce">
      <div className="ce-gutter" ref={gutterRef} aria-hidden>
        {Array.from({ length: lineCount }).map((_, i) => (
          <div key={i} className={i + 1 === cursor.line ? "on" : undefined}>
            {i + 1}
          </div>
        ))}
      </div>
      <div className="ce-scroll">
        <pre className="ce-pre" ref={preRef} aria-hidden>
          <code dangerouslySetInnerHTML={{ __html: html }} />
        </pre>
        <textarea
          ref={areaRef}
          className="ce-input"
          value={value}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          wrap="off"
          readOnly={readOnly}
          onChange={(event) => onChange(event.target.value)}
          onScroll={syncScroll}
          onKeyDown={onKeyDown}
          onKeyUp={reportCursor}
          onClick={reportCursor}
        />
      </div>
      <div className="ce-status">
        <span className="mono">{lang === "text" ? "text" : lang}</span>
        <span>
          {cursor.line}:{cursor.column}
        </span>
        <span>{lineCount} lines</span>
        <div className="spacer" />
        <span>{value.length.toLocaleString()} chars</span>
      </div>
    </div>
  );
}
