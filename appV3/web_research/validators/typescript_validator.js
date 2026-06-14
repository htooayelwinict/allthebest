#!/usr/bin/env node
"use strict";

const { parse } = require("@typescript-eslint/typescript-estree");

const BANNED_MODULES = new Set([
  "child_process",
  "cluster",
  "dgram",
  "dns",
  "fs",
  "fs/promises",
  "http",
  "http2",
  "https",
  "net",
  "os",
  "perf_hooks",
  "readline",
  "repl",
  "stream",
  "tls",
  "tty",
  "v8",
  "vm",
  "worker_threads",
  "zlib",
]);

const ALLOWED_MODULES = new Set(["@playwright/test"]);

function walk(node, visitor) {
  if (!node || typeof node !== "object") return;
  visitor(node);
  for (const key of Object.keys(node)) {
    const child = node[key];
    if (Array.isArray(child)) {
      for (const item of child) {
        if (item && typeof item.type === "string") walk(item, visitor);
      }
    } else if (child && typeof child.type === "string") {
      walk(child, visitor);
    }
  }
}

function snippet(source, loc) {
  if (!loc || !source) return "";
  const lines = source.split("\n");
  const line = lines[(loc.start.line || 1) - 1] || "";
  return line.trim().substring(0, 160);
}

function moduleName(value) {
  if (!value) return "";
  return String(value).startsWith("node:") ? String(value).slice(5) : String(value);
}

function violation(rule, node, source, detail) {
  return {
    rule,
    line: node && node.loc ? node.loc.start.line : 0,
    snippet: node && node.loc ? snippet(source, node.loc) : "",
    detail,
  };
}

function checkImportDeclaration(node, source) {
  const mod = node.source && node.source.value;
  if (!mod) return null;
  const normalized = moduleName(mod);
  if (BANNED_MODULES.has(normalized)) {
    return violation("banned_module_import", node, source, `import from '${mod}' is not allowed`);
  }
  if (!ALLOWED_MODULES.has(String(mod))) {
    return violation("disallowed_import", node, source, `only @playwright/test imports are allowed`);
  }
  return null;
}

function checkCallExpression(node, source) {
  const callee = node.callee;
  if (!callee) return null;

  if (callee.type === "Identifier" && callee.name === "require") {
    return violation("banned_require", node, source, "require() is not allowed");
  }
  if (callee.type === "Identifier" && callee.name === "eval") {
    return violation("banned_eval", node, source, "eval() is not allowed");
  }
  if (
    callee.type === "MemberExpression" &&
    callee.object &&
    callee.object.type === "Identifier" &&
    callee.object.name === "process" &&
    callee.property &&
    callee.property.type === "Identifier" &&
    callee.property.name === "exit"
  ) {
    return violation("banned_process_exit", node, source, "process.exit() is not allowed");
  }
  return null;
}

function checkNewExpression(node, source) {
  if (node.callee && node.callee.type === "Identifier" && node.callee.name === "Function") {
    return violation("banned_new_function", node, source, "new Function() is not allowed");
  }
  return null;
}

function checkImportExpression(node, source) {
  return violation("banned_dynamic_import", node, source, "dynamic import() is not allowed");
}

function checkMemberExpression(node, source) {
  if (
    node.object &&
    node.object.type === "Identifier" &&
    node.object.name === "process" &&
    node.property &&
    ((node.property.type === "Identifier" && node.property.name === "env") ||
      (node.computed && node.property.type === "Literal" && node.property.value === "env"))
  ) {
    return violation("banned_process_env", node, source, "process.env access is not allowed");
  }
  if (node.object && node.object.type === "Identifier" && node.object.name === "globalThis" && node.computed) {
    return violation("banned_globalthis_computed", node, source, "globalThis[expr] is not allowed");
  }
  return null;
}

function validateSource(source) {
  let ast;
  try {
    ast = parse(source, {
      jsx: false,
      range: false,
      loc: true,
      tokens: false,
      comment: false,
      errorOnUnknownASTType: false,
    });
  } catch (err) {
    return {
      ok: false,
      parse_error: true,
      violations: [
        {
          rule: "parse_error",
          line: err.lineNumber || 0,
          snippet: "",
          detail: String(err.message || err),
        },
      ],
    };
  }

  const violations = [];
  walk(ast, (node) => {
    let result = null;
    switch (node.type) {
      case "ImportDeclaration":
        result = checkImportDeclaration(node, source);
        break;
      case "CallExpression":
        result = checkCallExpression(node, source);
        break;
      case "NewExpression":
        result = checkNewExpression(node, source);
        break;
      case "ImportExpression":
        result = checkImportExpression(node, source);
        break;
      case "MemberExpression":
        result = checkMemberExpression(node, source);
        break;
    }
    if (result) violations.push(result);
  });

  return { ok: violations.length === 0, parse_error: false, violations };
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  try {
    const result = validateSource(input);
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(result.ok ? 0 : 1);
  } catch (err) {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        parse_error: true,
        violations: [{ rule: "internal_error", line: 0, snippet: "", detail: String(err) }],
      }) + "\n",
    );
    process.exit(2);
  }
});
