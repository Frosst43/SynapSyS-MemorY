// A deterministic, local SDK fixture. It never contacts a model or a network endpoint.
import { createHash } from "node:crypto"
import { readFileSync, writeFileSync } from "node:fs"

const fixture = JSON.parse(readFileSync("/workspace/symem-probe.json", "utf8"))
const usage = {
  inputTokens: { total: 0, noCache: 0, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 0, text: 0, reasoning: 0 },
}
const finish = (reason) => ({ type: "finish", finishReason: { unified: reason, raw: reason }, usage })
const text = (value) => [
  { type: "text-start", id: "fixture" },
  { type: "text-delta", id: "fixture", delta: value },
  { type: "text-end", id: "fixture" },
]
const require = (condition, message) => {
  if (!condition) throw new Error(message)
}
function valueOf(result) {
  require(result.output.type === "json" || result.output.type === "text", "Unexpected tool result type")
  const value = result.output.type === "json" ? result.output.value : JSON.parse(result.output.value)
  return value && typeof value === "object" && Object.hasOwn(value, "result") ? value.result : value
}
function call(id, toolName, memoryID) {
  const input = JSON.stringify({ id: memoryID })
  return [
    { type: "tool-input-start", id, toolName },
    { type: "tool-input-delta", id, delta: input },
    { type: "tool-input-end", id },
    { type: "tool-call", toolCallId: id, toolName, input },
    finish("tool-calls"),
  ]
}

let requests = 0
function parts(options) {
  const tool = options.tools?.find((entry) => entry.name === "synapsys-memory_memory_get")
  if (!tool) return [...text("Deterministic fixture"), finish("stop")]
  requests++
  require(requests <= 4, "Unexpected repeated provider requests")
  const tools = options.tools
    .filter((entry) => entry.name.startsWith("synapsys-memory_"))
    .map((entry) => entry.name.slice("synapsys-memory_".length)).sort()
  require(!tools.some((name) => ["memory_remember", "memory_update", "memory_forget"].includes(name)),
    "Write tools exposed to the simulated provider")
  const results = options.prompt.flatMap((message) =>
    Array.isArray(message.content) ? message.content.filter((part) => part.type === "tool-result") : [])
  const own = results.find((result) => result.toolCallId === "symem-own")
  if (!own) return call("symem-own", tool.name, fixture.own.id)
  const memory = valueOf(own)
  require(memory?.id === fixture.own.id && memory.space === fixture.space && memory.project === fixture.project &&
    memory.revision === fixture.own.revision && typeof memory.body === "string" &&
    createHash("sha256").update(memory.body).digest("hex") === fixture.own.sha256, "Own memory did not match")
  const foreign = results.find((result) => result.toolCallId === "symem-foreign")
  if (!foreign) return call("symem-foreign", tool.name, fixture.foreign)
  require(valueOf(foreign) === null, "Other project's memory was returned")
  require(requests === 3, "Unexpected number of provider steps")
  writeFileSync("/workspace/symem-result.json", JSON.stringify({
    own_memory_matches: true, foreign_memory_null: true, read_only_tools: tools,
    service_pid: process.pid, provider_requests: requests,
  }), { flag: "wx", mode: 0o600 })
  return [...text("SYMEM_PROVIDER_PASS"), finish("stop")]
}

const language = {
  specificationVersion: "v3",
  provider: "symem-fixture",
  modelId: "scripted",
  supportedUrls: {},
  async doGenerate() {
    return {
      content: [{ type: "text", text: "Deterministic fixture" }],
      finishReason: { unified: "stop", raw: "stop" }, usage, warnings: [],
    }
  },
  async doStream(options) {
    const events = [{ type: "stream-start", warnings: [] }, ...parts(options)]
    return { stream: new ReadableStream({
      start(controller) {
        for (const event of events) controller.enqueue(event)
        controller.close()
      },
    }) }
  },
}

export default {
  id: "synapsys.simulated-provider",
  async setup(context) {
    await context.aisdk.hook("sdk", (event) => {
      event.sdk = { languageModel: () => language }
    }, { providerID: "symem-fixture" })
    writeFileSync("/workspace/provider-ready.json", JSON.stringify({ ready: true }), { mode: 0o600 })
  },
}
