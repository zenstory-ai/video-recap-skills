#!/usr/bin/env node
"use strict";

import {readFile, writeFile, rm} from "node:fs/promises";
import {spawn} from "node:child_process";
import {tmpdir} from "node:os";
import {join, dirname} from "node:path";
import {fileURLToPath} from "node:url";

const [, , requestPath, outputPath, browserPath] = process.argv;
if (!requestPath || !outputPath || !browserPath) throw new Error("request, output, and browser are required");
const request = JSON.parse(await readFile(requestPath, "utf8"));
const html = await readFile(join(dirname(fileURLToPath(import.meta.url)), "theme_foreground_dom.html"), "utf8");
const profile = join(tmpdir(), `theme-foreground-chrome-${process.pid}-${Date.now()}`);
let browser;
let socket;
let nextId = 0;
const pending = new Map();
const blockedRequests = [];

function timeout(promise, milliseconds, label) {
  let handle;
  const guard = new Promise((_, reject) => { handle = setTimeout(() => reject(new Error(`${label} timeout`)), milliseconds); });
  return Promise.race([promise, guard]).finally(() => clearTimeout(handle));
}

async function closeOwnedBrowser() {
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  if (browser && browser.exitCode === null) {
    browser.kill("SIGTERM");
    await timeout(new Promise(resolve => browser.once("exit", resolve)), 5000, "browser exit").catch(() => browser.kill("SIGKILL"));
    if (browser.exitCode === null) await new Promise(resolve => browser.once("exit", resolve));
  }
  await rm(profile, {recursive: true, force: true});
}
process.on("SIGTERM", async () => {
  await closeOwnedBrowser().catch(() => {});
  process.exit(143);
});

try {
  browser = spawn(browserPath, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
    {stdio: ["ignore", "ignore", "pipe"]});
  let stderr = "";
  const endpoint = timeout(new Promise((resolve, reject) => {
    browser.stderr.on("data", chunk => {
      stderr += chunk.toString();
      const found = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (found) resolve(found[1]);
    });
    browser.once("exit", code => reject(new Error(`Browser exited before CDP endpoint (${code}): ${stderr}`)));
  }), 15000, "browser startup");
  const browserWs = await endpoint;
  const port = new URL(browserWs).port;
  const protocol = await (await fetch(`http://127.0.0.1:${port}/json/protocol`)).json();
  const hasCommand = (domainName, commandName) => protocol.domains.some(
    domain => domain.domain === domainName && domain.commands?.some(command => command.name === commandName));
  for (const [domain, command] of [["DOM", "getDocument"], ["DOM", "querySelectorAll"],
                                    ["CSS", "getPlatformFontsForNode"]]) {
    if (!hasCommand(domain, command)) throw new Error(`Required CDP feature unavailable: ${domain}.${command}`);
  }
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = pages.find(item => item.type === "page" && item.url === "about:blank");
  if (!page) throw new Error("Owned about:blank page was not available");
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await timeout(new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, {once: true});
    socket.addEventListener("error", () => reject(new Error("CDP websocket failed")), {once: true});
  }), 10000, "CDP websocket");
  socket.addEventListener("message", async event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const waiter = pending.get(message.id);
      if (waiter) {
        pending.delete(message.id);
        if (message.error) waiter.reject(new Error(message.error.message));
        else waiter.resolve(message.result);
      }
      return;
    }
    if (message.method === "Fetch.requestPaused") {
      const url = message.params.request.url;
      const allowed = url === "about:blank" || url.startsWith("data:");
      if (!allowed) blockedRequests.push(url);
      send(allowed ? "Fetch.continueRequest" : "Fetch.failRequest",
           allowed ? {requestId: message.params.requestId} : {requestId: message.params.requestId, errorReason: "BlockedByClient"}).catch(() => {});
    }
  });
  socket.addEventListener("close", () => {
    for (const waiter of pending.values()) waiter.reject(new Error("CDP websocket closed"));
    pending.clear();
  });
  socket.addEventListener("error", () => {
    for (const waiter of pending.values()) waiter.reject(new Error("CDP websocket error"));
    pending.clear();
  });
  function send(method, params = {}) {
    const id = ++nextId;
    socket.send(JSON.stringify({id, method, params}));
    return timeout(new Promise((resolve, reject) => pending.set(id, {resolve, reject})), 20000, method);
  }
  await send("Page.enable");
  await send("Runtime.enable");
  await send("DOM.enable");
  await send("CSS.enable");
  await send("Network.enable");
  await send("Network.setCacheDisabled", {cacheDisabled: true});
  await send("Page.setBypassCSP", {enabled: false});
  await send("Fetch.enable", {patterns: [{urlPattern: "*", requestStage: "Request"}]});
  await send("Emulation.setDeviceMetricsOverride", {width: request.canvas.width, height: request.canvas.height,
    deviceScaleFactor: 1, mobile: false});
  await send("Emulation.setDefaultBackgroundColorOverride", {color: {r: 0, g: 0, b: 0, a: 0}});
  const tree = await send("Page.getFrameTree");
  await send("Page.setDocumentContent", {frameId: tree.frameTree.frame.id, html});
  const rendered = await send("Runtime.evaluate", {expression: `renderTheme(${JSON.stringify(request)})`,
    awaitPromise: true, returnByValue: true});
  if (rendered.exceptionDetails) throw new Error(rendered.exceptionDetails.exception?.description || "DOM render failed");
  if (blockedRequests.length) throw new Error(`Unexpected page request was blocked: ${blockedRequests.join(", ")}`);
  const documentNode = await send("DOM.getDocument", {depth: 0});
  const matched = await send("DOM.querySelectorAll", {nodeId: documentNode.root.nodeId, selector: "[data-font-id]"});
  const fontEvidence = [];
  for (const nodeId of matched.nodeIds) {
    const attributeList = (await send("DOM.getAttributes", {nodeId})).attributes;
    const attributes = Object.fromEntries(Array.from({length: attributeList.length / 2}, (_, index) =>
      [attributeList[index * 2], attributeList[index * 2 + 1]]));
    const expected = request.fonts.find(font => font.id === attributes["data-font-id"]);
    if (!expected) throw new Error(`Rendered node references unknown font: ${attributes["data-font-id"]}`);
    const rows = (await send("CSS.getPlatformFontsForNode", {nodeId})).fonts;
    const used = rows.filter(row => row.glyphCount > 0);
    if (!used.length || used.some(row => row.isCustomFont !== true
        || !expected.platform_families.includes(row.familyName)
        || !expected.postscript_names.includes(row.postScriptName))) {
      throw new Error(`Rendered glyphs did not use the declared custom face: ${attributes["data-role"]}/${attributes["data-id"]} expected=${JSON.stringify({platform_families: expected.platform_families, postscript_names: expected.postscript_names})} rows=${JSON.stringify(rows)}`);
    }
    fontEvidence.push({role: attributes["data-role"], id: attributes["data-id"], font_id: expected.id, rows});
  }
  rendered.result.value.platform_fonts = fontEvidence;
  const capture = await send("Page.captureScreenshot", {format: "png", fromSurface: true, captureBeyondViewport: false,
    clip: {x: 0, y: 0, width: request.canvas.width, height: request.canvas.height, scale: 1}});
  await writeFile(outputPath, Buffer.from(capture.data, "base64"), {flag: "wx"});
  process.stdout.write(JSON.stringify(rendered.result.value));
} finally {
  for (const waiter of pending.values()) waiter.reject(new Error("CDP connection closed"));
  pending.clear();
  await closeOwnedBrowser();
}
